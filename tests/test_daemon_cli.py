import os
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

    def is_alive(self):
        return self._running


def _fake_monitor_factory(device, interval=1.0):
    return FakeMonitor(device, interval)


class AppGroupingTest(unittest.TestCase):
    """Eine App mit vielen Prozessen erscheint als EINE Zeile (Summe)."""

    def test_apps_grouped_and_summed(self):
        import tempfile

        from throtl.daemon import Daemon
        from throtl.engine import SimEngine

        class _Mon:
            def snapshot(self):
                cmd = "python3 ./legendary install CrabEA --platform Windows -y"
                return {
                    "1": {"name": cmd, "uid": "1000", "download": 1000.0, "upload": 10.0},
                    "2": {"name": cmd, "uid": "1000", "download": 1500.0, "upload": 20.0},
                    "3": {"name": "/usr/bin/curl -s x", "uid": "1000",
                          "download": 500.0, "upload": 5.0},
                }

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=tmp + "/d.sock", config_dir=tmp,
                       engine=SimEngine("lo"), monitor_factory=None)
            d.monitor = _Mon()
            snap = d._collect_snapshot()

        apps = {a["name"]: a for a in snap["apps"]}
        self.assertIn("legendary", apps)
        self.assertEqual(apps["legendary"]["download"], 2500.0)
        self.assertEqual(apps["legendary"]["pid_count"], 2)
        self.assertEqual(apps["legendary"]["exe"], "python3")
        self.assertIn("legendary install CrabEA".split()[0],
                      apps["legendary"]["pids"] and apps["legendary"]["name"])
        self.assertEqual(apps["curl"]["download"], 500.0)
        # Attribuierte Summe stimmt mit den Apps ueberein
        self.assertEqual(snap["attributed"]["download"], 3000.0)


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

    def test_updating_rule_does_not_double_escape(self):
        """GUI schickt gespeicherte Regeln zurueck -> Pattern darf nicht erneut
        escaped werden (sonst matcht die Regel nicht mehr)."""
        created = self.client.call(
            "set_process",
            {"name": "python3.12", "match_type": "name",
             "match_value": "python3.12", "priority": "normal"},
        )
        self.assertIn("\\.", created["match_value"])  # einmal escaped
        edited = dict(created)
        edited["download_limit"] = 1234
        updated = self.client.call("set_process", edited)
        self.assertEqual(updated["key"], created["key"])
        self.assertEqual(updated["download_limit"], 1234)
        self.assertEqual(updated["match_value"], created["match_value"])
        cfg = self.client.call("get_config")
        self.assertEqual(len(cfg["processes"]), 1)  # kein Duplikat
        self.assertEqual(cfg["processes"][0]["match_value"], created["match_value"])

    def test_toggle_enabled(self):
        result = self.client.call("toggle_enabled", {"enabled": False})
        self.assertFalse(result["enabled"])
        cfg = self.client.call("get_config")
        self.assertFalse(cfg["global"]["enabled"])

    def test_interface_throughput_sampler(self):
        """/proc/net/dev-Deltas liefern die echte Interface-Rate."""
        import time as _t

        from throtl.daemon import Daemon

        d = Daemon(socket_path=self.harness.socket_path + ".x",
                   config_dir=self.harness.config_dir,
                   engine=SimEngine("lo"), interval=1.0,
                   monitor_factory=None)
        d.interface = "lo"
        first = d._iface_throughput()
        self.assertEqual(first, (None, None))    # erster Aufruf: kein Delta
        _t.sleep(0.3)
        second = d._iface_throughput()
        self.assertEqual(len(second), 2)
        self.assertTrue(second[0] is None or second[0] >= 0)

    def test_snapshot_has_global_and_attributed(self):
        state = self.client.call("list_processes")
        self.assertIn("global", state)
        self.assertIn("attributed", state)
        self.assertIn("download", state["global"])
        self.assertIn("upload", state["attributed"])

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

    def test_stats_rpc_records_and_resets(self):
        """Der Monitor-Tick fuettert den StatsStore; get_stats liefert Bytes."""
        time.sleep(0.5)  # mindestens einen Tick (interval=0.3) abwarten
        stats = self.client.call("get_stats", {"window": "minute"})
        self.assertEqual(stats["window"], "minute")
        apps = {item["app"]: item for item in stats["apps"]}
        self.assertIn("firefox", apps)
        self.assertGreater(apps["firefox"]["download"], 0)
        self.assertGreater(stats["totals"]["download"], 0)

        reset = self.client.call("reset_stats")
        self.assertTrue(reset["ok"])
        # Kein Crash; ein Tick kann bereits wieder Daten gebucht haben.
        self.assertIsInstance(
            self.client.call("get_stats", {"window": "minute"})["apps"], list)

    def test_get_stats_invalid_window(self):
        with self.assertRaises(protocol.RpcError):
            self.client.call("get_stats", {"window": "week"})


