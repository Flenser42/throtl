"""GUI-Tests: testbare Logik (Formatierung, Interaktion) ohne Display.

Widget-Instanziierung wird uebersprungen, wenn kein GTK-Display verfuegbar ist
(Headless-Sandbox/CI). Die Logik-Teile (Einheiten, Prioritaets-Mapping,
Limit-Parsing) werden immer getestet.
"""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from throtl.units import format_rate, parse_rate
from throtl.config import PRIORITY_NAMES, priority_to_int
from throtl import daemon, protocol
from throtl.engine import SimEngine


def _gi_available():
    """PyGObject verfuegbar? (nur /usr/bin/python3 hat gi, mise-python nicht)."""
    try:
        import gi  # noqa: F401

        return True
    except Exception:
        return False


def _display_available():
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk

        return Gdk.Display.get_default() is not None
    except Exception:
        return False


class RateFormatTest(unittest.TestCase):
    def test_auto(self):
        self.assertEqual(format_rate(500, "auto"), "500.0 kbit/s")
        self.assertEqual(format_rate(5000, "auto"), "5.0 Mbit/s")

    def test_units(self):
        self.assertEqual(format_rate(1500, "kbps"), "1500.0 kbit/s")
        self.assertEqual(format_rate(1500, "kBs"), "187.5 KB/s")

    def test_parse(self):
        self.assertEqual(parse_rate("2mbps"), 2000)
        self.assertEqual(parse_rate("512kbps"), 512)
        self.assertIsNone(parse_rate(None))


class PriorityMappingTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(priority_to_int("kritisch"), 0)
        self.assertEqual(priority_to_int("niedrig"), 3)
        self.assertEqual(list(PRIORITY_NAMES),
                         ["kritisch", "hoch", "normal", "niedrig"])


@unittest.skipUnless(_gi_available(), "PyGObject (gi) fehlt - /usr/bin/python3 nutzen")
class GuiClientStateTest(unittest.TestCase):
    """Testet den GuiClient gegen einen echten Daemon (ohne GTK-Display).

    Der GuiClient braucht zwar GLib (fuer idle_add), wir testen aber nur die
    RPC-Schicht — das Polling selbst wird hier bewusst nicht gebraucht.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self._tmp.name, "daemon.sock")
        self.config_dir = os.path.join(self._tmp.name, "cfg")
        os.environ["THROTL_CONFIG_DIR"] = self.config_dir
        self.daemon = daemon.Daemon(
            socket_path=self.socket_path,
            config_dir=self.config_dir,
            engine=SimEngine("test0"),
            interval=0.3,
            monitor_factory=None,  # kein Monitor im Test -> snapshot leer
        )
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._wait_socket()

    def _run(self):
        self.daemon.start()
        self.daemon.serve_forever()

    def _wait_socket(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                return
            time.sleep(0.01)
        raise TimeoutError("Socket fehlt")

    def tearDown(self):
        self.daemon.shutdown()
        os.environ.pop("THROTL_CONFIG_DIR", None)
        self._tmp.cleanup()

    def test_status_and_config_rpc(self):
        from throtl.gui.client import GuiClient

        gui = GuiClient(socket_path=self.socket_path)
        gui.connect()
        try:
            status = gui.call("status")
            self.assertTrue(status["simulated"])
            cfg = gui.call("get_config")
            self.assertEqual(cfg["global"]["enabled"], True)
        finally:
            gui.shutdown()


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class GuiWidgetTest(unittest.TestCase):
    def test_priority_dropdown_mapping(self):
        from throtl.gui.widgets import PriorityDropdown

        dd = PriorityDropdown()
        dd.set_priority_name("hoch")
        self.assertEqual(dd.get_priority_name(), "hoch")

    def test_rate_entry_empty_means_unlimited(self):
        from throtl.units import parse_rate_lenient

        self.assertIsNone(parse_rate_lenient(""))
        self.assertIsNone(parse_rate_lenient("unlimited"))
        self.assertIsNone(parse_rate_lenient("unbegrenzt"))
        self.assertEqual(parse_rate_lenient("2mbps"), 2000)
        self.assertEqual(parse_rate_lenient("512 kbps"), 512)


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class ProcessTableInPlaceTest(unittest.TestCase):
    """set_state() darf Zeilen NICHT neu aufbauen (Fokus-/Teleing-Ueberleben)."""

    def test_rows_are_reused_between_poll_updates(self):
        from throtl.gui.process_pane import ProcessTable

        # lightweight gui stub mit state
        class _Gui:
            state = {"processes": [], "rules": []}
            client = None
            def show_error(self, m): pass
            def show_info(self, m): pass

        table = ProcessTable(_Gui(), unit="mBs")
        state1 = {
            "processes": [
                {"pid": "10", "name": "/usr/bin/foo", "download": 1000.0, "upload": 50.0},
                {"pid": "20", "name": "/usr/bin/bar", "download": 200.0, "upload": 30.0},
            ],
            "rules": [],
        }
        state2 = {
            "processes": [
                {"pid": "10", "name": "/usr/bin/foo", "download": 1200.0, "upload": 60.0},
                {"pid": "20", "name": "/usr/bin/bar", "download": 210.0, "upload": 31.0},
            ],
            "rules": [],
        }
        table.set_state(state1)
        first_rows = set(table._rows.keys())
        table.set_state(state2)
        self.assertEqual(set(table._rows.keys()), first_rows)  # gleiche pid-menge, nichts neu
        self.assertEqual(table.row_count(), 2)

    def test_row_drops_when_process_gone(self):
        from throtl.gui.process_pane import ProcessTable
        class _Gui:
            state = {"processes": [], "rules": []}
            client = None
            def show_error(self, m): pass
            def show_info(self, m): pass
        table = ProcessTable(_Gui(), unit="mBs")
        table.set_state({"processes": [{"pid": "1", "name": "/x", "download": 1.0, "upload": 1.0}], "rules": []})
        self.assertEqual(table.row_count(), 1)
        table.set_state({"processes": [], "rules": []})
        self.assertEqual(table.row_count(), 0)


if __name__ == "__main__":
    unittest.main()
