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

    def test_unit_change_does_not_clobber_edit_while_latched(self):
        import time

        t = self._table()
        t.set_state({"rules": [{
            "key": "name:x", "name": "x", "match_type": "name",
            "match_value": "x", "download_limit": 5000, "upload_limit": None,
            "priority": "normal", "recursive": False}],
            "apps": [{"name": "x", "exe": "/x", "download": 1.0,
                      "upload": 1.0, "pids": ["1"], "pid_count": 1,
                      "unattributed": False}]})
        row = t._rows["x"]
        row.dl.set_text("7")
        t._edit_latch_until = time.monotonic() + 30
        t.set_unit("kbps")  # wuerde sonst den Regelwert eintragen
        self.assertEqual(row.dl.get_text(), "7")

    def test_sort_change_callback_fires(self):
        from throtl.gui.process_pane import ProcessTable

        saved = []
        t = ProcessTable(self._Gui(), unit="mBs",
                         on_sort_change=lambda key, desc: saved.append((key, desc)))
        t.set_state(self._state())
        t._on_sort_clicked(None, "upload")
        self.assertEqual(saved[-1][0], "upload")
        # erneuter Klick kippt die Richtung und meldet das ebenfalls
        t._on_sort_clicked(None, "upload")
        self.assertEqual(saved[-1][0], "upload")
        self.assertNotEqual(saved[0][1], saved[-1][1])



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
        self.assertEqual(pid_label.get_text(), "3 processes")

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

    def test_short_history_auto_fits(self):
        """Weniger Historie als das Fenster -> auf volle Breite ziehen."""
        from throtl.gui.graph import MIN_CANVAS, PAD, BandwidthGraph

        g = BandwidthGraph(window_seconds=60)
        for i in range(4):
            g.push(1000.0, 100.0, now=1000.0 + i)
        # span = 3 s < window -> pps = (viewport - 2*PAD) / span
        self.assertEqual(g._area.get_size_request()[0], int(MIN_CANVAS))
        self.assertAlmostEqual(g._pps, (MIN_CANVAS - 2 * PAD) / 3.0, places=2)

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


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class StatsDialogTest(unittest.TestCase):
    def test_builds_and_draws_empty_series(self):
        from throtl.gui.app import StatsDialog

        class _Gui:
            def call(self, method, params=None):
                if method == "get_stats":
                    return {"window": "minute", "apps": [], "totals": {}}
                if method == "get_stats_history":
                    return {"window": "minute", "series": []}
                return {}

            def call_async(self, *args, **kwargs):
                return None

        dialog = StatsDialog(None, _Gui())
        self.assertIsNotNone(dialog.graph)
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        self.assertIsInstance(dialog, Adw.Dialog)
        dialog.force_close()


