"""IPC protocol: JSON messages over a Unix socket (newline-delimited).

Format per message: one line with one JSON object.

Request (client -> daemon):     {"id": 1, "method": "set_limit", "params": {...}}
Response (daemon -> client):    {"id": 1, "result": {...}}
                                 {"id": 1, "error": {"code": -32000, "message": "..."}}
Event (daemon -> client, push): {"event": "stats", "data": {...}}
"""

import json
import socket
import threading
import time
import weakref

MAX_MESSAGE_SIZE = 1 << 20  # 1 MiB per message

# Error codes (JSON-RPC-like)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
APPLICATION_ERROR = -32000


class ProtocolError(Exception):
    """Error in framing/JSON parsing."""


class RpcError(Exception):
    """Error response from the daemon (carries code + message)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class TimeoutError_(TimeoutError):
    """No response within the timeout."""


# Leftover bytes buffered per socket between two read_message() calls.
# WeakKeyDictionary, so closed sockets do not hold memory.
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
        raise ProtocolError(f"message too large ({len(data)} bytes)")
    sock.sendall(data)


def read_message(sock: socket.socket):
    """Read one message, blocking. Returns None on EOF.

    A leftover chunk read by an earlier ``recv()`` is buffered per socket.
    Without this buffer, messages arriving in the same packet (e.g. response +
    event, or two pipelined requests) would be lost — exactly what caused
    sporadically missing events.
    """
    buf = _take_buffer(sock)
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            if not buf:
                return None
            _store_buffer(sock, b"")
            raise ProtocolError("connection closed mid-message")
        buf += chunk
        if len(buf) > MAX_MESSAGE_SIZE:
            _store_buffer(sock, b"")
            raise ProtocolError("message too large")
    line, rest = buf.split(b"\n", 1)
    _store_buffer(sock, rest)
    try:
        return json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ProtocolError(f"invalid JSON: {error}") from error
    except RecursionError as error:
        # Deeply nested JSON (< MAX_MESSAGE_SIZE) blows the parser's recursion
        # limit; that is a protocol error, not a crash.
        raise ProtocolError("JSON too deeply nested") from error


def iter_messages(sock: socket.socket):
    """Generator over incoming messages until EOF."""
    while True:
        message = read_message(sock)
        if message is None:
            return
        yield message


def make_error(code: int, message: str) -> dict:
    return {"code": code, "message": message}


class Client:
    """Blocking IPC client with a reader thread.

    - ``call(method, params)`` runs a synchronous request.
    - Events (``{"event": ...}``) are delivered to ``on_event`` (callable, its own
      thread) — the GUI marshals them into the main loop via GLib.idle_add.
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
        # Serialises sendall(): several threads (GUI poller + user actions)
        # must not interleave on the socket.
        self._send_lock = threading.Lock()

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._connect_timeout)
        try:
            sock.connect(self._path)
        except OSError as error:
            sock.close()
            raise ConnectionError(f"daemon not reachable at {self._path}: {error}") from error
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
                    # Late responses to already-timed-out calls must not grow
                    # the map without bounds.
                    while len(self._responses) > 256:
                        self._responses.pop(next(iter(self._responses)))
                    self._cond.notify_all()
            elif "event" in message and self._on_event is not None:
                try:
                    self._on_event(message)
                except Exception:  # callback errors must not kill the reader
                    pass
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def call(self, method: str, params=None, timeout: float = 10.0):
        if self._sock is None:
            raise ConnectionError("client not connected")
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
                    raise ConnectionError("connection to the daemon closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Without this the entry would stay in _responses forever
                    # (a client with occasional timeouts leaks memory).
                    self._responses.pop(message_id, None)
                    raise TimeoutError_(f"timeout on '{method}' after {timeout}s")
                self._cond.wait(remaining)
            response = self._responses.pop(message_id)
        if "error" in response:
            error = response["error"]
            raise RpcError(error.get("code", APPLICATION_ERROR), error.get("message", "unknown error"))
        return response.get("result")

    def close(self) -> None:
        self._closed = True
        if self._sock is not None:
            try:
                # shutdown() wakes a reader blocked in recv() immediately. A
                # plain close() leaves it hanging on Linux until the next
                # packet/EOF — the join() then ran into its full timeout
                # (in the test 2 s per connection).
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
