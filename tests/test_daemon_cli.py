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
    """Starts a daemon in sim mode on a tmp socket in a thread."""

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
        raise TimeoutError("daemon socket did not appear")

    def stop(self):
        if self.daemon is not None:
            self.daemon.shutdown()
        os.environ.pop("THROTL_CONFIG_DIR", None)


def _client(socket_path):
    client = protocol.Client(socket_path)
    client.connect()
    return client


class FakeMonitor:
    """Stub monitor: returns a constant snapshot (for tests)."""

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

    def test_cmdline_rule_attributed_to_app(self):
        import tempfile

        from throtl.config import make_rule
        from throtl.daemon import Daemon
        from throtl.engine import SimEngine

        class _Mon:
            def snapshot(self):
                return {
                    "1": {"name": "java -jar /opt/JDownloader/JDownloader.jar",
                          "uid": "1000", "download": 100.0, "upload": 0.0},
                }

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=tmp + "/d.sock", config_dir=tmp,
                       engine=SimEngine("lo"), monitor_factory=None)
            d.store.upsert_process(make_rule(
                name="JDownloader", match_type="cmdline",
                match_value="JDownloader", priority="normal"))
            d.monitor = _Mon()
            snap = d._collect_snapshot()

        apps = {a["name"]: a for a in snap["apps"]}
        self.assertEqual(apps["JDownloader"]["rule_name"], "JDownloader")
        self.assertEqual(apps["JDownloader"]["rule_key"], "cmdline:JDownloader")

    def test_download_capped_at_rule_limit(self):
        """nethogs misst ingress VOR dem Shaping: der berichtete Download wird
        auf das Limit der Regel gedeckelt (effektive Rate)."""
        import tempfile

        from throtl.config import make_rule
        from throtl.daemon import Daemon
        from throtl.engine import SimEngine

        rule = make_rule("Capped", "exe", "/usr/bin/curl", download_limit=500)

        class _Mon:
            def snapshot(self):
                return {
                    # raw 1000 -> capped to 500
                    "1": {"name": "/usr/bin/curl -s x", "uid": "1000",
                          "download": 1000.0, "upload": 0.0},
                    # raw 200 -> below the 500 limit, unchanged
                    "2": {"name": "/usr/bin/curl -s y", "uid": "1000",
                          "download": 200.0, "upload": 0.0},
                }

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=tmp + "/d.sock", config_dir=tmp,
                       engine=SimEngine("lo"), monitor_factory=None)
            d.store.upsert_process(rule)
            d.monitor = _Mon()
            snap = d._collect_snapshot()

        apps = {a["name"]: a for a in snap["apps"]}
        self.assertEqual(apps["curl"]["download"], 700.0)
        # capped pid reports the effective (shaped) rate
        pids = {p["pid"]: p for p in snap["processes"]}
        self.assertEqual(pids["1"]["download"], 500.0)
        self.assertEqual(pids["2"]["download"], 200.0)

    def test_match_hint_for_native_and_interpreted_apps(self):
        import tempfile

        from throtl.daemon import Daemon
        from throtl.engine import SimEngine

        class _Mon:
            def snapshot(self):
                return {
                    "1": {"name": "/usr/bin/curl -s x", "uid": "1000",
                          "download": 500.0, "upload": 5.0},
                    "2": {"name": "python3 ./legendary install", "uid": "1000",
                          "download": 1000.0, "upload": 10.0},
                }

        with tempfile.TemporaryDirectory() as tmp:
            d = Daemon(socket_path=tmp + "/d.sock", config_dir=tmp,
                       engine=SimEngine("lo"), monitor_factory=None)
            d.monitor = _Mon()
            snap = d._collect_snapshot()

        apps = {a["name"]: a for a in snap["apps"]}
        self.assertEqual(
            apps["curl"]["match_hint"],
            {"type": "exe", "value": "/usr/bin/curl"},
        )
        self.assertEqual(apps["legendary"]["match_hint"]["type"], "cmdline")
        self.assertIn("legendary", apps["legendary"]["match_hint"]["value"])


