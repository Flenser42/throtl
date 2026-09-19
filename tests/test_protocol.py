import socket
import tempfile
import threading
import time
import unittest

from throtl import protocol


def _fake_daemon(path, handler):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(5)
    try:
        conn, _ = server.accept()
        handler(conn)
        conn.close()
    finally:
        server.close()


def _queue_ready_daemon(path, handler, ready):
    """Bindet den Socket synchron, meldet 'ready', schaltet erst dann auf accept."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(5)
    ready.set()
    try:
        conn, _ = server.accept()
        handler(conn)
        conn.close()
    finally:
        server.close()


class MessageFramingTest(unittest.TestCase):
    def test_roundtrip(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        payload = {"id": 1, "method": "status", "params": {"a": "ümlaut"}}
        protocol.send_message(left, payload)
        received = protocol.read_message(right)
        self.assertEqual(received, payload)
        left.close()
        right.close()

    def test_eof_returns_none(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        left.close()
        self.assertIsNone(protocol.read_message(right))
        right.close()

    def test_oversized_message_rejected(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        huge = "x" * (protocol.MAX_MESSAGE_SIZE + 10)
        with self.assertRaises(protocol.ProtocolError):
            protocol.send_message(left, {"data": huge})
        left.close()
        right.close()

    def test_coalesced_messages_are_not_lost(self):
        """Response + Event im selben recv() duerfen nicht verworfen werden."""
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        first = {"id": 1, "result": {"ok": True}}
        second = {"event": "stats", "data": {"x": 1}}
        protocol.send_message(left, first)
        protocol.send_message(left, second)
        self.assertEqual(protocol.read_message(right), first)
        self.assertEqual(protocol.read_message(right), second)
        left.close()
        right.close()

    def test_iter_messages_handles_pipelined_requests(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        protocol.send_message(left, {"id": 1})
        protocol.send_message(left, {"id": 2})
        left.close()
        self.assertEqual(list(protocol.iter_messages(right)), [{"id": 1}, {"id": 2}])
        right.close()


class ClientTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name + "/daemon.sock"

    def tearDown(self):
        self._tmp.cleanup()

    def _run_server(self, handler, ready=None):
        if ready is not None:
            thread = threading.Thread(
                target=_queue_ready_daemon, args=(self.path, handler, ready), daemon=True
            )
        else:
            thread = threading.Thread(
                target=_fake_daemon, args=(self.path, handler), daemon=True
            )
        thread.start()
        if ready is not None:
            self.assertTrue(ready.wait(5.0), "Server-Socket wurde nicht bereit")
        return thread

    def test_call_and_events(self):
        events = []
        ready = threading.Event()

        def handler(conn):
            request = protocol.read_message(conn)
            self.assertEqual(request["method"], "hello")
            protocol.send_message(conn, {"id": request["id"], "result": {"ok": True}})
            protocol.send_message(conn, {"event": "stats", "data": {"x": 1}})
            request2 = protocol.read_message(conn)
            protocol.send_message(conn, {"id": request2["id"], "result": 42})

        self._run_server(handler, ready=ready)
        client = protocol.Client(self.path, on_event=events.append)
        try:
            client.connect()
            self.assertEqual(client.call("hello"), {"ok": True})
            self.assertEqual(client.call("second"), 42)
            deadline = time.monotonic() + 2
            while not events and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(events, [{"event": "stats", "data": {"x": 1}}])
        finally:
            client.close()

    def test_rpc_error(self):
        ready = threading.Event()

        def handler(conn):
            request = protocol.read_message(conn)
            protocol.send_message(
                conn,
                {
                    "id": request["id"],
                    "error": {"code": protocol.APPLICATION_ERROR, "message": "kaputt"},
                },
            )

        self._run_server(handler, ready=ready)
        client = protocol.Client(self.path)
        try:
            client.connect()
            with self.assertRaises(protocol.RpcError) as ctx:
                client.call("boom")
            self.assertEqual(ctx.exception.code, protocol.APPLICATION_ERROR)
            self.assertEqual(ctx.exception.message, "kaputt")
        finally:
            client.close()

    def test_timeout(self):
        ready = threading.Event()

        def handler(conn):
            time.sleep(5)

        self._run_server(handler, ready=ready)
        client = protocol.Client(self.path)
        try:
            client.connect()
            with self.assertRaises(protocol.TimeoutError_):
                client.call("slow", timeout=0.4)
        finally:
            client.close()

    def test_connect_failure(self):
        client = protocol.Client(self.path + ".nonexistent")
        with self.assertRaises(ConnectionError):
            client.connect()


if __name__ == "__main__":
    unittest.main()
