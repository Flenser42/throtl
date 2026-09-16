import io
import threading
import time
import unittest

from throtl import monitor
from throtl.monitor import NethogsMonitor, TraceParser, parse_trace


# Realistisches nethogs-`-t`-Trace (kB/s, Anzeige-Modus -v 1).
# Format je Zeile: <name>[/<cmdline>]/<pid>/<uid>\t<sent>\t<recv>
TRACE = (
    b"Refreshing:\n"
    b"/usr/lib/firefox/firefox/1457/1000\t2.0\t300.0\n"
    b"steam/2886/1000\t0.5\t10.2\n"
    b"java -jar JDownloader.jar/4001/1000\t80.0\t40.0\n"
    b"Refreshing:\n"
    b"/usr/lib/firefox/firefox/1457/1000\t3.0\t250.0\n"
    b"steam/2886/1000\t0.1\t12.0\n"
    b"Refreshing:\n"
    b"/usr/lib/firefox/firefox/1457/1000\t1.0\t100.0\n"
)


class ParseTraceTest(unittest.TestCase):
    def test_parse_valid(self):
        result = monitor.parse_trace("/usr/bin/foo/123/1000\t1.5\t2.5\n")
        self.assertEqual(result, ("/usr/bin/foo", "123", "1000", 1.5, 2.5))

    def test_parse_cmdline(self):
        result = monitor.parse_trace("java -jar x.jar/99/1000\t3.0\t4.0\n")
        self.assertEqual(result[0], "java -jar x.jar")
        self.assertEqual(result[1], "99")

    def test_header_and_junk(self):
        self.assertIsNone(monitor.parse_trace("Refreshing:\n"))
        self.assertIsNone(monitor.parse_trace("Unknown connection: x"))
        self.assertIsNone(monitor.parse_trace(""))
        self.assertIsNone(monitor.parse_trace("kein-tab"))

    def test_bad_numbers(self):
        self.assertIsNone(monitor.parse_trace("x/1/2\tabc\tdef"))


class TraceParserTickTest(unittest.TestCase):
    def test_ticks(self):
        parser = TraceParser()
        ticks = parser.feed(TRACE.decode())
        ticks.append(parser.finish())
        self.assertEqual(len(ticks), 3)
        tick = ticks[0]
        self.assertIn("1457", tick)
        self.assertEqual(tick["1457"]["name"], "/usr/lib/firefox/firefox")
        self.assertEqual(tick["1457"]["download"], monitor._kBs_to_kbit(300.0))
        self.assertEqual(tick["1457"]["upload"], monitor._kBs_to_kbit(2.0))

    def test_recv_is_download(self):
        parser = TraceParser()
        ticks = parser.feed(TRACE.decode())
        ticks.append(parser.finish())
        # JDownloader: sent 80 (upload), recv 40 (download)
        jd = ticks[0]["4001"]
        self.assertEqual(jd["upload"], monitor._kBs_to_kbit(80.0))
        self.assertEqual(jd["download"], monitor._kBs_to_kbit(40.0))

    def test_process_disappears(self):
        parser = TraceParser()
        ticks = parser.feed(TRACE.decode())
        ticks.append(parser.finish())
        # im letzten Fenster gibt es nur firefox
        self.assertNotIn("2886", ticks[2])
        self.assertNotIn("4001", ticks[2])
        self.assertIn("1457", ticks[2])


class NethogsMonitorTest(unittest.TestCase):
    def test_processes_injected_stream(self):
        source = io.StringIO(TRACE.decode())
        mon = NethogsMonitor("enp34s0", inject=source)
        mon.start()
        deadline = time.monotonic() + 2
        latest = {}
        while time.monotonic() < deadline:
            snapshot = mon.snapshot()
            if snapshot.get("1457"):
                latest = snapshot
                break
            time.sleep(0.02)
        mon.stop()
        self.assertIn("1457", latest)
        self.assertEqual(latest["1457"]["name"], "/usr/lib/firefox/firefox")

    def test_default_cmd_is_absolute(self):
        """Damit ein systemd-Dienst nethogs auch bei minimalem PATH findet."""
        mon = NethogsMonitor("wlo1", interval=1.0)
        self.assertTrue(mon.cmd.startswith("/") or mon.cmd == "nethogs")

    def test_missing_binary_raises(self):
        mon = NethogsMonitor("enp34s0", cmd="/nonexistent/nethogs")
        with self.assertRaises(RuntimeError):
            mon.start()

    def test_build_argv(self):
        mon = NethogsMonitor("wlo1", interval=2.0, cmd="nethogs")
        self.assertEqual(mon._build_argv(),
                         ["nethogs", "-t", "-d", "2.0", "-C", "wlo1"])

    def test_build_argv_skips_auto_device(self):
        mon = NethogsMonitor("auto", interval=1.0, cmd="nethogs")
        self.assertEqual(mon._build_argv(), ["nethogs", "-t", "-d", "1.0", "-C"])

    def test_build_argv_without_udp(self):
        mon = NethogsMonitor("wlo1", interval=1.0, cmd="nethogs", capture_udp=False)
        self.assertEqual(mon._build_argv(), ["nethogs", "-t", "-d", "1.0", "wlo1"])

    def test_unattributable_traffic_is_kept(self):
        """Nicht zuordenbarer Traffic darf NICHT verschwinden."""
        got = monitor.parse_trace("unknown TCP/0/0\t1.5\t2.5\n")
        self.assertEqual(got[0], monitor.UNATTRIBUTED_NAME)
        self.assertEqual(got[1], monitor.UNATTRIBUTED_PID)
        self.assertEqual(got[3], 1.5)
        self.assertEqual(got[4], 2.5)
        # pid 0 ohne "unknown" ebenfalls als nicht zuordenbar markieren
        self.assertEqual(monitor.parse_trace("/usr/bin/foo/0/1000\t1\t2\n")[1],
                         monitor.UNATTRIBUTED_PID)

    def test_auto_device(self):
        # detect_default_interface aus config; hier nur smoke test, dass der
        # Monitor mit einem Device konstruierbar ist.
        mon = NethogsMonitor("lo", inject=io.StringIO(""))
        mon.start()
        mon.stop()


if __name__ == "__main__":
    unittest.main()
