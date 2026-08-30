"""Throtl-GUI: natives GTK4 + libadwaita-Frontend.

Startet das Hauptfenster, verbindet sich mit dem Throtl-Daemon ueber den
Unix-Socket und zeigt Live-Bandbreiten + verwaltete Regeln.

Design: dunkel, minimalistisch — passend zu Omarchy/Hyprland. Verwendet
Adwaita-Widgets und eine kleine CSS-Datei fuer Akzente.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Gdk

from .. import SOCKET_PATH, __version__
from ..config import PRIORITY_NAMES, priority_to_name
from ..units import format_rate
from .client import GuiClient
from .graph import BandwidthGraph
from .process_pane import ProcessPanel
from .rule_editor import RuleEditor
from .widgets import PRIORITY_LABELS

APP_ID = "io.github.throtl"
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")


class ThrotlWindow(Adw.ApplicationWindow):
    def __init__(self, app, gui, autostart: bool = False):
        super().__init__(application=app)
        self.gui = gui
        self.app = app
        self.set_title("Throtl")
        self.set_default_size(760, 560)

        self.content = Adw.ToolbarView()
        self.set_content(self.content)

        self._build_headerbar()
        self._build_body()

        # Status-Nachrichten-Leiste
        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(0.0)
        self._status_revealer = Gtk.Revealer()
        self._status_revealer.add_css_class("status-bar")
        self._status_revealer.set_child(self.status_label)
        self.content.add_bottom_bar(self._status_revealer)

        self.connect("destroy", self._on_destroy)

        if autostart:
            # Beim Autostart minimiert/versteckt starten (im Tray weiterlaufen)
            self.present()
            self.close()  # verstecken statt beenden (nur wenn Tray vorhanden ist)
        else:
            self.present()

    # --- Layout ----------------------------------------------------------

    def _build_headerbar(self):
        header = Adw.HeaderBar()
        self.content.add_top_bar(header)

        # Ein/Aus-Schalter fuer globales Shaping
        self.toggle_switch = Gtk.Switch()
        self.toggle_switch.set_active(True)
        self.toggle_switch.connect("state-set", self._on_toggle)
        toggle_box = Gtk.Box()
        toggle_box.append(Gtk.Label(label="Shaping"))
        toggle_box.append(self.toggle_switch)
        header.pack_start(toggle_box)

        # Einheit-Auswahl (kbps / kBs)
        self.unit_dd = Gtk.DropDown(
            model=Gio.ListStore.new(Gtk.StringObject), factory=None)
        for u in ("kbps", "kBs"):
            self.unit_dd.get_model().append(Gtk.StringObject.new(
                {"kbps": "kbit/s", "kBs": "KB/s"}[u]))
        self.unit_dd.set_selected(0)
        self.unit_dd.connect("notify::selected", self._on_unit)
        header.pack_end(self.unit_dd)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)

    def _build_body(self):
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        view.set_margin_top(8)
        view.set_margin_bottom(8)
        view.set_margin_start(12)
        view.set_margin_end(12)
        self.content.set_content(view)

        # Globale Limits-Leiste
        self.global_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.global_bar.add_css_class("toolbar")
        # (kurz: Anzeige der globalen Limits; vollständig ueber CLI/Prefs)
        self.global_label = Gtk.Label(label="Global: unbegrenzt")
        self.global_label.set_xalign(0.0)
        self.global_label.set_hexpand(True)
        self.global_bar.append(self.global_label)
        view.append(self.global_bar)

        # Live-Graph
        self.graph = BandwidthGraph(window_seconds=60)
        view.append(self.graph)

        # Prozesspane (Live + Regeln)
        self.process_panel = ProcessPanel(self, {}, "kbps")
        view.append(self.process_panel)

        # Regel-Editor
        self.rule_editor = RuleEditor(self, "kbps")
        view.append(self.rule_editor)

    # --- Public API fuer Sub-Widgets --------------------------------------

    def render_rules(self, rules):
        """Vom ProcessPanel aufgerufen: Regeln im Editor rendern."""
        self.rule_editor.refresh(rules)

    def add_rule(self):
        self.rule_editor.add_blank()

    def show_error(self, message):
        self.status_label.set_text(f"⚠ {message}")
        self._status_revealer.set_reveal_child(True)

    def show_info(self, message):
        self.status_label.set_text(message)
        self._status_revealer.set_reveal_child(True)

    def reload(self):
        try:
            cfg = self.gui.client.call("get_config")
            self.unit = cfg.get("unit", "kbps")
            state = self.gui.client.call("list_processes")
            self._apply_state(state)
            self.rule_editor.refresh(cfg.get("processes", []))
            self.toggle_switch.set_active(bool(cfg["global"].get("enabled", True)))
            self._update_global_label(cfg)
            self.show_info("Neu geladen")
        except Exception as error:
            self.show_error(str(error))

    # --- Event-Handler ---------------------------------------------------

    def _apply_state(self, state):
        self.process_panel.refresh(state)
        total_d = sum(p.get("download", 0.0) for p in state.get("processes", []))
        total_u = sum(p.get("upload", 0.0) for p in state.get("processes", []))
        self.graph.push(total_d, total_u)
        if hasattr(self, "unit"):
            pass  # Einheit live wechseln ohne Neubau

    def _on_toggle(self, switch, state):
        try:
            self.gui.client.call("toggle_enabled", {"enabled": state})
            self.show_info("Shaping " + ("AUS" if not state else "AN"))
        except Exception as error:
            self.show_error(str(error))
            switch.set_state(not state)
        return True

    def _on_unit(self, dropdown, *args):
        idx = dropdown.get_selected()
        unit = ("kbps", "kBs")[idx] if idx >= 0 else "kbps"
        try:
            self.gui.client.call("set_unit", {"unit": unit})
        except Exception as error:
            self.show_error(str(error))

    def _update_global_label(self, cfg):
        g = cfg.get("global", {})
        dl = format_rate(g.get("download_limit"))
        ul = format_rate(g.get("upload_limit"))
        self.global_label.set_text(f"Global: runter {dl} · rauf {ul}")

    def _on_destroy(self, *args):
        if hasattr(self, "gui"):
            self.gui.shutdown()

    # --- Poll-Callback ---------------------------------------------------

    def on_state(self, state):
        self._apply_state(state)
        self.toggle_switch.set_active(bool(state.get("enabled", True)))


def _load_css(application):
    provider = Gtk.CssProvider()
    if os.path.exists(CSS_PATH):
        provider.load_from_path(CSS_PATH)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class ThrotlApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None
        self.gui = None
        self.autostart = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        _load_css(self)

    def do_activate(self):
        if self.window is None:
            self.gui = GuiClient(
                on_state=self._broadcast_state,
                on_error=self._show_fatal_error)
            self.window = ThrotlWindow(self, self.gui, autostart=self.autostart)
            GLib.idle_add(self._connect_daemon, priority=GLib.PRIORITY_LOW)
        else:
            self.window.present()

    def _connect_daemon(self):
        """Daemon im Hintergrund-Thread verbinden (Socket + Poll-Start)."""
        try:
            import threading

            def _connect(self=self):
                try:
                    self.gui.connect()
                    self.gui.start_polling(1.0)
                    GLib.idle_add(self.window.reload)
                except Exception:
                    pass

            threading.Thread(target=_connect, daemon=True).start()
        except Exception as error:
            self._show_fatal_error(str(error))
        return False

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
