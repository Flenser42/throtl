"""GTK-GUI-IPC-Client: verbindet sich mit dem Daemon und pollt Live-Daten.

Der Client verbindet sich synchron beim Aufruf von ``connect()`` (im
GUI-Mainloop-Thread, mit kurzem Timeout). Danach pollt er ueber einen
GLib-Timer die Live-Daten (list_processes). Die Ergebnis-/Fehler-Callbacks
werden via GLib.idle_add in den Mainloop geroutet, damit sie GTK-sicher sind.
"""

from gi.repository import GLib

from .. import SOCKET_PATH
from ..protocol import Client


class GuiClient:
    """Kapselt eine Verbindung zum Daemon mit Poll-Loop.

    - ``connect(timeout)``: oeffnet den Unix-Socket (synchron).
    - ``start_polling(interval)``: startet periodisches Abfragen.
    - ``call(method, params)``: synchroner RPC; wirft eine klare Exception,
      wenn nicht mit dem Daemon verbunden.
    - Attribute: ``connected``, ``state``, ``last_error``.
    """

    def __init__(self, on_state=None, on_error=None, socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self.on_state = on_state
        self.on_error = on_error
        self._client = None
        self._timer_id = None
        self._busy = False
        self.connected = False
        self.state = {"processes": [], "enabled": True, "rules": []}
        self.last_error = None

    # --- Verbindung ------------------------------------------------------

    def connect(self, timeout: float = 2.0) -> None:
        cl = Client(self.socket_path, connect_timeout=timeout)
        try:
            cl.connect()
        except ConnectionError as error:
            self.connected = False
            self.last_error = str(error)
            self._notify_error(
                "Throtl daemon is not reachable.\nStart it with:\n"
                "  sudo systemctl start netlimiter-clone\n"
                f"({error})"
            )
            raise
        self._client = cl
        self.connected = True
        self.last_error = None

    def _notify_error(self, message: str) -> None:
        if self.on_error is not None:
            GLib.idle_add(self.on_error, message)

    # --- Polling ---------------------------------------------------------

    def start_polling(self, interval: float = 1.0) -> None:
        if self._timer_id is None and self.connected:
            self._timer_id = GLib.timeout_add(
                int(interval * 1000), self._poll_once
            )

    def _poll_once(self) -> bool:
        if self._busy or not self.connected or self._client is None:
            return True  # weiter laufen (nicht stoppen)
        self._busy = True
        try:
            state = self._client.call("list_processes", timeout=3.0)
        except Exception as error:
            self.connected = False
            self._notify_error(f"Live data failed: {error}")
            return False
        finally:
            self._busy = False
        self.state = state
        if self.on_state is not None:
            GLib.idle_add(self.on_state, state)
        return True

    # --- RPC -------------------------------------------------------------

    def call(self, method: str, params=None, timeout: float = 5.0):
        """Synchroner RPC. Klare Exception, wenn nicht verbunden."""
        if not self.connected or self._client is None:
            raise ConnectionError(
                "Not connected to the Throtl daemon. Start it with "
                "`sudo systemctl start netlimiter-clone` and open Throtl again."
            )
        return self._client.call(method, params or {}, timeout=timeout)

    # --- Lifecycle -------------------------------------------------------

    def shutdown(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self.connected = False
