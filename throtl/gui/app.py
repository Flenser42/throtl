"""Throtl GUI — a NetLimiter-style bandwidth manager for Linux (GTK4 + libadwaita).

Layout overview (inspired by NetLimiter):
    HeaderBar:  [Throttling on/off]  [global DL/UL limits + link]  [Unit]  [Refresh]
    ───────────────────────────────────────────────────────────────────────────────
    Live bandwidth graph (Download / Upload over time)
    Network connections table:
        PID | Process | ▼ Download | ▲ Upload | DL limit | UL limit | Priority
        (each row is editable: type a rate in the limit fields, pick a priority)
    Global status / error bar at the bottom.

All user-facing text is English. The display unit can be switched between
Mbit/s, MB/s, kbit/s and KB/s.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Gdk

from .. import SOCKET_PATH, __version__
from ..units import format_rate, parse_rate_lenient
from .client import GuiClient
from .graph import BandwidthGraph
from .process_pane import ProcessTable

APP_ID = "io.github.throtl"
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")

DEBUG = os.environ.get("THROTL_DEBUG", "") == "1"


def _debug(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[throtl-gui] {msg}\n")
        sys.stderr.flush()


# Unit id (value stored in daemon config) -> short display label
UNIT_CHOICES = (
    ("mBs", "MB/s"),
    ("mbps", "Mbit/s"),
    ("kBs", "KB/s"),
    ("kbps", "kbit/s"),
)


class ThrotlWindow(Adw.ApplicationWindow):
    def __init__(self, app, gui, autostart: bool = False):
        _debug("ThrotlWindow.__init__: start")
        super().__init__(application=app)
        self.gui = gui
        self.app = app
        self.set_title("Throtl — Network Bandwidth Manager")
        self.set_default_size(980, 620)

        self.unit = "mBs"

        self.content = Adw.ToolbarView()
        self.set_content(self.content)

        self._build_headerbar()
        self._build_body()

        # Status / error bar
        self.status_label = Gtk.Label(label="", xalign=0.0)
        self.status_label.add_css_class("dim-label")
        revealer = Gtk.Revealer()
        revealer.add_css_class("status-bar")
        revealer.set_child(self.status_label)
        self.content.add_bottom_bar(revealer)
        self.status_label_revealer = revealer

        self.connect("destroy", self._on_destroy)
        self.present()
        _debug("ThrotlWindow.__init__: done")

    # --- Layout ----------------------------------------------------------

    def _build_headerbar(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Throtl"))
        self.content.add_top_bar(header)

        # Throttling master switch
        switch_box = Gtk.Box(spacing=6)
        switch_box.append(Gtk.Label(label="Throttling"))
        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_active(True)
        self.toggle_switch.connect("state-set", self._on_toggle)
        switch_box.append(self.toggle_switch)
        header.pack_start(switch_box)

        # Unit chooser
        self.unit_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        self.unit_dd.get_model().append(Gtk.StringObject.new("MB/s"))
        self.unit_dd.get_model().append(Gtk.StringObject.new("Mbit/s"))
        self.unit_dd.get_model().append(Gtk.StringObject.new("KB/s"))
        self.unit_dd.get_model().append(Gtk.StringObject.new("kbit/s"))
        self.unit_dd.set_selected(0)
        self.unit_dd.add_css_class("throtl-unit")
        self.unit_dd.connect("notify::selected", self._on_unit)
        header.pack_end(self.unit_dd)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Reload")
        refresh_btn.connect("clicked", lambda *_w: self.reload())
        header.pack_end(refresh_btn)

    def _build_body(self):
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        view.set_margin_top(8)
        view.set_margin_bottom(8)
        view.set_margin_start(12)
        view.set_margin_end(12)
        self.content.set_content(view)

        # Global limits row
        self.global_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.global_box.add_css_class("toolbar")
        gdown = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        gdown.append(Gtk.Label(label="Global download", xalign=0))
        self.global_dl_entry = self._limit_entry()
        gdown.append(self.global_dl_entry)
        self.global_box.append(gdown)

        gup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        gup.append(Gtk.Label(label="Global upload", xalign=0))
        self.global_ul_entry = self._limit_entry()
        gup.append(self.global_ul_entry)
        self.global_box.append(gup)

        prio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        prio_box.append(Gtk.Label(label="Global priority", xalign=0))
        self.global_prio = self._priority_dropdown()
        prio_box.append(self.global_prio)
        self.global_box.append(prio_box)

        hint = Gtk.Label(
            label="Leave a limit empty for unlimited. Blanks keep the current "
                  "traffic unthrottled so QoS needs a defined interface cap.",
            xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.set_hexpand(True)
        self.global_box.append(hint)
        view.append(self.global_box)

        self.global_dl_entry.connect("changed", self._on_global_dl)
        self.global_ul_entry.connect("changed", self._on_global_ul)
        self.global_prio.connect("notify::selected", self._on_global_prio)

        # Live graph
        self.graph = BandwidthGraph(window_seconds=60)
        view.append(self.graph)

        # Connections table
        self.table = ProcessTable(self, unit=self.unit)
        view.append(self.table)

    def _label_with(self, text, css=""):
        label = Gtk.Label(label=text, xalign=0)
        if css:
            label.add_css_class(css)
        return label

    def _limit_entry(self) -> Gtk.Entry:
        entry = Gtk.Entry(width_chars=10)
        entry.set_placeholder_text("unlimited")
        return entry

    def _priority_dropdown(self):
        from .widgets import PriorityDropdown

        dd = PriorityDropdown()
        dd.set_priority_name("normal")
        return dd

    # --- Public API for sub-widgets --------------------------------------

    def show_error(self, message: str) -> None:
        self.status_label.set_text(f"⚠ {message}")
        self.status_label_revealer.set_reveal_child(True)

    def show_info(self, message: str) -> None:
        self.status_label.set_text(message)
        self.status_label_revealer.set_reveal_child(True)

    def reload(self) -> None:
        try:
            cfg = self.gui.call("get_config")
            self.unit = cfg.get("unit", "mBs") or "mBs"
            if self.unit not in (u for u, _l in UNIT_CHOICES):
                self.unit = "mBs"
            self._sync_unit_widgets()
            state = self.gui.call("list_processes")
            self._apply_state(state)
            self.toggle_switch.set_active(bool(cfg["global"].get("enabled", True)))
            self._sync_global_fields(cfg)
            self.show_info("Reloaded")
        except Exception as error:
            self.show_error(str(error))

    # --- State application -----------------------------------------------

    def _apply_state(self, state: dict) -> None:
        self.table.refresh(state)
        total_d = sum(p.get("download", 0.0) for p in state.get("processes", []))
        total_u = sum(p.get("upload", 0.0) for p in state.get("processes", []))
        self.graph.push(total_d, total_u)

    def on_state(self, state: dict) -> None:
        self._apply_state(state)
        self.toggle_switch.set_active(bool(state.get("enabled", True)))

    def _sync_unit_widgets(self) -> None:
        self.table.set_unit(self.unit)
        labels = dict(UNIT_CHOICES)
        for i, (unit, label) in enumerate(UNIT_CHOICES):
            if unit == self.unit:
                self.unit_dd.set_selected(i)
                break
        hint = labels.get(self.unit, "MB/s")
        for entry in (self.global_dl_entry, self.global_ul_entry):
            entry.set_placeholder_text(f"limit in {hint}")

    def _sync_global_fields(self, cfg: dict) -> None:
        g = cfg["global"]
        self.global_dl_entry.set_text(_entry_text(g.get("download_limit"), self.unit))
        self.global_ul_entry.set_text(_entry_text(g.get("upload_limit"), self.unit))
        from .widgets import PRIORITY_NAMES

        prio = g.get("download_priority", "normal")
        if prio in PRIORITY_NAMES:
            self.global_prio.set_priority_name(prio)

    # --- Handlers --------------------------------------------------------

    def _on_toggle(self, switch, state):
        try:
            self.gui.call("toggle_enabled", {"enabled": state})
            self.show_info("Throttling " + ("disabled" if not state else "enabled"))
        except Exception as error:
            self.show_error(str(error))
            switch.set_state(not state)
        return True

    def _on_unit(self, dd, *args):
        idx = dd.get_selected()
        if 0 <= idx < len(UNIT_CHOICES):
            self.unit = UNIT_CHOICES[idx][0]
        self._sync_unit_widgets()
        try:
            self.gui.call("set_unit", {"unit": self.unit})
        except Exception as error:
            self.show_error(str(error))

    def _on_global_dl(self, entry, *_args):
        self._debounce_global("download_limit", entry)

    def _on_global_ul(self, entry, *_args):
        self._debounce_global("upload_limit", entry)

    def _debounce_global(self, key, entry):
        # Debounce: fire at most once per 500ms after edits stop.
        timer_attr = "_timer_" + key.replace("_", "")

        def _send():
            text = entry.get_text()
            try:
                value = None if not (text or "").strip() else parse_rate_lenient(text)
            except ValueError as error:
                self.show_error(f"Invalid limit: {error}")
                setattr(self, timer_attr, None)
                return False
            try:
                self.gui.call("set_global", {key: value})
                self.show_info("Global limit set")
            except Exception as error:
                self.show_error(str(error))
            setattr(self, timer_attr, None)
            return False

        old = getattr(self, timer_attr, None)
        if old is not None:
            GLib.source_remove(old)
        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_global_prio(self, dd, *_args):
        from .widgets import PRIORITY_NAMES

        name = dd.get_priority_name()
        try:
            self.gui.call("set_global", {"download_priority": name,
                                         "upload_priority": name})
        except Exception as error:
            self.show_error(str(error))

    def _on_destroy(self, *args):
        if hasattr(self, "gui"):
            self.gui.shutdown()

    # compatibility alias (used by ProcessTable via self.gui.client in old code?)
    @property
    def client(self):
        return self.gui


def _entry_text(kbit, unit: str) -> str:
    if kbit is None:
        return ""
    # For Mbit/s / MB/s units show a compact rate number the user can edit.
    if unit in ("mBs", "mbps"):
        val = kbit / 1000.0 if unit == "mbps" else kbit * 0.125 / 1000.0
        return f"{val:.2f}"
    if unit == "kBs":
        return f"{kbit * 0.125:.1f}"
    return str(int(kbit))


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
                                 on_error=self._show_fatal_error)
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
        """Connect to the daemon synchronously (short timeout) and start polling.
        On failure the window stays visible with an explanatory message."""
        try:
            self.gui.connect(timeout=2.0)
            self.gui.start_polling(1.0)
            self.window.reload()
        except ConnectionError:
            # Message came via on_error; window stays up.
            pass
        except Exception as error:
            self.window.show_error(str(error))

    def _broadcast_state(self, state):
        if self.window is not None:
            self.window.on_state(state)

    def _show_fatal_error(self, message):
        if self.window is not None:
            self.window.show_error(f"Daemon: {message}")


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
