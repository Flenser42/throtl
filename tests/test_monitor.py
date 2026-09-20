import io
import time
import unittest

from throtl import monitor
from throtl.monitor import NethogsMonitor, TraceParser

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
                         ["nethogs", "-t", "-d", "2.0", "-C", "-l", "wlo1"])

    def test_build_argv_skips_auto_device(self):
        mon = NethogsMonitor("auto", interval=1.0, cmd="nethogs")
        self.assertEqual(mon._build_argv(), ["nethogs", "-t", "-d", "1.0", "-C", "-l"])

    def test_build_argv_without_udp(self):
        mon = NethogsMonitor("wlo1", interval=1.0, cmd="nethogs", capture_udp=False)
        self.assertEqual(mon._build_argv(), ["nethogs", "-t", "-d", "1.0", "-l", "wlo1"])

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

    def test_dead_process_is_detected_and_reaped(self):
        """Stirbt nethogs, muss is_alive() False werden und der Prozess
        gereapt werden (kein Zombie), inklusive Grund in last_error."""
        mon = NethogsMonitor("lo", cmd="/bin/true", capture_udp=False)
        mon.start()
        deadline = time.monotonic() + 3
        while mon.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(mon.is_alive())
        self.assertIsNotNone(mon.last_error)
        mon.stop()  # darf nicht werfen
        self.assertFalse(mon.is_alive())

    def test_kib_per_second_conversion(self):
        # nethogs rechnet in 1024er-Schritten: 1 KiB/s = 8.192 kbit/s.
        self.assertAlmostEqual(monitor._kBs_to_kbit(1000.0), 8192.0, places=3)

    def test_injected_stream_reports_alive_until_stopped(self):
        mon = NethogsMonitor("lo", inject=io.StringIO(TRACE.decode()))
        mon.start()
        self.assertTrue(mon.is_alive())
        mon.stop()
        self.assertFalse(mon.is_alive())


if __name__ == "__main__":
    unittest.main()


class PrettyAppNameTest(unittest.TestCase):
    """nethogs -l liefert die Kommandozeile -> lesbaren App-Namen ableiten."""

    def test_script_apps(self):
        from throtl.monitor import pretty_app_name

        self.assertEqual(
            pretty_app_name("python3 ./legendary install CrabEA --platform Windows -y"),
            "legendary")
        self.assertEqual(pretty_app_name("python3 /opt/app/main.py --serve"), "main")
        self.assertEqual(pretty_app_name("python3 -m http.server"), "http.server")
        self.assertEqual(pretty_app_name("python3 -u /home/x/udp_load.py"), "udp_load")
        self.assertEqual(pretty_app_name("node /usr/lib/foo/server.js"), "server")

    def test_native_apps(self):
        from throtl.monitor import pretty_app_name

        self.assertEqual(pretty_app_name("/usr/bin/curl -s -o /dev/null"), "curl")
        self.assertEqual(pretty_app_name("/usr/lib/electron43/electron --type=utility"),
                         "electron")
        self.assertEqual(pretty_app_name("java -jar JDownloader.jar"), "JDownloader")
        self.assertEqual(pretty_app_name("reasonix-desktop"), "reasonix-desktop")

    def test_edge_cases(self):
        from throtl.monitor import pretty_app_name

        self.assertEqual(pretty_app_name(""), "?")
        self.assertEqual(pretty_app_name("python3"), "python3")
