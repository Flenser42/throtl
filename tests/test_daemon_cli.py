import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from throtl import daemon, protocol
from throtl.engine import SimEngine


class DaemonHarness:
    """Startet einen Daemon im Sim-Modus auf einem tmp-Socket im Thread."""

    def __init__(self, tmpdir):
        self.tmpdir = tmpdir
        self.socket_path = os.path.join(tmpdir, "daemon.sock")
        self.config_dir = os.path.join(tmpdir, "config")
        self.ready = threading.Event()
        self.daemon = None
        self._thread = None

    def start(self, interval=0.3, monitor_factory=None):
        os.environ["THROTL_CONFIG_DIR"] = self.config_dir
        self.daemon = daemon.Daemon(
            socket_path=self.socket_path,
            config_dir=self.config_dir,
            engine=SimEngine("test0"),
            interval=interval,
            monitor_factory=monitor_factory or _fake_monitor_factory,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.ready.wait(3.0)
        self.wait_socket()

    def _run(self):
        self.daemon.start()
        self.ready.set()
        self.daemon.serve_forever()

    def wait_socket(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                return
            time.sleep(0.01)
        raise TimeoutError("Daemon-Socket nicht erschienen")

    def stop(self):
        if self.daemon is not None:
            self.daemon.shutdown()
        os.environ.pop("THROTL_CONFIG_DIR", None)


def _client(socket_path):
    client = protocol.Client(socket_path)
    client.connect()
    return client


class FakeMonitor:
    """Stub-Monitor: liefert einen konstanten Snapshot (fuer Tests)."""

    def __init__(self, device, interval=1.0):
        self.device = device
        self.interval = interval
        self._running = False

    def start(self):
        self._running = True

    def snapshot(self):
        return {
            "1001": {"name": "/usr/bin/firefox", "uid": "1000",
                     "download": 1500.0, "upload": 80.0},
            "1002": {"name": "ssh", "uid": "1000",
                     "download": 20.0, "upload": 200.0},
        }

    def stop(self):
        self._running = False


def _fake_monitor_factory(device, interval=1.0):
    return FakeMonitor(device, interval)


class DaemonCliEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.harness = DaemonHarness(self._tmp.name)
        self.harness.start()
        self.client = _client(self.harness.socket_path)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        self.harness.stop()
        self._tmp.cleanup()

    def test_status(self):
        status = self.client.call("status")
        self.assertIsInstance(status["daemon"], str)
        self.assertTrue(status["simulated"])
        self.assertEqual(status["engine"]["device"], "test0")
        self.assertTrue(status["monitoring"])

    def test_set_global_and_persist(self):
        self.client.call("set_global", {"download_limit": "2mbps",
                                        "upload_priority": "hoch"})
        cfg = self.client.call("get_config")
        self.assertEqual(cfg["global"]["download_limit"], 2000)
        self.assertEqual(cfg["global"]["upload_priority"], "hoch")
        # Persistenz: Datei existiert und laedt denselben Wert
        import throtl.config as C

        loaded = C.load_config(os.path.join(self.harness.config_dir, "config.toml"))
        self.assertEqual(loaded["global"]["download_limit"], 2000)

    def test_set_process_roundtrip(self):
        result = self.client.call(
            "set_process",
            {"name": "Firefox", "match_type": "exe",
             "match_value": "/usr/lib/firefox/firefox",
             "download_limit": 2048, "upload_limit": 512, "priority": "kritisch"},
        )
        self.assertEqual(result["name"], "Firefox")
        self.assertEqual(result["download_limit"], 2048)
        cfg = self.client.call("get_config")
        self.assertEqual(len(cfg["processes"]), 1)
        self.assertEqual(cfg["processes"][0]["priority"], "kritisch")

        # Loeschen
        removed = self.client.call("remove_process", {"key": result["key"]})
        self.assertTrue(removed["removed"])
        cfg = self.client.call("get_config")
        self.assertEqual(len(cfg["processes"]), 0)

    def test_toggle_enabled(self):
        result = self.client.call("toggle_enabled", {"enabled": False})
        self.assertFalse(result["enabled"])
        cfg = self.client.call("get_config")
        self.assertFalse(cfg["global"]["enabled"])

    def test_set_unit_invalid_rejected(self):
        with self.assertRaises(protocol.RpcError):
            self.client.call("set_unit", {"unit": "tb"})

    def test_set_unit_mbs_accepted(self):
        result = self.client.call("set_unit", {"unit": "mBs"})
        self.assertEqual(result["unit"], "mBs")
        # Der Daemon darf die mBs-Unit nicht ablehnen/crashen
        cfg = self.client.call("get_config")
        self.assertEqual(cfg["unit"], "mBs")

    def test_list_processes_live(self):
        state = self.client.call("list_processes")
        self.assertTrue(state["enabled"])
        pids = {p["pid"] for p in state["processes"]}
        self.assertEqual(pids, {"1001", "1002"})
        firefox = next(p for p in state["processes"] if p["pid"] == "1001")
        self.assertEqual(firefox["name"], "/usr/bin/firefox")
        self.assertGreater(firefox["download"], 0)


if __name__ == "__main__":
    unittest.main()
