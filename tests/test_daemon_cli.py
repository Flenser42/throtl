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

    def test_set_process_with_window(self):
        result = self.client.call("set_process", {
            "name": "Steam", "match_type": "name", "match_value": "steam",
            "download_limit": 512,
            "window": {"days": ["sa", "so"], "start": "10:00", "end": "23:00"},
        })
        self.assertEqual(result["window"]["days"], [5, 6])
        cfg = self.client.call("get_config")
        self.assertEqual(cfg["processes"][0]["window"]["start"], "10:00")
        # Fenster per Update entfernen (Regel bleibt erhalten).
        updated = self.client.call("set_process", {
            "key": result["key"], "window": None})
        self.assertIsNone(updated["window"])
        self.assertEqual(len(self.client.call("get_config")["processes"]), 1)

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


class MatchRulesTest(unittest.TestCase):
    """Regeln wiederfinden: gespeicherte Muster sind regex-escaped.

    ``make_rule`` escaped den match_value fuer TrafficToll (dort wird er als
    Regex benutzt). Der Anzeige-Abgleich muss das rueckgaengig machen, sonst
    passt keine einzige Regel und ``rule_name`` bleibt ueberall leer.
    """

    def test_matches_a_rule_stored_with_escapes(self):
        from throtl.config import make_rule

        rule = make_rule(name="yt-dlp", match_type="exe",
                         match_value="/usr/bin/yt-dlp", priority="hoch")
        self.assertIn("\\", rule["match_value"], "Vorbedingung: escaped")
        self.assertEqual(
            daemon._match_rules([rule], "/usr/bin/yt-dlp").get("name"),
            "yt-dlp")

    def test_matches_a_longer_command_line(self):
        from throtl.config import make_rule

        rule = make_rule(name="steam", match_type="exe",
                         match_value="/opt/Steam/steam", priority="niedrig")
        self.assertEqual(
            daemon._match_rules([rule], "/opt/Steam/steam --silent").get("name"),
            "steam")

    def test_name_rule_matches_the_process_name(self):
        from throtl.config import make_rule

        rule = make_rule(name="Agent.exe", match_type="name",
                         match_value="Agent.exe", priority="normal")
        self.assertEqual(daemon._match_rules([rule], "Agent.exe").get("name"),
                         "Agent.exe")

    def test_unrelated_process_does_not_match(self):
        from throtl.config import make_rule

        rule = make_rule(name="steam", match_type="exe",
                         match_value="/opt/Steam/steam", priority="normal")
        self.assertEqual(daemon._match_rules([rule], "/usr/bin/mpv"), {})


class DaemonSocketLivenessTest(unittest.TestCase):
    """Ein zweiter Daemon darf einen laufenden nicht enterben."""

    def _bare(self, path):
        from throtl.daemon import Daemon

        d = Daemon.__new__(Daemon)
        d.socket_path = path
        return d

    def test_prepare_refuses_a_live_daemon(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.sock")
            open(path, "w").close()
            d = self._bare(path)
            with mock.patch.object(d, "_socket_is_live", return_value=True):
                with self.assertRaises(RuntimeError):
                    d._prepare_socket_path()
            self.assertTrue(os.path.exists(path))  # nicht entfernt

    def test_prepare_removes_a_stale_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.sock")
            open(path, "w").close()
            d = self._bare(path)
            d._socket_is_live = lambda: False
            d._prepare_socket_path()
            self.assertFalse(os.path.exists(path))

    def test_socket_is_live_false_for_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._bare(os.path.join(tmp, "nope.sock"))
            self.assertFalse(d._socket_is_live())

    def test_socket_is_live_true_against_running_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = DaemonHarness(tmp)
            harness.start()
            try:
                d = self._bare(harness.socket_path)
                self.assertTrue(d._socket_is_live())
            finally:
                harness.stop()


class DaemonProtocolErrorTest(unittest.TestCase):
    """Ein kaputter Frame darf nur die Verbindung beenden, nicht den Thread."""

    def test_malformed_frame_does_not_escape_connection_handler(self):
        from unittest import mock

        from throtl.daemon import Daemon
        from throtl.protocol import ProtocolError

        d = Daemon.__new__(Daemon)

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("throtl.daemon.iter_messages",
                        side_effect=ProtocolError("bad frame")):
            d._handle_connection(_Conn())  # darf nicht werfen


class DaemonMonitorResilienceTest(unittest.TestCase):
    """Ein fehlerhafter Tick darf den Monitor-Thread nicht toeten."""

    def test_monitor_loop_survives_tick_exception(self):
        from throtl.daemon import Daemon

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                       engine=SimEngine("lo"), interval=0.02,
                       monitor_factory=None)
            ticks = []

            def flaky(*_args, **_kwargs):
                ticks.append(1)
                if len(ticks) == 1:
                    raise RuntimeError("tick boom")

            d._tick_monitor = flaky
            d._running = True
            thread = threading.Thread(target=d._monitor_loop, daemon=True)
            thread.start()
            deadline = time.monotonic() + 2.0
            while len(ticks) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            d._running = False
            thread.join(1.0)
        self.assertGreaterEqual(len(ticks), 3)


class DaemonTickSerializationTest(unittest.TestCase):
    """RPC-Thread und Monitor-Thread duerfen nicht gleichzeitig ticken."""

    def test_concurrent_ticks_do_not_overlap(self):
        from throtl.daemon import Daemon

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                       engine=SimEngine("lo"), interval=0.05,
                       monitor_factory=None)
            entered = threading.Event()
            release = threading.Event()
            state = {"inside": 0, "max": 0}
            guard = threading.Lock()

            def slow_collect(record_stats=False):
                with guard:
                    state["inside"] += 1
                    state["max"] = max(state["max"], state["inside"])
                entered.set()
                release.wait(2.0)
                with guard:
                    state["inside"] -= 1
                return {"apps": [], "processes": [], "rules": []}

            d._collect_snapshot = slow_collect
            first = threading.Thread(target=d._tick_monitor)
            first.start()
            self.assertTrue(entered.wait(2.0))
            second = threading.Thread(target=d._tick_monitor)
            second.start()
            time.sleep(0.2)
            self.assertTrue(second.is_alive(), "zweiter Tick lief parallel")
            release.set()
            first.join(2.0)
            second.join(2.0)
            self.assertEqual(state["max"], 1)


class DaemonEngineRecoveryTest(unittest.TestCase):
    """Ein gestorbener tt-Prozess wird automatisch neu angewendet."""

    class _Engine:
        simulated = True

        def __init__(self):
            self.device = "test0"
            self.running = False
            self.applied = 0

        def apply(self, config):
            self.applied += 1
            self.running = bool(config["global"].get("enabled", True))

        def is_running(self):
            return self.running

        def stop(self):
            self.running = False

        def status(self):
            return {"running": self.running, "device": self.device}

    def _daemon(self, tmp):
        from throtl.daemon import Daemon

        return Daemon(socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                      engine=self._Engine(), interval=0.05, monitor_factory=None)

    def test_dead_engine_schedules_apply_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d._apply_event.clear()
            d._tick_monitor()
            self.assertTrue(d._apply_event.is_set())

    def test_running_engine_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d.engine.running = True
            d._apply_event.clear()
            d._tick_monitor()
            self.assertFalse(d._apply_event.is_set())

    def test_disabled_shaping_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            with d._state_lock:
                d.store.get()["global"]["enabled"] = False
            d._apply_event.clear()
            d._tick_monitor()
            self.assertFalse(d._apply_event.is_set())


if __name__ == "__main__":
    unittest.main()
