"""GTK-GUI-IPC-client.

A background thread polls the daemon for live data and marshals results into the
GTK main loop via GLib.idle_add, so the UI never blocks on IPC and a slow or
restarting daemon cannot freeze the window. The client reconnects automatically.

Public API:
    connect(timeout)            - initial (synchronous) connect
    start_polling(interval)     - start the background poller (+ reconnect)
    call(method, params, ...)   - synchronous RPC (reads/short calls)
    call_async(method, ...)     - RPC on a worker thread; never blocks the UI
    shutdown()                  - stop polling and close the socket
"""

import queue
import threading

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from .. import SOCKET_PATH
from ..protocol import Client


class GuiClient:
    def __init__(self, on_state=None, on_error=None, on_connected=None,
                 socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self.on_state = on_state
        self.on_error = on_error
        self.on_connected = on_connected
        self._client = None
        self._client_lock = threading.Lock()
        self._poll_thread = None
        self._stop = threading.Event()
        self._interval = 1.0
        self.connected = False
        self.state = {"processes": [], "enabled": True, "rules": [], "interface": "?"}
        self.last_error = None
        # Mutierende RPCs laufen in einem eigenen Worker, damit ein langsamer
        # Engine-Neustart (~2 s) den GTK-Mainloop nicht einfriert.
        self._jobs = queue.Queue()
        self._worker = None
        self._worker_stop = threading.Event()

    # --- Connection ------------------------------------------------------

    def _open(self, timeout: float) -> None:
        cl = Client(self.socket_path, connect_timeout=timeout)
        cl.connect()
        with self._client_lock:
            self._client = cl
        self.connected = True
        self.last_error = None

    def connect(self, timeout: float = 2.0) -> None:
        """Initial connect; raises ConnectionError (and reports it) on failure."""
        try:
            self._open(timeout)
        except ConnectionError as error:
            self.connected = False
            self.last_error = str(error)
            self._notify_error(
                "Throtl daemon is not reachable.\n"
                "  sudo systemctl start throtl\n"
                f"({error})"
            )
            raise

    def _close_client(self) -> None:
        with self._client_lock:
            cl = self._client
            self._client = None
        if cl is not None:
            try:
                cl.close()
            except Exception:
                pass

    def _notify_error(self, message: str) -> None:
        if self.on_error is not None:
            GLib.idle_add(self.on_error, message)

    # --- Background polling ---------------------------------------------

    def start_polling(self, interval: float = 1.0) -> None:
        self._interval = max(0.2, float(interval))
        self._stop.clear()
        if self._poll_thread is None or not self._poll_thread.is_alive():
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name="throtl-gui-poll", daemon=True
            )
            self._poll_thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self.connected:
                try:
                    self._open(self._interval)
                    if self.on_connected is not None:
                        GLib.idle_add(self.on_connected)
                except Exception:
                    # Daemon (noch) nicht da: still weiter versuchen.
                    self._stop.wait(self._interval)
                    continue
            try:
                with self._client_lock:
                    client = self._client
                if client is None:
                    self.connected = False
                    continue
                state = client.call("list_processes", timeout=3.0)
            except Exception as error:
                self.connected = False
                self.last_error = str(error)
                self._close_client()
                self._notify_error(f"Lost connection to daemon ({error}); reconnecting…")
                self._stop.wait(self._interval)
                continue
            self.state = state
            if self.on_state is not None:
                GLib.idle_add(self.on_state, state)
            self._stop.wait(self._interval)

    # --- RPC for user actions -------------------------------------------

    def _ensure_connected(self) -> None:
        if self.connected:
            return
        # Einmal sofort versuchen (z. B. daemon gerade neu gestartet).
        try:
            self._open(1.0)
            if self.on_connected is not None:
                GLib.idle_add(self.on_connected)
        except Exception as error:
            raise ConnectionError(
                "Not connected to the Throtl daemon. Start it with "
                "`sudo systemctl start throtl`."
            ) from error

    def call(self, method: str, params=None, timeout: float = 5.0):
        self._ensure_connected()
        with self._client_lock:
            client = self._client
        if client is None:
            raise ConnectionError("Not connected to the Throtl daemon.")
        result = client.call(method, params or {}, timeout=timeout)
        # Nach Aenderungen den Zustand neu holen, damit `self.state` aktuell ist.
        if method != "list_processes":
            try:
                self.state = client.call("list_processes", timeout=timeout)
            except Exception:
                pass
        return result

    # --- Convenience-Wrapper (Profile + Statistik) -----------------------

    def list_profiles(self):
        return self.call("list_profiles")

    def activate_profile(self, name: str):
        return self.call("activate_profile", {"name": name})

    def save_profile(self, name: str, activate: bool = True):
        return self.call("set_profile", {"name": name, "activate": activate})

    def delete_profile(self, name: str):
        return self.call("delete_profile", {"name": name})

    def set_schedule(self, rules):
        return self.call("set_schedule", {"rules": rules})

    def get_stats(self, window: str = "minute"):
        return self.call("get_stats", {"window": window})

    # --- Async mutations -------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker_stop.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="throtl-gui-rpc", daemon=True
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            method, params, on_done, on_error = job
            try:
                result = self.call(method, params)
            except Exception as error:
                callback = on_error or self.on_error
                if callback is not None:
                    GLib.idle_add(callback, str(error))
            else:
                if on_done is not None:
                    GLib.idle_add(on_done, result)

    def call_async(self, method: str, params=None, on_done=None, on_error=None) -> None:
        """Fire a mutating RPC on the worker thread (non-blocking for the UI).

        ``on_done(result)`` / ``on_error(message)`` are invoked in the GTK main
        loop. If ``on_error`` is omitted, ``self.on_error`` is used.
        """
        self._ensure_worker()
        self._jobs.put((method, params or {}, on_done, on_error))

    # --- Lifecycle -------------------------------------------------------

    def shutdown(self) -> None:
        self._stop.set()
        self._worker_stop.set()
        self._jobs.put(None)
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        self._close_client()
        self.connected = False
