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


class RateUnitTest(unittest.TestCase):
    """tc rates must be expressed in BITS.

    iproute2 knows ``kbit`` (bits) and ``KBps`` (bytes) and matches suffixes
    regardless of case. An ``8000kbps`` thus lands as 8000 kilobytes/s in the
    class — 64 Mbit/s instead of 8 Mbit/s, i.e. 8x too high. Therefore only the
    bit form is written.
    """

    def test_uses_bits_not_bytes(self):
        self.assertEqual(engine.format_rate_kbps(8000), "8000kbit")
        self.assertEqual(engine.format_rate_kbps(1), "1kbit")
        self.assertNotIn("kbps", engine.format_rate_kbps(8000))

    def test_large_values_switch_to_gbit(self):
        # 1 Gbit/s = 1_000_000 kbit/s; the divisor must match the unit.
        self.assertEqual(engine.format_rate_kbps(8_000_000), "8gbit")
        self.assertEqual(engine.format_rate_kbps(1_500_000), "1.5gbit")
        self.assertEqual(engine.format_rate_kbps(1_000_000), "1gbit")
        self.assertEqual(engine.format_rate_kbps(999_999), "999999kbit")

    def test_none_stays_none(self):
        self.assertIsNone(engine.format_rate_kbps(None))


class RenderTest(unittest.TestCase):
    def test_disabled_renders_empty(self):
        cfg = _cfg(global_={"enabled": False})
        text = engine.render_tt_config(cfg)
        self.assertIn("disabled", text)
        self.assertNotIn("processes", text)
        self.assertNotIn("download:", text)

    def test_global_limits(self):
        cfg = _cfg(global_={"download_limit": 10_000, "upload_limit": 2000})
        text = engine.render_tt_config(cfg)
        self.assertIn("download: 10000kbit", text)
        self.assertIn("upload: 2000kbit", text)
        self.assertIn("download-minimum: 100kbit", text)
        self.assertIn("upload-minimum: 10kbit", text)
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
        self.assertIn("    download: 2048kbit", text)
        self.assertIn("    upload: 512kbit", text)
        self.assertIn("    download-priority: 0", text)  # kritisch
        self.assertIn("    upload-priority: 0", text)
        self.assertIn('      - exe: "/usr/lib/firefox/firefox"', text)
        self.assertIn("    recursive: true", text)
        self.assertIn('      - name: "steam"', text)

    def test_quoting(self):
        self.assertEqual(engine.yaml_quote('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(engine.yaml_quote("plain"), '"plain"')

    def test_window_rules_filtered(self):
        from datetime import datetime

        always = make_rule("Always", "name", "always", download_limit=1000)
        nights = make_rule(
            "Nights", "name", "nights", download_limit=2000,
            window={"days": ["mi"], "start": "20:00", "end": "02:00"})
        cfg = _cfg(processes=[always, nights])
        midday = datetime(2026, 9, 16, 12, 0)
        text = engine.render_tt_config(cfg, when=midday)
        self.assertIn('"Always"', text)
        self.assertNotIn('"Nights"', text)
        night = datetime(2026, 9, 16, 22, 0)
        self.assertIn('"Nights"', engine.render_tt_config(cfg, when=night))

    def test_format_rate(self):
        self.assertEqual(engine.format_rate_kbps(512), "512kbit")
        self.assertIsNone(engine.format_rate_kbps(None))


class EngineProcessTest(unittest.TestCase):
    """Process-based tests with a fake tt script."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        fake = Path(self._tmp.name) / "tt"
        fake.write_text(
            # ``exec``: the process IS the sleep afterwards, instead of a shell
            # waiting on its child. Without that SIGINT needs ~30 s instead of
            # immediately (every engine test cost several seconds that way).
            "#!/bin/sh\nprintf '%s\\n' \"$@\">> \"$TT_FAKE_LOG\"\nexec sleep 30\n"
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
        time.sleep(0.2)  # give the subprocess time to write the log
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
        """An unchanged config must not trigger an expensive tt restart."""
        engine_ = engine.TrafficTollEngine("enp34s0", command=self.fake)
        engine_.apply(_cfg())
        gen1 = engine_._generation
        engine_.apply(_cfg())          # identical -> no restart
        self.assertEqual(engine_._generation, gen1)
        engine_.stop()

    def test_apply_metrics(self):
        """applies/restarts/failures + duration are counted (for status)."""
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
    """Regression: status() must not block itself (lock deadlock)."""

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
        self.assertFalse(t.is_alive(), "status() blocked itself (deadlock)")
        self.assertIn("running", result["status"])

    def test_is_running_and_status_no_deadlock(self):
        from throtl.engine import TrafficTollEngine

        eng = TrafficTollEngine("lo", command="/bin/true")
        self.assertFalse(eng.is_running())
        self.assertFalse(eng.status()["running"])


class TcCleanupTest(unittest.TestCase):
    """Before every tt start, tc leftovers must be removed (otherwise the qdisc
    setup fails: 'Exclusivity flag on' / 'Parent Qdisc doesn't exists')."""

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


class YamlQuoteAndLogTest(unittest.TestCase):
    """Escaping and log cap (review: raw control chars / growing tt.log)."""

    def test_escapes_controls_that_would_break_the_yaml(self):
        out = engine.yaml_quote("a\rb\u2028c\x07d")
        self.assertNotIn("\r", out)
        self.assertNotIn("\u2028", out)
        self.assertNotIn("\x07", out)
        self.assertIn("\\r", out)
        self.assertIn("\\u2028", out)
        self.assertIn("\\u0007", out)

    def test_keeps_ordinary_text_and_quotes(self):
        self.assertEqual(engine.yaml_quote("Steam"), '"Steam"')
        self.assertEqual(engine.yaml_quote('say "hi"'), '"say \\"hi\\""')

    def test_log_is_truncated_when_it_grows_too_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tt.log"
            eng = engine.TrafficTollEngine("lo", command="/bin/true")
            eng._stderr_path = str(path)
            eng._LOG_MAX_BYTES = 4096
            for i in range(400):
                eng._append_stderr_log("x" * 64 + str(i))
            # After truncation only the rest remains — never unlimited.
            self.assertLessEqual(path.stat().st_size, eng._LOG_MAX_BYTES + 128)


class GracefulStopTest(unittest.TestCase):
    """tt is stopped via SIGINT so its atexit cleanup removes the qdiscs."""

    def test_stop_sends_sigint(self):
        import subprocess
        import tempfile
        import time
        from pathlib import Path

        from throtl.engine import TrafficTollEngine

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sig.txt"
            fake = Path(tmp) / "tt"
            # Python (like the real tt): the handler runs immediately; with a
            # shell a trap would only fire after the foreground sleep.
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
            eng.dry_run = True          # no tc calls in the test
            eng._proc = subprocess.Popen([str(fake)])
            time.sleep(0.4)
            eng.stop()
            self.assertTrue(log.exists(), "fake tt received no signal")
            self.assertIn("INT", log.read_text())