@unittest.skipUnless(_gi_available(), "PyGObject nicht verfuegbar")
class PlainErrorTest(unittest.TestCase):
    """Rohe Daemon-Meldungen werden in Klartext uebersetzt, nie erfunden."""

    def test_transport_error_offers_retry(self):
        from throtl.gui.app import _plain_error

        title, details, offline = _plain_error("Connection refused")
        self.assertTrue(offline)
        self.assertIn("Reconnecting", title)
        self.assertEqual(details, "Connection refused")

    def test_group_hint_keeps_the_daemon_diagnosis(self):
        from throtl.gui.app import _plain_error

        title, details, offline = _plain_error(
            "Socket not accessible.\nRun: newgrp throtl")
        self.assertFalse(offline)
        self.assertEqual(title, "Socket not accessible.")
        self.assertIn("newgrp", details)

    def test_unknown_message_passes_through_unchanged(self):
        from throtl.gui.app import _plain_error

        title, details, offline = _plain_error("Disk on fire")
        self.assertEqual(title, "Disk on fire")
        self.assertEqual(details, "")
        self.assertFalse(offline)


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class KeyboardShortcutTest(unittest.TestCase):
    """Tastaturbedienung: Kuerzel registriert, Esc verbraucht nur was es loest."""

    # Jede Gtk.Application braucht eine eigene ID — sonst kollidiert der
    # zweite Test der Klasse beim Registrieren.
    _seq = 0

    @staticmethod
    def _window():
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        from throtl.gui.app import ThrotlWindow

        class _Gui:
            connected = True

            def call(self, method, params=None, timeout=10):
                return {}

            def call_async(self, method, params=None, **kwargs):
                pass

            def shutdown(self):
                pass

        KeyboardShortcutTest._seq += 1
        app = Adw.Application(
            application_id=f"io.github.throtl.keytest{KeyboardShortcutTest._seq}")
        app.register(None)
        return ThrotlWindow(app, _Gui())

    def test_window_registers_shortcuts(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        win = self._window()
        try:
            self.assertIsInstance(win.shortcuts, Gtk.ShortcutController)
            # Ctrl+F, Escape und Ctrl+1 … Ctrl+9 (ShortcutController ist ein
            # GListModel, daher get_n_items).
            self.assertGreaterEqual(win.shortcuts.get_n_items(), 11)
        finally:
            win.destroy()

    def test_escape_only_consumes_when_a_filter_is_set(self):
        win = self._window()
        try:
            self.assertFalse(win._clear_filter())
            win.search_entry.set_text("firefox")
            self.assertTrue(win._clear_filter())
            self.assertEqual(win.search_entry.get_text(), "")
        finally:
            win.destroy()


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class ProfileReloadTest(unittest.TestCase):
    def test_reload_profiles_does_not_activate(self):
        """Das Befuellen des Dropdowns darf kein activate_profile ausloesen."""
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        from throtl.gui.app import ThrotlWindow

        calls = []

        class _Gui:
            connected = True

            def call(self, method, params=None, timeout=10):
                if method == "list_profiles":
                    return {"profiles": ["Standard", "Uni"], "active": "Uni"}
                return {}

            def call_async(self, method, params=None, **kwargs):
                calls.append(method)

            def shutdown(self):
                pass

        app = Adw.Application(application_id="io.github.throtl.reloadtest")
        app.register(None)
        win = ThrotlWindow(app, _Gui())
        try:
            win._reload_profiles()
            self.assertNotIn("activate_profile", calls)
        finally:
            win.destroy()


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class ProcessTableFilterTest(unittest.TestCase):
    """Das Suchfeld filtert die Prozessliste (Name/exe, case-insensitiv)."""

    class _Gui:
        client = None

        def show_error(self, m): pass
        def show_info(self, m): pass

    @staticmethod
    def _state():
        return {"rules": [], "apps": [
            {"name": "firefox", "exe": "/usr/lib/firefox/firefox",
             "download": 100.0, "upload": 10.0, "pids": ["1"],
             "pid_count": 1, "unattributed": False},
            {"name": "steam", "exe": "/usr/bin/steam",
             "download": 50.0, "upload": 5.0, "pids": ["2"],
             "pid_count": 1, "unattributed": False},
        ]}

    def _table(self):
        from throtl.gui.process_pane import ProcessTable
        return ProcessTable(self._Gui(), unit="mBs")

    def test_filter_shows_only_matching(self):
        table = self._table()
        table.set_state(self._state())
        table.set_filter("fire")
        self.assertEqual(set(table._rows.keys()), {"firefox"})

    def test_filter_matches_exe(self):
        table = self._table()
        table.set_state(self._state())
        table.set_filter("steam")
        self.assertEqual(set(table._rows.keys()), {"steam"})

    def test_clearing_filter_restores_all(self):
        table = self._table()
        table.set_state(self._state())
        table.set_filter("fire")
        table.set_filter("")
        self.assertEqual(set(table._rows.keys()), {"firefox", "steam"})

    def test_no_match_shows_placeholder(self):
        table = self._table()
        table.set_state(self._state())
        table.set_filter("zzzz")
        self.assertEqual(table.row_count(), 0)
        self.assertIsNotNone(table._no_match)


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class WindowButtonTest(unittest.TestCase):
    """Zeitfenster-Button pro Zeile markiert gesetzte Fenster (armed)."""

    class _Gui:
        client = None

        def show_error(self, m): pass
        def show_info(self, m): pass

    def _table(self, window):
        from throtl.gui.process_pane import ProcessTable
        table = ProcessTable(self._Gui(), unit="mBs")
        rule = {"key": "name:x", "name": "x", "match_type": "name",
                "match_value": "x", "download_limit": None,
                "upload_limit": None, "priority": "normal",
                "recursive": False, "window": window}
        table.set_state({"rules": [rule], "apps": [
            {"name": "x", "exe": "/x", "download": 1.0, "upload": 1.0,
             "pids": ["1"], "pid_count": 1, "unattributed": False}]})
        return table

    @staticmethod
    def _button(roww):
        # Zelle 1 ist die Process-Spalte (Box: Label + Zeitfenster-Button)
        name_box = roww.box.get_first_child().get_next_sibling()
        return name_box.get_last_child()

    def test_not_armed_without_window(self):
        table = self._table(None)
        self.assertNotIn("armed", self._button(table._rows["x"]).get_css_classes())

    def test_armed_with_window(self):
        table = self._table({"days": [0], "start": "08:00", "end": "12:00"})
        self.assertIn("armed", self._button(table._rows["x"]).get_css_classes())


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class RuleWindowDialogTest(unittest.TestCase):
    """Logik des Zeitfenster-Dialogs (Speichern/Validierung/Loeschen)."""

    def _dialog(self, window=None):
        from throtl.gui.process_pane import RuleWindowDialog
        saved = []
        dialog = RuleWindowDialog(
            None, window, "app", lambda key, win: saved.append((key, win)))
        return dialog, saved

    def test_save_with_default_all_days(self):
        dialog, saved = self._dialog()
        dialog._start.set_text("20:00")
        dialog._end.set_text("00:00")
        dialog._on_response(None, "save")
        self.assertEqual(saved[0][0], "app")
        self.assertEqual(saved[0][1]["days"], list(range(7)))
        self.assertEqual(saved[0][1]["start"], "20:00")
        self.assertEqual(saved[0][1]["end"], "00:00")

    def test_selected_days_only(self):
        dialog, saved = self._dialog()
        for index, button in dialog._day_buttons.items():
            button.set_active(index == 2)
        dialog._start.set_text("08:00")
        dialog._end.set_text("09:00")
        dialog._on_response(None, "save")
        self.assertEqual(saved[0][1]["days"], [2])

    def test_invalid_time_is_not_saved(self):
        dialog, saved = self._dialog()
        dialog._start.set_text("kaputt")
        dialog._end.set_text("09:00")
        self.assertIsNone(dialog._current_window())
        dialog._on_response(None, "save")
        self.assertEqual(saved, [])

    def test_clear_removes_window(self):
        dialog, saved = self._dialog(
            {"days": [0], "start": "08:00", "end": "09:00"})
        dialog._on_response(None, "clear")
        self.assertEqual(saved, [("app", None)])

    def test_prefilled_from_existing_window(self):
        dialog, _ = self._dialog(
            {"days": [5, 6], "start": "10:00", "end": "23:00"})
        self.assertEqual(dialog._start.get_text(), "10:00")
        self.assertTrue(dialog._day_buttons[5].get_active())
        self.assertFalse(dialog._day_buttons[0].get_active())


class _FakeWindowGui:
    """Fake GuiClient fuer komplette Fenster-Smoke-Tests."""

    connected = True

    def __init__(self):
        self.calls = []

    def call(self, method, params=None, timeout=10):
        self.calls.append((method, params))
        if method == "get_config":
            return {"unit": "mBs", "start_profile": None,
                    "global": {"enabled": True, "download_limit": None,
                               "upload_limit": None,
                               "download_priority": "normal"}}
        if method == "list_processes":
            return {"interface": "eth0", "enabled": True, "processes": [],
                    "apps": [], "rules": [],
                    "global": {"download": 1000.0, "upload": 100.0},
                    "attributed": {"download": 900.0, "upload": 90.0}}
        if method == "status":
            return {"monitoring": True, "daemon": "0.5.0"}
        if method == "list_profiles":
            return {"profiles": ["Standard"], "active": "Standard"}
        if method == "get_budgets":
            return {"enabled": True, "entries": []}
        return {}

    def call_async(self, method, params=None, **kwargs):
        self.calls.append((method, params))

    def start_polling(self, *args, **kwargs):
        pass

    def shutdown(self):
        pass


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class WindowSmokeTest(unittest.TestCase):
    """Baut das komplette Fenster und ruft die wichtigsten Handler auf."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._tmp.cleanup()

    def _window(self, gui):
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        from throtl.gui.app import ThrotlWindow

        app = Adw.Application(application_id=f"io.github.throtl.smoke{id(self)}")
        app.register(None)
        return ThrotlWindow(app, gui)

    def test_handlers_do_not_crash(self):
        gui = _FakeWindowGui()
        win = self._window(gui)
        try:
            win.reload()
            win.search_entry.set_text("fire")
            win._on_search(win.search_entry)
            win.search_entry.set_text("")
            win._on_search(win.search_entry)
            win._on_toggle(win.toggle_switch, False)
            win._set_unit("kBs")
            win._on_global_prio(win.global_prio)
            win._on_budgets({"enabled": True, "entries": []})
            win.on_state({"interface": "eth0", "enabled": True,
                          "processes": [], "apps": [], "rules": [],
                          "global": {"download": 500.0, "upload": 50.0},
                          "attributed": {"download": 400.0, "upload": 40.0}})
            win._on_sort_change("name", False)
            self.assertIn("toggle_enabled", [c[0] for c in gui.calls])
            self.assertIn("set_unit", [c[0] for c in gui.calls])
        finally:
            win.destroy()

    def test_totals_are_a_two_line_block(self):
        """Total und „matched to apps" stehen in getrennten Zeilen."""
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        gui = _FakeWindowGui()
        win = self._window(gui)
        try:
            win.on_state({"interface": "eth0", "enabled": True,
                          "processes": [], "apps": [], "rules": [],
                          "global": {"download": 500.0, "upload": 50.0},
                          "attributed": {"download": 400.0, "upload": 40.0}})
            self.assertEqual(win.total_label.get_text(), "Total traffic")
            self.assertIn("matched to apps", win.total_meta.get_text())
            self.assertEqual(win.totals_box.get_orientation(),
                             Gtk.Orientation.VERTICAL)
            self.assertIsNot(win.total_row, win.meta_row)
            self.assertEqual(win.total_row.get_parent(), win.totals_box)
            self.assertEqual(win.meta_row.get_parent(), win.totals_box)
        finally:
            win.destroy()

    def test_unit_is_a_menu_action_and_still_applies(self):
        """Die Einheit liegt im Menue, wirkt aber wie vorher."""
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        gui = _FakeWindowGui()
        win = self._window(gui)
        try:
            action = win.lookup_action("unit")
            self.assertIsNotNone(action, "Einheit muss als Action existieren")
            action.activate(GLib.Variant.new_string("mbps"))
            self.assertEqual(win.unit, "mbps")
            self.assertIn(("set_unit", {"unit": "mbps"}), gui.calls)
            self.assertEqual(win.table.unit, "mbps")
            self.assertEqual(win.graph.unit, "mbps")
            self.assertFalse(hasattr(win, "unit_dd"),
                             "der Header darf kein Einheiten-Dropdown mehr haben")
        finally:
            win.destroy()

    def test_start_profile_response(self):
        gui = _FakeWindowGui()
        win = self._window(gui)

        class _Dropdown:
            def __init__(self, index):
                self._index = index

            def get_selected(self):
                return self._index

        try:
            win._on_start_profile_response(
                None, "save", _Dropdown(1), ["(none)", "Uni"])
            self.assertIn(("set_start_profile", {"name": "Uni"}), gui.calls)
            win._on_start_profile_response(
                None, "save", _Dropdown(0), ["(none)", "Uni"])
            self.assertIn(("set_start_profile", {}), gui.calls)
            win._on_start_profile_response(
                None, "cancel", _Dropdown(1), ["(none)", "Uni"])
        finally:
            win.destroy()


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class GraphTimeAxisTest(unittest.TestCase):
    """Die Zeitachse des Graphen nutzt runde Abstaende und endet bei 'jetzt'."""

    def test_ticks_are_round_and_include_now(self):
        from throtl.gui.graph import time_ticks

        self.assertEqual(time_ticks(60), [60.0, 45.0, 30.0, 15.0, 0.0])
        self.assertEqual(time_ticks(30), [30.0, 20.0, 10.0, 0.0])
        self.assertEqual(time_ticks(900), [900.0, 600.0, 300.0, 0.0])

    def test_degenerate_span_still_yields_now(self):
        from throtl.gui.graph import time_ticks

        self.assertEqual(time_ticks(0), [0.0])
        self.assertEqual(time_ticks(-5), [0.0])


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class StatsResetTest(unittest.TestCase):
    """Statistics: Zuruecksetzen fragt vorher nach (Datenverlust)."""

    def _dialog(self):
        from throtl.gui.app import StatsDialog

        calls = []

        class _Gui:
            def call(self, method, params=None, timeout=10):
                if method == "get_stats":
                    return {"window": "minute", "apps": [], "totals": {}}
                if method == "get_stats_history":
                    return {"window": "minute", "series": []}
                return {}

            def call_async(self, method, params=None, **kwargs):
                calls.append((method, params))
                if kwargs.get("on_done"):
                    kwargs["on_done"]({})

        return StatsDialog(None, _Gui()), calls

    def test_reset_needs_confirmation(self):
        dialog, calls = self._dialog()
        try:
            dialog._on_reset()
            self.assertEqual([c for c in calls if c[0] == "reset_stats"], [])
            dialog._on_reset_response(None, "cancel")
            self.assertEqual([c for c in calls if c[0] == "reset_stats"], [])
            dialog._on_reset_response(None, "reset")
            self.assertIn("reset_stats", [c[0] for c in calls])
        finally:
            dialog.force_close()


if __name__ == "__main__":
    unittest.main()