class DaemonAsyncApplyTest(unittest.TestCase):
    """Ein langsamer Engine-Apply darf die RPC-Antwort nicht blockieren."""

    def test_set_process_returns_before_engine_finishes(self):
        from throtl.daemon import Daemon

        class SlowEngine:
            simulated = True

            def __init__(self):
                self.device = "test0"
                self.applied = 0

            def apply(self, config):
                time.sleep(1.5)
                self.applied += 1

            def is_running(self):
                return True

            def stop(self):
                pass

            def status(self):
                return {"running": True, "device": "test0", "generation": self.applied}

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                       engine=SlowEngine(), interval=0.5, monitor_factory=None)
            d.start()  # initialer Apply synchron (dauert ~1.5 s)
            try:
                t0 = time.monotonic()
                d._h_set_process({"name": "x", "match_type": "name",
                                  "match_value": "x"})
                elapsed = time.monotonic() - t0
            finally:
                d.shutdown()
        self.assertLess(elapsed, 0.5)


class DaemonMonitorRecoveryTest(unittest.TestCase):
    """Ein gestorbener Monitor wird erkannt, gereapt und neu gestartet."""

    def test_dead_monitor_is_replaced(self):
        from throtl.daemon import Daemon

        created = []

        class DeadMonitor:
            last_error = "boom"

            def __init__(self, device, interval=1.0):
                self.device = device
                self.interval = interval
                self.stopped = False
                created.append(self)

            def start(self):
                pass

            def snapshot(self):
                return {}

            def is_alive(self):
                return False

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                       engine=SimEngine("lo"), interval=0.5,
                       monitor_factory=lambda dev, i: DeadMonitor(dev, i))
            d.start()
            try:
                for _ in range(4):
                    d._tick_monitor()
            finally:
                d.shutdown()
        self.assertGreaterEqual(len(created), 2)          # neu gestartet
        self.assertTrue(any(m.stopped for m in created))  # alten aufgeraeumt


class BudgetsRpcTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.harness = DaemonHarness(self._tmp.name)
        self.harness.start()
        self.client = _client(self.harness.socket_path)

    def tearDown(self):
        self.client.close()
        self.harness.stop()

    def test_set_get_remove_budget(self):
        self.client.call("set_budget", {"day": "1gb", "week": "10gb"})
        budgets = self.client.call("get_config")["budgets"]
        self.assertEqual(budgets["day"], 1_000_000_000)
        self.assertEqual(budgets["week"], 10_000_000_000)
        self.client.call("set_budget", {"app": "firefox", "day": "5mb"})
        budgets = self.client.call("get_config")["budgets"]
        self.assertEqual(budgets["rules"][0]["app"], "firefox")
        self.assertEqual(budgets["rules"][0]["day"], 5_000_000)
        status = self.client.call("get_budgets")
        self.assertIn("entries", status)
        self.assertTrue(any(e["scope"] == "global" for e in status["entries"]))
        removed = self.client.call("remove_budget", {"app": "firefox"})
        self.assertTrue(removed["removed"])

    def test_stats_history_series(self):
        result = self.client.call("get_stats_history", {"window": "minute"})
        self.assertEqual(result["window"], "minute")
        self.assertEqual(len(result["series"]), 60)


if __name__ == "__main__":
    unittest.main()
