"""GTK-GUI-IPC-Client: verbindet sich mit dem Daemon und pollt Live-Daten.

Da Events im Daemon nicht gepusht werden (siehe throtl.daemon), pollt die GUI
periodisch `list_processes` + `status` über einen GLib-Timer. Ergebnisse werden
synchron gehalten; aufgerufene Callbacks sitzen im GLib-Mainloop.
"""

import json
import socket
import threading

from gi.repository import GLib

from .. import SOCKET_PATH, __version__
from ..protocol import Client, RpcError, send_message, read_message


class GuiClient:
    """Kapselt eine Verbindung zum Daemon mit Poll-Loop.

    - ``connect()``: oeffnet den Unix-Socket.
    - ``start_polling(interval, no_data_cb)``: startet periodisches Abfragen.
    - Attribute halten den letzten Stand: ``config``, ``state``, ``status``.
    - ``busy`` True, solange ein Call laeuft (fuer UI-Feedback).
    """

    def __init__(self, on_state=None, on_status=None, on_error=None,
                 socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self.on_state = on_state
        self.on_status = on_status
        self.on_error = on_error
        self._client = None
        self._timer_id = None
        self._busy = False
        self.config = {}
        self.state = {"processes": [], "enabled": True, "rules": []}
        self.status = {}
        self.connected = False

    def connect(self) -> None:
        self._client = Client(self.socket_path)
        try:
            self._client.connect()
        except ConnectionError as error:
            self.connected = False
            self._notify_error(f"Daemon nicht erreichbar: {error}")
            raise
        self.connected = True

    def _notify_error(self, message: str) -> None:
        if self.on_error is not None:
            GLib.idle_add(self.on_error, message)

    def start_polling(self, interval: float = 1.0) -> None:
        if self._timer_id is None and self.connected:
            self._timer_id = GLib.timeout_add(
                int(interval * 1000), self._poll_once
            )

    def _poll_once(self) -> bool:
        if self._busy or not self.connected:
            return True  # weiter pollend
        self._busy = True
        try:
            if self._client is None:
                return True
            state = self._client.call("list_processes", timeout=3.0)
        except Exception as error:
            self._notify_error(f"Live-Daten: {error}")
            self.connected = False
            return False
        finally:
            self._busy = False
        self.state = state
        if self.on_state is not None:
            GLib.idle_add(self.on_state, state)
        return True

    def call(self, method: str, params=None, timeout: float = 5.0):
        """Synchronen RPC-Aufruf tarnen (fuehrt den Call im aufrufenden Thread aus).

        Blockiert kurz; fuer GUI-Aktionen (setzen/loeschen) akzeptabel.
        """
        if self._client is None:
            raise ConnectionError("GUI-Client nicht verbunden")
        return self._client.call(method, params or {}, timeout=timeout)

    def shutdown(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
