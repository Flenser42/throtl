import os
import tempfile
import unittest
from pathlib import Path

from throtl import engine
from throtl.config import default_config, make_rule


def _cfg(processes=None, global_=None, interface=None):
    cfg = default_config()
    if processes is not None:
        cfg["processes"] = processes
    if global_ is not None:
        cfg["global"].update(global_)
    if interface is not None:
        cfg["interface"] = interface
    return cfg


class RenderTest(unittest.TestCase):
    def test_disabled_renders_empty(self):
        cfg = _cfg(global_={"enabled": False})
        text = engine.render_tt_config(cfg)
        self.assertIn("deaktiviert", text)
        self.assertNotIn("processes", text)
        self.assertNotIn("download:", text)

    def test_global_limits(self):
        cfg = _cfg(global_={"download_limit": 10_000, "upload_limit": 2000})
        text = engine.render_tt_config(cfg)
        self.assertIn("download: 10000kbps", text)
        self.assertIn("upload: 2000kbps", text)
        self.assertIn("download-minimum: 100kbps", text)
        self.assertIn("upload-minimum: 10kbps", text)
        self.assertIn("download-priority: 2", text)  # default: normal=2

    def test_unlimited_omits_keys(self):
        text = engine.render_tt_config(_cfg())
        self.assertNotIn("download:", text)
        self.assertNotIn("upload:", text)

    def test_process_rules(self):
        rules = [
            make_rule("Firefox", "exe", "/usr/lib/firefox/firefox",
                      download_limit=2048, upload_limit=512, priority="kritisch"),
            make_rule("Steam", "name", "steam", recursive=True),
        ]
        text = engine.render_tt_config(_cfg(processes=rules))
        self.assertIn('  "Firefox":', text)
        self.assertIn("    download: 2048kbps", text)
        self.assertIn("    upload: 512kbps", text)
        self.assertIn("    download-priority: 0", text)  # kritisch
        self.assertIn("    upload-priority: 0", text)
        self.assertIn('      - exe: "/usr/lib/firefox/firefox"', text)
        self.assertIn("    recursive: true", text)
        self.assertIn('      - name: "steam"', text)

    def test_quoting(self):
        self.assertEqual(engine.yaml_quote('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(engine.yaml_quote("plain"), '"plain"')

    def test_format_rate(self):
        self.assertEqual(engine.format_rate_kbps(512), "512kbps")
        self.assertIsNone(engine.format_rate_kbps(None))


class EngineProcessTest(unittest.TestCase):
    """Prozessbasierte Tests mit einem Fake-tt-Skript."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        fake = Path(self._tmp.name) / "tt"
        fake.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\">> \"$TT_FAKE_LOG\"\nsleep 30\n"
        )
        fake.chmod(0o755)
        self.fake = str(fake)
        os.environ["TT_FAKE_LOG"] = os.path.join(self._tmp.name, "log")
        os.environ["THROTL_RUN_DIR"] = self._tmp.name

    def tearDown(self):
        if "TT_FAKE_LOG" in os.environ:
            del os.environ["TT_FAKE_LOG"]
        if "THROTL_RUN_DIR" in os.environ:
            del os.environ["THROTL_RUN_DIR"]
        self._tmp.cleanup()

    def test_apply_starts_fake_tt(self):
        import time

        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg())
        self.assertTrue(engine_.is_running())
        time.sleep(0.2)  # dem Subprozess Zeit zum Log-Scheiben geben
        engine_.stop()
        log = Path(os.environ["TT_FAKE_LOG"]).read_text()
        lines = log.splitlines()
        self.assertEqual(lines[0], "enp34s0")
        self.assertTrue(lines[2].startswith("--delay"))

    def test_apply_disabled_no_proc(self):
        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg(global_={"enabled": False}))
        self.assertFalse(engine_.is_running())
        engine_.stop()

    def test_restart_on_reapply(self):
        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg())
        gen1 = engine_._generation
        engine_.apply(_cfg(global_={"download_limit": 5000}))
        self.assertTrue(engine_.is_running())
        self.assertGreater(engine_._generation, gen1)
        engine_.stop()

    def test_identical_reapply_is_noop(self):
        """Unveraenderte Config darf keinen teuren tt-Neustart ausloesen."""
        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg())
        gen1 = engine_._generation
        engine_.apply(_cfg())          # identisch -> kein Neustart
        self.assertEqual(engine_._generation, gen1)
        engine_.stop()

    def test_apply_metrics(self):
        """applies/restarts/failures + Dauer werden gezaehlt (fuer status)."""
        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg())
        engine_.apply(_cfg())          # no-op
        engine_.apply(_cfg(global_={"download_limit": 5000}))
        status = engine_.status()
        self.assertEqual(status["applies"], 2)
        self.assertEqual(status["restarts"], 2)
        self.assertEqual(status["apply_failures"], 0)
        self.assertIsNotNone(status["last_apply_seconds"])
        self.assertIsNotNone(status["avg_apply_seconds"])
        engine_.stop()

    def test_missing_command_raises(self):
        engine_ = engine.TrafficTollEngine("enp34s0", command="/nonexistent/tt")
        with self.assertRaises(RuntimeError):
            engine_.apply(_cfg())


class PriorityMappingTest(unittest.TestCase):
    def test_priority_ints_match_tt(self):
        from throtl.config import PRIORITY_TO_INT

        self.assertEqual(PRIORITY_TO_INT["kritisch"], 0)
        self.assertEqual(PRIORITY_TO_INT["niedrig"], 3)

    def test_global_priority_roundtrip(self):
        cfg = _cfg()
        for prio in ("kritisch", "hoch", "normal", "niedrig"):
            cfg["global"]["download_priority"] = prio
            text = engine.render_tt_config(cfg)
            self.assertIn(f"download-priority: {engine.priority_to_int(prio)}", text)


if __name__ == "__main__":
    unittest.main()


class EngineStatusDeadlockTest(unittest.TestCase):
    """Regression: status() darf sich nicht selbst blockieren (Lock-Deadlock)."""

    def test_status_returns_without_deadlock(self):
        import threading

        from throtl.engine import TrafficTollEngine

        eng = TrafficTollEngine("lo", command="/bin/true")
        result = {}

        def _call():
            result["status"] = eng.status()

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "status() hat sich selbst blockiert (Deadlock)")
        self.assertIn("running", result["status"])

    def test_is_running_and_status_no_deadlock(self):
        from throtl.engine import TrafficTollEngine

        eng = TrafficTollEngine("lo", command="/bin/true")
        self.assertFalse(eng.is_running())
        self.assertFalse(eng.status()["running"])


class TcCleanupTest(unittest.TestCase):
    """Vor jedem tt-Start muessen tc-Reste entfernt werden (sonst scheitert der
    QDisc-Aufbau: 'Exclusivity flag on' / 'Parent Qdisc doesn't exists')."""

    def test_cleanup_deletes_root_and_ingress(self):
        from unittest import mock

        from throtl.engine import TrafficTollEngine

        eng = TrafficTollEngine("enp0s3")
        with mock.patch("throtl.engine.subprocess.run") as run:
            eng._tc_cleanup()
        calls = [c.args[0] for c in run.call_args_list]
        self.assertIn(["tc", "qdisc", "del", "dev", "enp0s3", "root"], calls)
        self.assertIn(["tc", "qdisc", "del", "dev", "enp0s3", "ingress"], calls)

    def test_cleanup_skipped_in_dry_run(self):
        from unittest import mock

        from throtl.engine import TrafficTollEngine

        eng = TrafficTollEngine("enp0s3")
        eng.dry_run = True
        with mock.patch("throtl.engine.subprocess.run") as run:
            eng._tc_cleanup()
        run.assert_not_called()


class GracefulStopTest(unittest.TestCase):
    """tt wird per SIGINT beendet, damit sein atexit-Cleanup die QDiscs raeumt."""

    def test_stop_sends_sigint(self):
        import subprocess
        import tempfile
        import time
        from pathlib import Path

        from throtl.engine import TrafficTollEngine

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sig.txt"
            fake = Path(tmp) / "tt"
            # Python (wie das echte tt): der Handler laeuft sofort, bei einer
            # Shell wuerde ein trap erst nach dem Vordergrund-`sleep` greifen.
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import signal, sys, time\n"
                f"LOG = {str(log)!r}\n"
                "def _h(signum, frame):\n"
                "    open(LOG, 'a').write(('INT' if signum == signal.SIGINT else 'TERM') + '\\n')\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGINT, _h)\n"
                "signal.signal(signal.SIGTERM, _h)\n"
                "time.sleep(30)\n"
            )
            fake.chmod(0o755)
            eng = TrafficTollEngine("enp0s3", command=str(fake))
            eng.dry_run = True          # keine tc-Aufrufe im Test
            eng._proc = subprocess.Popen([str(fake)])
            time.sleep(0.4)
            eng.stop()
            self.assertTrue(log.exists(), "fake tt hat kein Signal erhalten")
            self.assertIn("INT", log.read_text())
