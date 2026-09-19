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

from throtl import daemon
from throtl.config import PRIORITY_NAMES, priority_to_int
from throtl.engine import SimEngine
from throtl.units import format_rate, parse_rate


def _gi_available():
    """PyGObject verfuegbar? (nur /usr/bin/python3 hat gi, mise-python nicht)."""
    try:
        import gi  # noqa: F401

        return True
    except Exception:
        return False


def _display_available():
    """GTK muss initialisiert sein, sonst ist Gdk.Display immer None."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        Gtk.init_check()
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


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class ProcessTableSortTest(unittest.TestCase):
    """Sortierung: Standard = Download absteigend; Kopfklick aendert die Spalte."""

    class _Gui:
        state = {"processes": [], "rules": []}
        client = None
        def show_error(self, m): pass
        def show_info(self, m): pass

    def _table(self):
        from throtl.gui.process_pane import ProcessTable
        return ProcessTable(self._Gui(), unit="mBs")

    @staticmethod
    def _state():
        return {"rules": [], "processes": [
            {"pid": "1", "name": "/usr/bin/slow", "download": 10.0, "upload": 900.0},
            {"pid": "2", "name": "/usr/bin/fast", "download": 5000.0, "upload": 20.0},
            {"pid": "3", "name": "/usr/bin/mid", "download": 700.0, "upload": 300.0},
        ]}

    def test_default_sort_is_download_desc(self):
        t = self._table()
        t.set_state(self._state())
        self.assertEqual(t.visible_order(), ["2", "3", "1"])

    def test_sort_by_upload(self):
        t = self._table()
        t.set_state(self._state())
        t.set_sort("upload", True)
        self.assertEqual(t.visible_order(), ["1", "3", "2"])

    def test_sort_by_name_asc(self):
        t = self._table()
        t.set_state(self._state())
        t.set_sort("name", False)
        self.assertEqual(t.visible_order(), ["2", "3", "1"])  # fast, mid, slow

    def test_header_click_toggles_direction(self):
        t = self._table()
        t.set_state(self._state())
        t._on_sort_clicked(None, "download")   # schon aktiv -> Richtung kippt
        self.assertEqual(t.visible_order(), ["1", "3", "2"])
        t._on_sort_clicked(None, "download")
        self.assertEqual(t.visible_order(), ["2", "3", "1"])

    def test_table_expands(self):
        t = self._table()
        self.assertTrue(t.get_vexpand())



@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class ProcessTableGroupedTest(unittest.TestCase):
    """Bei gruppierten 'apps' erscheint eine Zeile pro Anwendung."""

    class _Gui:
        state = {"processes": [], "rules": []}
        client = None
        def show_error(self, m): pass
        def show_info(self, m): pass

    def test_grouped_rows_and_label(self):
        from throtl.gui.process_pane import ProcessTable

        t = ProcessTable(self._Gui(), unit="mBs")
        t.set_state({
            "rules": [],
            "apps": [
                {"name": "legendary", "exe": "python3", "download": 10151.0,
                 "upload": 300.0, "pids": ["1", "2", "3"], "pid_count": 3,
                 "unattributed": False, "rule_name": None},
                {"name": "curl", "exe": "/usr/bin/curl", "download": 10.0,
                 "upload": 1.0, "pids": ["9"], "pid_count": 1,
                 "unattributed": False, "rule_name": None},
            ],
        })
        self.assertEqual(t.row_count(), 2)
        self.assertEqual(t.visible_order(), ["legendary", "curl"])  # Download desc
        # Prozess-Spalte zeigt die Anzahl der PIDs
        row = t._rows["legendary"]
        pid_label = row.box.get_first_child().get_first_child()
        self.assertEqual(pid_label.get_text(), "3 pids")

    def test_grouped_sorted_by_summed_rate(self):
        from throtl.gui.process_pane import ProcessTable

        t = ProcessTable(self._Gui(), unit="mBs")
        t.set_state({
            "rules": [],
            "apps": [
                {"name": "a", "exe": "/usr/bin/a", "download": 5.0, "upload": 0.0,
                 "pids": ["1"], "pid_count": 1, "unattributed": False},
                {"name": "b", "exe": "/usr/bin/b", "download": 900.0, "upload": 0.0,
                 "pids": ["2"], "pid_count": 1, "unattributed": False},
            ],
        })
        self.assertEqual(t.visible_order(), ["b", "a"])

    def test_editing_grouped_row_targets_the_app(self):
        """Editieren einer gruppierten Zeile muss den App-Key treffen und die
        Regel aus Name/exe ableiten (nicht die angezeigte PID)."""
        from throtl.gui.process_pane import ProcessTable

        calls = []

        class _Client:
            def call(self, method, params):
                calls.append((method, params))
                return params

        class _Gui:
            client = _Client()

            def show_error(self, m): pass
            def show_info(self, m): pass

        t = ProcessTable(_Gui(), unit="mBs")
        t.set_state({"rules": [], "apps": [
            {"name": "legendary", "exe": "python3", "download": 10.0,
             "upload": 1.0, "pids": ["1", "2"], "pid_count": 2,
             "unattributed": False},
        ]})
        t._set_rule_field("legendary", "download_limit", 1234)
        self.assertEqual(calls[0][0], "set_process")
        params = calls[0][1]
        self.assertEqual(params["match_type"], "name")
        self.assertEqual(params["match_value"], "legendary")
        self.assertEqual(params["download_limit"], 1234)


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class BandwidthGraphTest(unittest.TestCase):
    """Auto-Scroll, Zeitfenster-Auswahl und Zeit-Achse des Graphen."""

    @staticmethod
    def _adjustment(upper, page, value):
        class _Adj:
            def get_upper(self): return upper
            def get_page_size(self): return page
            def get_value(self): return value
        return _Adj()

    def test_window_choices_map_to_seconds(self):
        from throtl.gui.graph import WINDOW_CHOICES, BandwidthGraph

        self.assertEqual(WINDOW_CHOICES[BandwidthGraph._window_index(0)][0], "All")
        self.assertEqual(BandwidthGraph._window_index(60), 1)
        self.assertEqual(BandwidthGraph._window_index(30), 0)

    def test_push_keeps_time_span_and_autoscroll_toggle(self):
        from throtl.gui.graph import BandwidthGraph

        g = BandwidthGraph(window_seconds=60)
        for i in range(90):
            g.push(1000.0, 100.0, now=1000.0 + i)
        self.assertEqual(g._time_span(), 89.0)

        g._autoscroll = True
        g._on_scrolled(self._adjustment(2000, 500, 1500))   # am Ende
        self.assertTrue(g._autoscroll)
        g._on_scrolled(self._adjustment(2000, 500, 0))      # zurueckgescrollt
        self.assertFalse(g._autoscroll)
        g._on_scrolled(self._adjustment(2000, 500, 1500))   # wieder am Ende
        self.assertTrue(g._autoscroll)

    def test_hover_index_uses_timestamps(self):
        from throtl.gui.graph import BandwidthGraph

        g = BandwidthGraph(window_seconds=60)
        for i in range(60):
            g.push(1000.0, 100.0, now=1000.0 + i)
        # ohne Allocation ist die Breite 0; Mitte der Samples muss trotzdem
        # ein gueltiger Index in der Naehe der Mitte sein
        index = g._index_at(0.0)
        self.assertIsNotNone(index)
        self.assertTrue(0 <= index < len(g._samples))


if __name__ == "__main__":
    unittest.main()