class InterfaceChangeTest(unittest.TestCase):
    """The daemon follows the default route only when interface is auto."""

    def _daemon(self, tmp):
        from throtl.daemon import Daemon

        return Daemon(socket_path=tmp + "/d.sock", config_dir=tmp,
                      engine=SimEngine("lo"), monitor_factory=None)

    def test_auto_interface_rebinds_engine(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d.store.get()["interface"] = "auto"
            d.interface = "wlan0"
            with mock.patch("throtl.daemon.detect_default_interface",
                            return_value="eth1"):
                d._check_interface_change()
            self.assertEqual(d.interface, "eth1")
            self.assertEqual(d.engine.device, "eth1")

    def test_pinned_interface_is_left_alone(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            d = self._daemon(tmp)
            d.store.get()["interface"] = "tailscale0"
            d.interface = "tailscale0"
            with mock.patch("throtl.daemon.detect_default_interface",
                            return_value="eth1") as detect:
                d._check_interface_change()
            self.assertEqual(d.interface, "tailscale0")
            self.assertEqual(d.engine.device, "lo")
            detect.assert_not_called()


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
        """GUI sends stored rules back -> the pattern must not be escaped again
        (otherwise the rule no longer matches)."""
        created = self.client.call(
            "set_process",
            {"name": "python3.12", "match_type": "name",
             "match_value": "python3.12", "priority": "normal"},
        )
        self.assertIn("\\.", created["match_value"])  # escaped once
        edited = dict(created)
        edited["download_limit"] = 1234
        updated = self.client.call("set_process", edited)
        self.assertEqual(updated["key"], created["key"])
        self.assertEqual(updated["download_limit"], 1234)
        self.assertEqual(updated["match_value"], created["match_value"])
        cfg = self.client.call("get_config")
        self.assertEqual(len(cfg["processes"]), 1)  # no duplicate
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
        # Remove the window via update (the rule stays).
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
        """/proc/net/dev deltas give the real interface rate."""
        import time as _t

        from throtl.daemon import Daemon

        d = Daemon(socket_path=self.harness.socket_path + ".x",
                   config_dir=self.harness.config_dir,
                   engine=SimEngine("lo"), interval=1.0,
                   monitor_factory=None)
        d.interface = "lo"
        first = d._iface_throughput()
        self.assertEqual(first, (None, None))    # first call: no delta
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
        # The daemon must not reject/crash on the mBs unit
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
        """The monitor tick feeds the StatsStore; get_stats returns bytes."""
        time.sleep(0.5)  # wait at least one tick (interval=0.3)
        stats = self.client.call("get_stats", {"window": "minute"})
        self.assertEqual(stats["window"], "minute")
        apps = {item["app"]: item for item in stats["apps"]}
        self.assertIn("firefox", apps)
        self.assertGreater(apps["firefox"]["download"], 0)
        self.assertGreater(stats["totals"]["download"], 0)

        reset = self.client.call("reset_stats")
        self.assertTrue(reset["ok"])
        # No crash; a tick may already have booked data again.
        self.assertIsInstance(
            self.client.call("get_stats", {"window": "minute"})["apps"], list)

    def test_get_stats_invalid_window(self):
        with self.assertRaises(protocol.RpcError):
            self.client.call("get_stats", {"window": "week"})


class DaemonAsyncApplyTest(unittest.TestCase):
    """A slow engine apply must not block the RPC response."""

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
    """A dead monitor is detected, reaped and restarted."""

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

    def test_get_budgets_emits_alerts_and_dedups(self):
        empty = self.client.call("get_budgets")
        self.assertIsInstance(empty["alerts"], list)
        self.assertEqual(empty["alerts"], [])

        # Deterministic usage (450 MB) against a 1 MB global day budget.
        self.harness.daemon.stats.record(
            "seed", download_kbit=1000.0, now=time.time(), interval=3600.0)
        self.client.call("set_budget", {"day": "1mb"})

        first = self.client.call("get_budgets")
        self.assertTrue(any(e["scope"] == "global" and e["window"] == "day"
                            for e in first["entries"]))
        self.assertEqual(len(first["alerts"]), 1)
        alert = first["alerts"][0]
        self.assertEqual(alert["scope"], "global")
        self.assertEqual(alert["window"], "day")
        self.assertEqual(alert["level"], 2)

        # Same level on the next poll: no repeated alert.
        second = self.client.call("get_budgets")
        self.assertEqual(second["alerts"], [])

    def test_stats_history_series(self):
        result = self.client.call("get_stats_history", {"window": "minute"})
        self.assertEqual(result["window"], "minute")
        self.assertEqual(len(result["series"]), 60)


class MatchRulesTest(unittest.TestCase):
    """Regeln wiederfinden: gespeicherte Muster sind regex-escaped.

    ``make_rule`` escapes the match_value for TrafficToll (where it is used as
    a regex). The display matching must undo that, otherwise no rule matches and
    ``rule_name`` stays empty everywhere.
    """

    def test_matches_a_rule_stored_with_escapes(self):
        from throtl.config import make_rule

        rule = make_rule(name="yt-dlp", match_type="exe",
                         match_value="/usr/bin/yt-dlp", priority="hoch")
        self.assertIn("\\", rule["match_value"], "precondition: escaped")
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

    def test_exe_rule_sh_does_not_match_bash(self):
        from throtl.config import make_rule

        rule = make_rule(name="sh", match_type="exe", match_value="sh",
                         priority="normal")
        self.assertEqual(daemon._match_rules([rule], "/bin/bash"), {})
        self.assertEqual(daemon._match_rules([rule], "/bin/sh").get("name"), "sh")

    def test_name_rule_matches_basename_of_path(self):
        from throtl.config import make_rule

        rule = make_rule(name="firefox", match_type="name",
                         match_value="firefox", priority="normal")
        self.assertEqual(
            daemon._match_rules([rule], "/usr/lib/firefox/firefox").get("name"),
            "firefox")


class DaemonSocketLivenessTest(unittest.TestCase):
    """A second daemon must not disinherit a running one."""

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
            self.assertTrue(os.path.exists(path))  # not removed

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
    """A broken frame must end only the connection, not the thread."""

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
            d._handle_connection(_Conn())  # must not throw


class DaemonMonitorResilienceTest(unittest.TestCase):
    """A faulty tick must not kill the monitor thread."""

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
    """RPC thread and monitor thread must not tick simultaneously."""

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
    """A dead tt process is automatically re-applied."""

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


class RpcHardeningTest(unittest.TestCase):
    """Regressionen aus dem Robustheits-/Security-Review (0.12)."""

    def _daemon(self, tmp):
        instance = daemon.Daemon(
            socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
            engine=SimEngine("lo"), interval=0.05, monitor_factory=None,
        )
        instance.interface = "lo"
        return instance

    def _call(self, instance, method, params=None):
        return instance._dispatch({"id": 1, "method": method, "params": params or {}})

    def test_non_object_json_is_rejected_not_fatal(self):
        # 123/"hi"/[]/true are valid JSON, but not an object: that must not
        # kill the connection thread with an AttributeError.
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            for payload in (123, "hi", [], True, None):
                with self.subTest(payload=payload):
                    reply = instance._dispatch(payload)
                    self.assertIn("error", reply)
                    self.assertIsNone(reply["id"])

    def test_get_config_returns_a_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            result = self._call(instance, "get_config")["result"]
            result["processes"].append({"key": "injected"})
            self.assertEqual(instance.store.get()["processes"], [])

    def test_set_global_and_budget_return_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            globals_result = self._call(
                instance, "set_global", {"download_limit": 1000}
            )["result"]
            globals_result["download_limit"] = 1
            self.assertEqual(instance.store.get()["global"]["download_limit"], 1000)

            budgets_result = self._call(instance, "set_budget", {"day": 1000})["result"]
            budgets_result["budgets"]["day"] = 999999
            self.assertEqual(instance.store.get()["budgets"]["day"], 1000)

    def test_cmdline_rule_matches_literally_and_is_not_unescaped(self):
        from throtl.daemon import _match_rules

        plain = {"name": "JD", "match_type": "cmdline", "match_value": "JDownloader"}
        self.assertEqual(
            _match_rules([plain], "java -jar /opt/JDownloader/JDownloader.jar").get("name"),
            "JD",
        )
        # A regex with an escape must NOT be converted back: otherwise
        # \\. matches as "." and the rule matches a different process.
        escaped = {"name": "Jar", "match_type": "cmdline", "match_value": r"foo\.jar"}
        self.assertEqual(_match_rules([escaped], "foo.jar -x"), {})
        # exe/name stay escaped-compared (unchanged).
        exe_rule = {"name": "X", "match_type": "exe", "match_value": r"/usr/bin/x\.y"}
        self.assertEqual(_match_rules([exe_rule], "/usr/bin/x.y").get("name"), "X")

    def test_string_false_is_coerced(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            reply = self._call(instance, "toggle_enabled", {"enabled": "false"})["result"]
            self.assertFalse(reply["enabled"])
            self.assertFalse(instance.store.get()["global"]["enabled"])
            budget = self._call(instance, "set_budget", {"enabled": "false"})["result"]
            self.assertFalse(budget["budgets"]["enabled"])

    def test_priority_zero_is_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            self._call(instance, "set_process", {
                "name": "X", "match_type": "exe", "match_value": "/usr/bin/x",
                "priority": 0,
            })
            self.assertEqual(instance.store.get()["processes"][0]["priority"], "kritisch")

    def test_rules_changed_tick_does_not_record_stats(self):
        # A rule change triggers an extra tick; it must not book the
        # byte sums a second time.
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            seen = []
            instance._collect_snapshot = lambda record_stats=False: (
                seen.append(record_stats) or {}
            )
            instance._safe_tick_monitor(record_stats=False)
            self.assertEqual(seen, [False])

    def test_import_config_refuses_interface_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            reply = self._call(
                instance, "import_config", {"config": {"interface": "eth9"}}
            )
            self.assertIn("error", reply)
            self.assertIn("interface", reply["error"]["message"])

    def test_bad_window_is_reported_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = self._daemon(tmp)
            reply = self._call(instance, "set_process", {
                "name": "X", "match_type": "exe", "match_value": "/usr/bin/x",
                "window": {"days": [0], "start": "20:00", "end": "20:00"},
            })
            self.assertIn("error", reply)


if __name__ == "__main__":
    unittest.main()
