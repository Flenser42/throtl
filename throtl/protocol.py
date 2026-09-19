"""IPC-Protokoll: JSON-Nachrichten ueber Unix-Socket (newline-delimited).

Format pro Nachricht: eine Zeile mit einem JSON-Objekt.

Request (Client -> Daemon):      {"id": 1, "method": "set_limit", "params": {...}}
Response (Daemon -> Client):     {"id": 1, "result": {...}}
                                 {"id": 1, "error": {"code": -32000, "message": "..."}}
Event (Daemon -> Client, push):  {"event": "stats", "data": {...}}
"""

import json
import socket
import threading
import time
import weakref

MAX_MESSAGE_SIZE = 1 << 20  # 1 MiB pro Nachricht

# Fehlercodes (JSON-RPC-aehnlich)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
APPLICATION_ERROR = -32000


class ProtocolError(Exception):
    """Fehler beim Framing/JSON-Parsing."""


class RpcError(Exception):
    """Fehlerantwort des Daemons (transportiert code + message)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class TimeoutError_(TimeoutError):
    """Keine Antwort innerhalb des Timeouts."""


# Pro Socket gepufferte Restbytes zwischen zwei read_message()-Aufrufen.
# WeakKeyDictionary, damit geschlossene Sockets keinen Speicher halten.
_READ_BUFFERS: "weakref.WeakKeyDictionary[socket.socket, bytes]" = (
    weakref.WeakKeyDictionary()
)
_READ_BUFFERS_LOCK = threading.Lock()


def _take_buffer(sock: socket.socket) -> bytes:
    with _READ_BUFFERS_LOCK:
        return _READ_BUFFERS.get(sock, b"")


def _store_buffer(sock: socket.socket, buf: bytes) -> None:
    with _READ_BUFFERS_LOCK:
        if buf:
            _READ_BUFFERS[sock] = buf
        else:
            _READ_BUFFERS.pop(sock, None)


def send_message(sock: socket.socket, obj) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"Nachricht zu gross ({len(data)} Bytes)")
    sock.sendall(data)


def read_message(sock: socket.socket):
    """Eine Nachricht blockierend lesen. Gibt None bei EOF zurueck.

    Ein evtl. in einem frueheren ``recv()`` mitgelesenes Reststueck wird pro
    Socket gepuffert. Ohne diesen Puffer gingen Nachrichten verloren, die im
    selben Paket eintreffen (z. B. Response + Event oder zwei gepipelined
    Requests) — genau das verursachte sporadisch fehlende Events.
    """
    buf = _take_buffer(sock)
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            if not buf:
                return None
            _store_buffer(sock, b"")
            raise ProtocolError("Verbindung mitten in Nachricht geschlossen")
        buf += chunk
        if len(buf) > MAX_MESSAGE_SIZE:
            _store_buffer(sock, b"")
            raise ProtocolError("Nachricht zu gross")
    line, rest = buf.split(b"\n", 1)
    _store_buffer(sock, rest)
    try:
        return json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ProtocolError(f"ungueltiges JSON: {error}") from error


def iter_messages(sock: socket.socket):
    """Generator ueber eingehende Nachrichten bis EOF."""
    while True:
        message = read_message(sock)
        if message is None:
            return
        yield message


def make_error(code: int, message: str) -> dict:
    return {"code": code, "message": message}


class Client:
    """Blockierender IPC-Client mit Reader-Thread.

    - ``call(method, params)`` fuehrt einen synchronen Request aus.
    - Events (``{"event": ...}``) werden an ``on_event`` (Callable, eigener Thread)
      zugestellt — die GUI marshalt sie per GLib.idle_add in den Mainloop.
    """

    def __init__(self, path: str, on_event=None, connect_timeout: float = 5.0):
        self._path = path
        self._on_event = on_event
        self._connect_timeout = connect_timeout
        self._sock = None
        self._thread = None
        self._closed = False
        self._next_id = 0
        self._responses = {}
        self._cond = threading.Condition()
        # Serialisiert sendall(): mehrere Threads (GUI-Poller + Nutzeraktionen)
        # duerfen sich nicht auf dem Socket verschraenken.
        self._send_lock = threading.Lock()

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._connect_timeout)
        try:
            sock.connect(self._path)
        except OSError as error:
            sock.close()
            raise ConnectionError(f"Daemon nicht erreichbar unter {self._path}: {error}") from error
        sock.settimeout(None)
        self._sock = sock
        self._thread = threading.Thread(target=self._reader, name="throtl-ipc", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while not self._closed:
            try:
                message = read_message(self._sock)
            except (OSError, ProtocolError):
                message = None
            if message is None:
                break
            if "id" in message:
                with self._cond:
                    self._responses[message["id"]] = message
                    self._cond.notify_all()
            elif "event" in message and self._on_event is not None:
                try:
                    self._on_event(message)
                except Exception:  # Callback-Fehler duerfen den Reader nicht killen
                    pass
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def call(self, method: str, params=None, timeout: float = 10.0):
        if self._sock is None:
            raise ConnectionError("Client nicht verbunden")
        with self._cond:
            self._next_id += 1
            message_id = self._next_id
        request = {"id": message_id, "method": method, "params": params or {}}
        with self._send_lock:
            send_message(self._sock, request)
        with self._cond:
            deadline = time.monotonic() + timeout
            while message_id not in self._responses:
                if self._closed:
                    raise ConnectionError("Verbindung zum Daemon geschlossen")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError_(f"Timeout bei '{method}' nach {timeout}s")
                self._cond.wait(remaining)
            response = self._responses.pop(message_id)
        if "error" in response:
            error = response["error"]
            raise RpcError(error.get("code", APPLICATION_ERROR), error.get("message", "unbekannter Fehler"))
        return response.get("result")

    def close(self) -> None:
        self._closed = True
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
