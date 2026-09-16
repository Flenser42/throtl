"""Throtl GUI — a NetLimiter-style bandwidth manager for Linux (GTK4 + libadwaita).

Layout (inspired by NetLimiter):
    HeaderBar:  [Throttling switch]                      [Unit ▾] [Reload]
    ─────────────────────────────────────────────────────────────────────────
    Global limits:  Download [____]  Upload [____]  Priority [▾]   hint…
    Total:  ▼ 12.3 Mbit/s   ▲ 480 kbit/s
    Live bandwidth graph (download / upload over time)
    Network table:  PID | Process | ▼ Download | ▲ Upload | DL limit | UL limit | Priority
    Status / error bar at the bottom.

All user-facing text is English. Limit fields are displayed and interpreted in
the selected unit (MB/s, Mbit/s, KB/s, kbit/s); an explicit suffix like
"2 kbps" always wins. Editing is debounced, and programmatic updates are
guarded so they never fire daemon calls.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Gdk

from .. import __version__
from ..units import format_rate, format_rate_for_entry, parse_rate_in_unit
from .client import GuiClient
from .graph import BandwidthGraph
from .process_pane import ProcessTable
from .widgets import UNIT_CHOICES, UNIT_IDS, UNIT_LABELS

APP_ID = "io.github.throtl"
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")

DEBUG = os.environ.get("THROTL_DEBUG", "") == "1"


def _debug(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[throtl-gui] {msg}\n")
        sys.stderr.flush()


class ThrotlWindow(Adw.ApplicationWindow):
    def __init__(self, app, gui, autostart: bool = False):
        _debug("ThrotlWindow.__init__: start")
        super().__init__(application=app)
        self.gui = gui
        self.app = app
        self.set_title("Throtl — Network Bandwidth Manager")
        self.set_default_size(1000, 640)
        self.unit = "mBs"
        self._syncing = False

        self.content = Adw.ToolbarView()
        self.set_content(self.content)
        self._build_headerbar()
        self._build_body()

        # Status / error bar
        self.status_label = Gtk.Label(label="", xalign=0.0, wrap=True)
        self.status_label.add_css_class("dim-label")
        self._status_revealer = Gtk.Revealer()
        self._status_revealer.add_css_class("status-bar")
        self._status_revealer.set_child(self.status_label)
        self.content.add_bottom_bar(self._status_revealer)

        self.connect("destroy", self._on_destroy)
        self.present()
        _debug("ThrotlWindow.__init__: done")

    # --- Layout -----------------------------------------------------------

    def _build_headerbar(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Throtl"))
        self.content.add_top_bar(header)

        switch_box = Gtk.Box(spacing=8)
        switch_box.append(Gtk.Label(label="Throttling"))
        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_active(True)
        self.toggle_switch.set_valign(Gtk.Align.CENTER)
        self.toggle_switch.connect("state-set", self._on_toggle)
        switch_box.append(self.toggle_switch)
        header.pack_start(switch_box)

        self.unit_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        for _unit, label in UNIT_CHOICES:
            self.unit_dd.get_model().append(Gtk.StringObject.new(label))
        self.unit_dd.set_selected(0)
        self.unit_dd.set_tooltip_text("Display unit")
        self.unit_dd.add_css_class("throtl-unit")
        self.unit_dd.connect("notify::selected", self._on_unit)
        header.pack_end(self.unit_dd)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Reload from daemon")
        refresh_btn.connect("clicked", lambda *_w: self.reload())
        header.pack_end(refresh_btn)

    def _build_body(self):
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        view.set_margin_top(10)
        view.set_margin_bottom(10)
        view.set_margin_start(12)
        view.set_margin_end(12)
        self.content.set_content(view)

        # --- Global limits ---
        glob = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        glob.add_css_class("toolbar")
        self.global_dl_entry = self._labelled_entry(glob, "Global download")
        self.global_ul_entry = self._labelled_entry(glob, "Global upload")

        prio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        prio_box.append(self._caption("Global priority"))
        self.global_prio = self._priority_dropdown()
        prio_box.append(self.global_prio)
        glob.append(prio_box)

        hint = Gtk.Label(
            label="Empty = unlimited. Set a global cap to enable prioritisation.",
            xalign=0.0, wrap=True, hexpand=True)
        hint.add_css_class("dim-label")
        hint.set_valign(Gtk.Align.END)
        glob.append(hint)
        view.append(glob)

        self.global_dl_entry.connect("changed", self._on_global_dl)
        self.global_ul_entry.connect("changed", self._on_global_ul)
        self.global_prio.connect("notify::selected", self._on_global_prio)

        # --- Totals + graph ---
        self.total_label = Gtk.Label(label="Total:  ▼ 0   ▲ 0", xalign=0.0)
        self.total_label.add_css_class("total-label")
        view.append(self.total_label)

        self.graph = BandwidthGraph(window_seconds=60)
        view.append(self.graph)

        # --- Process table ---
        self.table = ProcessTable(self, unit=self.unit)
        view.append(self.table)

    def _caption(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0.0)
        label.add_css_class("dim-label")
        return label

    def _labelled_entry(self, parent: Gtk.Box, caption: str) -> Gtk.Entry:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self._caption(caption))
        entry = Gtk.Entry(width_chars=12)
        entry.set_placeholder_text("unlimited")
        box.append(entry)
        parent.append(box)
        return entry

    def _priority_dropdown(self):
        from .widgets import PriorityDropdown

        dd = PriorityDropdown()
        dd.set_priority_name("normal")
        return dd

    # --- Status bar -------------------------------------------------------

    def show_error(self, message: str) -> None:
        self.status_label.set_text(f"⚠  {message}")
        self._status_revealer.set_reveal_child(True)

    def show_info(self, message: str) -> None:
        self.status_label.set_text(message)
        self._status_revealer.set_reveal_child(True)

    # --- Daemon sync ------------------------------------------------------

    def reload(self) -> None:
        try:
            cfg = self.gui.call("get_config")
            unit = cfg.get("unit", "mBs") or "mBs"
            self.unit = unit if unit in UNIT_IDS else "mBs"
            self._sync_unit_widgets()
            self._sync_global_fields(cfg)
            state = self.gui.call("list_processes")
            self._apply_state(state)
        except Exception as error:
            self.show_error(str(error))

    def _sync_unit_widgets(self) -> None:
        self.table.set_unit(self.unit)
        idx = UNIT_IDS.index(self.unit)
        if self.unit_dd.get_selected() != idx:
            self._syncing = True
            try:
                self.unit_dd.set_selected(idx)
            finally:
                self._syncing = False
        hint = f"limit in {UNIT_LABELS.get(self.unit, 'MB/s')}"
        for entry in (self.global_dl_entry, self.global_ul_entry):
            entry.set_placeholder_text(hint)

    def _sync_global_fields(self, cfg: dict) -> None:
        g = cfg.get("global", {})
        self._syncing = True
        try:
            self.global_dl_entry.set_text(
                format_rate_for_entry(g.get("download_limit"), self.unit))
            self.global_ul_entry.set_text(
                format_rate_for_entry(g.get("upload_limit"), self.unit))
            self.toggle_switch.set_active(bool(g.get("enabled", True)))
            from .widgets import PRIORITY_NAMES

            prio = g.get("download_priority", "normal")
            if prio in PRIORITY_NAMES:
                self.global_prio.set_priority_name(prio)
        finally:
            self._syncing = False

    # --- State application ------------------------------------------------

    def _apply_state(self, state: dict) -> None:
        self.table.set_state(state)
        processes = state.get("processes", [])
        total_d = sum(p.get("download", 0.0) for p in processes)
        total_u = sum(p.get("upload", 0.0) for p in processes)
        self.total_label.set_text(
            f"Total ({len(processes)} processes):"
            f"   ▼ {format_rate(total_d, self.unit, 1)}"
            f"   ▲ {format_rate(total_u, self.unit, 1)}")
        self.graph.push(total_d, total_u)
        if not self._syncing:
            enabled = bool(state.get("enabled", True))
            if self.toggle_switch.get_active() != enabled:
                self._syncing = True
                try:
                    self.toggle_switch.set_active(enabled)
                finally:
                    self._syncing = False

    def on_state(self, state: dict) -> None:
        self._apply_state(state)

    # --- Handlers ---------------------------------------------------------

    def _on_toggle(self, switch, state):
        """Nutzer schaltet das Shaping um.

        Wir setzen den sichtbaren Zustand SELBST (nach erfolgreichem RPC) und
        geben True zurueck, damit GTKs Default-Handler ihn nicht ueberschreibt —
        sonst liefen Schalter und Daemon auseinander (Switch blieb optisch AN,
        obwohl der Daemon AUS meldete).
        """
        if self._syncing:
            return False
        try:
            self.gui.call("toggle_enabled", {"enabled": bool(state)})
        except Exception as error:
            self.show_error(str(error))
            return True  # Zustand unveraendert lassen
        self.show_info("Throttling " + ("off" if not state else "on"))
        self._syncing = True
        try:
            switch.set_active(bool(state))
        finally:
            self._syncing = False
        return True

    def _on_unit(self, dd, *_args):
        if self._syncing:
            return
        idx = dd.get_selected()
        if not (0 <= idx < len(UNIT_IDS)):
            return
        self.unit = UNIT_IDS[idx]
        self.table.set_unit(self.unit)
        hint = f"limit in {UNIT_LABELS.get(self.unit, 'MB/s')}"
        for entry in (self.global_dl_entry, self.global_ul_entry):
            entry.set_placeholder_text(hint)
        # Global limit fields zeigen denselben Wert in der neuen Einheit
        try:
            cfg = self.gui.call("get_config")
            self._sync_global_fields(cfg)
            self.gui.call("set_unit", {"unit": self.unit})
        except Exception as error:
            self.show_error(str(error))

    def _on_global_dl(self, entry, *_args):
        self._debounce_global("download_limit", entry)

    def _on_global_ul(self, entry, *_args):
        self._debounce_global("upload_limit", entry)

    def _debounce_global(self, key, entry):
        if self._syncing:
            return
        timer_attr = "_timer_" + key.replace("_", "")

        def _send():
            setattr(self, timer_attr, None)
            if self._syncing:
                return False
            text = entry.get_text()
            try:
                value = parse_rate_in_unit(text, self.unit)
            except ValueError as error:
                self.show_error(f"Invalid limit: {error}")
                return False
            try:
                self.gui.call("set_global", {key: value})
                self.show_info("Global limit updated")
            except Exception as error:
                self.show_error(str(error))
            return False

        old = getattr(self, timer_attr, None)
        if old is not None:
            GLib.source_remove(old)
        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_global_prio(self, dd, *_args):
        if self._syncing:
            return
        name = dd.get_priority_name()
        try:
            self.gui.call("set_global", {"download_priority": name,
                                         "upload_priority": name})
            self.show_info(f"Global priority: {name}")
        except Exception as error:
            self.show_error(str(error))

    def _on_destroy(self, *args):
        if hasattr(self, "gui"):
            self.gui.shutdown()

    # Backwards-compatible alias used by ProcessTable
    @property
    def client(self):
        return self.gui


def _load_css(application) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    if os.path.exists(CSS_PATH):
        provider.load_from_path(CSS_PATH)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class ThrotlApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None
        self.gui = None
        self.autostart = False

    def do_startup(self):
        _debug("do_startup: begin")
        Adw.Application.do_startup(self)
        _load_css(self)
        _debug("do_startup: done")

    def do_activate(self):
        _debug("do_activate")
        if self.window is None:
            self.gui = GuiClient(on_state=self._broadcast_state,
                                 on_error=self._show_daemon_error,
                                 on_connected=self._on_daemon_connected)
            try:
                self.window = ThrotlWindow(self, self.gui,
                                           autostart=self.autostart)
                _debug("window built + presented")
            except Exception:
                import traceback

                traceback.print_exc()
                self.quit()
                return
            self._connect_daemon()
        else:
            self.window.present()

    def _connect_daemon(self):
        """Initial connect; the poller keeps retrying in the background."""
        try:
            self.gui.connect(timeout=2.0)
        except ConnectionError:
            pass  # Meldung kam ueber on_error; Fenster bleibt sichtbar
        except Exception as error:
            self.window.show_error(str(error))
        self.gui.start_polling(1.0)
        if self.gui.connected:
            self.window.reload()

    def _on_daemon_connected(self):
        """Called (in the main loop) whenever the poller (re)connects."""
        if self.window is not None and self.gui.connected:
            self.window.reload()

    def _broadcast_state(self, state):
        if self.window is not None:
            self.window.on_state(state)

    def _show_daemon_error(self, message):
        if self.window is not None:
            self.window.show_error(message)


def main(argv=None):
    if argv is None:
        argv = sys.argv
    autostart = "--autostart" in argv
    argv = [a for a in argv if a != "--autostart"]

    app = ThrotlApplication()
    app.autostart = autostart
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
