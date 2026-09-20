"""Throtl GUI — a per-application bandwidth manager for Linux (GTK4 + libadwaita).

Layout:
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

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

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
    def __init__(self, app, gui):
        _debug("ThrotlWindow.__init__: start")
        super().__init__(application=app)
        self.gui = gui
        self.app = app
        self.set_title("Throtl — Network Bandwidth Manager")
        self.set_default_size(1060, 780)
        self.unit = "mBs"
        self._syncing = False
        self._profile_names = []

        self._build_actions()
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

        self._build_profile_controls(header)

    def _build_profile_controls(self, header):
        """Profil-Auswahl + Menue (Profil speichern/loeschen, Statistik)."""
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Label(label="Profile"))
        self.profile_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        self.profile_dd.set_tooltip_text("Active profile")
        self.profile_dd.set_size_request(150, -1)
        self.profile_dd.connect("notify::selected", self._on_profile_selected)
        box.append(self.profile_dd)

        profile_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        profile_refresh.set_tooltip_text("Reload profiles")
        profile_refresh.connect("clicked", lambda *_w: self._reload_profiles())
        box.append(profile_refresh)

        menu = Gio.Menu()
        menu.append("Save settings as profile…", "win.save-profile")
        menu.append("Delete profile", "win.delete-profile")
        menu.append("Statistics…", "win.stats")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("Profile and statistics")
        box.append(menu_btn)
        header.pack_end(box)

    def _build_actions(self):
        for name, handler in (
            ("save-profile", self._on_save_profile),
            ("delete-profile", self._on_delete_profile),
            ("stats", self._on_show_stats),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _build_body(self):
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        view.add_css_class("throtl-root")
        view.set_margin_top(10)
        view.set_margin_bottom(10)
        view.set_margin_start(12)
        view.set_margin_end(12)
        self.content.set_content(view)

        # --- Global limits ---
        glob = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        glob.add_css_class("toolbar")
        self.global_dl_entry = self._labelled_entry(glob, "Global download limit")
        self.global_ul_entry = self._labelled_entry(glob, "Global upload limit")

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

        # Scrollbare History (~15 min bei 1 Hz), Hover zeigt Werte
        self.graph = BandwidthGraph(max_samples=900, unit=self.unit)
        view.append(self.graph)

        # --- Process table (fuellt den Rest bis zum unteren Rand) ---
        self.table = ProcessTable(self, unit=self.unit)
        self.table.set_vexpand(True)
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

    # --- Profile ----------------------------------------------------------

    def _reload_profiles(self) -> None:
        try:
            result = self.gui.call("list_profiles")
        except Exception as error:
            self.show_error(str(error))
            return
        names = result.get("profiles") or []
        active = result.get("active")
        self._profile_names = names
        model = self.profile_dd.get_model()
        model.remove_all()
        for name in names:
            model.append(Gtk.StringObject.new(name))
        index = names.index(active) if active in names else 0
        self._syncing = True
        try:
            self.profile_dd.set_selected(index)
        finally:
            self._syncing = False

    def _current_profile(self) -> str | None:
        idx = self.profile_dd.get_selected()
        if 0 <= idx < len(self._profile_names):
            return self._profile_names[idx]
        return None

    def _on_profile_selected(self, dd, *_args):
        if self._syncing:
            return
        name = self._current_profile()
        if not name:
            return
        self.gui.call_async(
            "activate_profile", {"name": name},
            on_done=lambda _r: self._on_profile_activated(name),
            on_error=self.show_error,
        )

    def _on_profile_activated(self, name: str) -> None:
        self.show_info(f"Profile: {name}")
        self.reload()

    def _on_save_profile(self, *_args):
        dialog = Adw.MessageDialog(
            transient_for=self, heading="Save settings as profile")
        dialog.set_body(
            "The current global limits and process rules are stored under the "
            "given name.")
        entry = Gtk.Entry()
        entry.set_placeholder_text("Profile name")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._on_save_profile_response, entry)
        dialog.present()

    def _on_save_profile_response(self, _dialog, response, entry):
        if response != "save":
            return
        name = entry.get_text().strip()
        if not name:
            self.show_error("Profile name must not be empty.")
            return
        self.gui.call_async(
            "set_profile", {"name": name, "activate": True},
            on_done=lambda _r: self._on_profile_activated(name),
            on_error=self.show_error,
        )

    def _on_delete_profile(self, *_args):
        name = self._current_profile()
        if not name:
            return
        dialog = Adw.MessageDialog(transient_for=self, heading="Delete profile?")
        dialog.set_body(f'Delete the profile "{name}"?')
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_profile_response, name)
        dialog.present()

    def _on_delete_profile_response(self, _dialog, response, name):
        if response != "delete":
            return
        self.gui.call_async(
            "delete_profile", {"name": name},
            on_done=lambda _r: self.reload(),
            on_error=self.show_error,
        )

    def _on_show_stats(self, *_args):
        StatsDialog(self, self.gui).present()

    # --- Status bar -------------------------------------------------------

    def show_error(self, message: str) -> None:
        self.status_label.set_text(f"⚠  {message}")
        self._status_revealer.add_css_class("error")
        self._status_revealer.set_reveal_child(True)

    def show_info(self, message: str) -> None:
        self.status_label.set_text(message)
        self._status_revealer.remove_css_class("error")
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
            self._report_monitor_status()
            self._reload_profiles()
        except Exception as error:
            self.show_error(str(error))

    def _report_monitor_status(self) -> None:
        """Klaren Hinweis zeigen, wenn keine Prozessdaten kommen koennen."""
        try:
            st = self.gui.call("status")
        except Exception:
            return
        err = st.get("monitor_error")
        if err:
            self.show_error(f"Monitoring disabled — no process list. Cause: {err}")
        elif not st.get("monitoring"):
            self.show_error("Monitoring is not active — no process list available.")

    def _sync_unit_widgets(self) -> None:
        self.table.set_unit(self.unit)
        self.graph.set_unit(self.unit)
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
        iface = state.get("interface", "?")
        # "Global" = echte Rate des Interfaces (aus /proc/net/dev) — korrekt,
        # weil sie auch nicht zuordenbaren Traffic enthaelt.
        g = state.get("global") or {}
        a = state.get("attributed") or {}
        if g.get("download") is None:
            main = f"Global ({iface}):  measuring…"
            graph_d = a.get("download", 0.0)
            graph_u = a.get("upload", 0.0)
        else:
            main = (f"Global ({iface}):"
                    f"   ▼ {format_rate(g.get('download'), self.unit, 1)}"
                    f"   ▲ {format_rate(g.get('upload'), self.unit, 1)}")
            graph_d, graph_u = g.get("download"), g.get("upload")
        if "apps" in state:
            count = len(state.get("apps") or [])
            unit_word = "apps"
        else:
            count = len(processes)
            unit_word = "procs"
        sub = (f"{count} {unit_word}, attributed"
               f" ▼ {format_rate(a.get('download', 0.0), self.unit, 1)}"
               f" ▲ {format_rate(a.get('upload', 0.0), self.unit, 1)}")
        self.total_label.set_text(f"{main}      ·      {sub}")
        self.total_label.set_tooltip_text(
            "Global = real interface throughput (kernel counters, includes "
            "traffic that cannot be attributed to a process).\n"
            "attributed = what nethogs could map to processes.")
        self.graph.push(graph_d, graph_u)
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

        Der Schalter wird sofort umgelegt (optimistisch) und der RPC laeuft im
        Hintergrund — ein tt-Neustart darf das Fenster nicht einfrieren. Bei
        einem Fehler wird der Schalter zurueckgesetzt.
        """
        if self._syncing:
            return False
        enabled = bool(state)
        self._syncing = True
        try:
            switch.set_active(enabled)
        finally:
            self._syncing = False
        self.gui.call_async(
            "toggle_enabled", {"enabled": enabled},
            on_done=lambda _r: self.show_info(
                "Throttling " + ("on" if enabled else "off")),
            on_error=lambda message: self._revert_toggle(not enabled, message),
        )
        return True

    def _revert_toggle(self, active: bool, message: str) -> None:
        self._syncing = True
        try:
            self.toggle_switch.set_active(active)
        finally:
            self._syncing = False
        self.show_error(message)

    def _on_unit(self, dd, *_args):
        if self._syncing:
            return
        idx = dd.get_selected()
        if not (0 <= idx < len(UNIT_IDS)):
            return
        self.unit = UNIT_IDS[idx]
        self.table.set_unit(self.unit)
        self.graph.set_unit(self.unit)
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
            self.gui.call_async(
                "set_global", {key: value},
                on_done=lambda _r: self.show_info("Global limit updated"),
                on_error=self.show_error,
            )
            return False

        old = getattr(self, timer_attr, None)
        if old is not None:
            GLib.source_remove(old)
        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_global_prio(self, dd, *_args):
        if self._syncing:
            return
        name = dd.get_priority_name()
        self.gui.call_async(
            "set_global", {"download_priority": name, "upload_priority": name},
            on_done=lambda _r: self.show_info(f"Global priority: {name}"),
            on_error=self.show_error,
        )

    def _on_destroy(self, *args):
        if hasattr(self, "gui"):
            self.gui.shutdown()

    # Backwards-compatible alias used by ProcessTable
    @property
    def client(self):
        return self.gui


_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _format_bytes(value) -> str:
    """Bytes menschenlesbar formatieren (SI, 1000er-Schritte)."""
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    for unit in _BYTE_UNITS:
        if amount < 1000 or unit == _BYTE_UNITS[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1000.0
    return f"{amount:.1f} {_BYTE_UNITS[-1]}"


class StatsDialog(Adw.Window):
    """Einfache Statistik-Ansicht: App -> Volumen, umschaltbarer Zeitraum.

    Bewusst kein Diagramm — eine Liste genuegt (siehe Aufgabe C).
    """

    WINDOW_CHOICES = (
        ("minute", "Last hour"),
        ("hour", "Last 2 days"),
        ("day", "Last 30 days"),
    )

    def __init__(self, parent, gui):
        super().__init__()
        self.gui = gui
        self.set_title("Throtl — Statistics")
        self.set_default_size(520, 520)
        try:
            self.set_transient_for(parent)
        except (TypeError, AttributeError):
            pass

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Statistics"))
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)
        toolbar.set_content(body)

        controls = Gtk.Box(spacing=8)
        controls.append(Gtk.Label(label="Range"))
        self.window_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        for _key, label in self.WINDOW_CHOICES:
            self.window_dd.get_model().append(Gtk.StringObject.new(label))
        self.window_dd.set_selected(0)
        self.window_dd.connect("notify::selected", lambda *_a: self._refresh())
        controls.append(self.window_dd)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Reload statistics")
        refresh.connect("clicked", lambda *_a: self._refresh())
        controls.append(refresh)
        reset = Gtk.Button(label="Reset")
        reset.add_css_class("destructive-action")
        reset.connect("clicked", self._on_reset)
        controls.append(reset)
        body.append(controls)

        self.totals_label = Gtk.Label(label="", xalign=0.0)
        self.totals_label.add_css_class("dim-label")
        body.append(self.totals_label)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.listbox)
        body.append(scroll)

        self._refresh()

    def _window_key(self) -> str:
        idx = self.window_dd.get_selected()
        if 0 <= idx < len(self.WINDOW_CHOICES):
            return self.WINDOW_CHOICES[idx][0]
        return "minute"

    def _clear_rows(self) -> None:
        child = self.listbox.get_first_child()
        while child is not None:
            self.listbox.remove(child)
            child = self.listbox.get_first_child()

    def _refresh(self) -> None:
        try:
            data = self.gui.call("get_stats", {"window": self._window_key()})
        except Exception as error:
            self.totals_label.set_text(f"⚠  {error}")
            return
        apps = data.get("apps") or []
        totals = data.get("totals") or {}
        self.totals_label.set_text(
            "Total:  ▼ {down}   ▲ {up}".format(
                down=_format_bytes(totals.get("download")),
                up=_format_bytes(totals.get("upload")),
            ))
        self._clear_rows()
        if not apps:
            self.listbox.append(Adw.ActionRow(title="No data recorded yet"))
            return
        for item in apps:
            download = item.get("download", 0.0)
            upload = item.get("upload", 0.0)
            row = Adw.ActionRow(title=str(item.get("app", "?")))
            row.set_subtitle(
                f"▼ {_format_bytes(download)}   ▲ {_format_bytes(upload)}")
            total = Gtk.Label(label=_format_bytes(download + upload))
            total.add_css_class("dim-label")
            row.add_suffix(total)
            self.listbox.append(row)

    def _on_reset(self, *_args):
        self.gui.call_async(
            "reset_stats", {},
            on_done=lambda _r: self._refresh(),
            on_error=lambda message: self.totals_label.set_text(f"⚠  {message}"),
        )


def _load_css() -> None:
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

    def do_startup(self):
        _debug("do_startup: begin")
        Adw.Application.do_startup(self)
        _load_css()
        _debug("do_startup: done")

    def do_activate(self):
        _debug("do_activate")
        if self.window is None:
            self.gui = GuiClient(on_state=self._broadcast_state,
                                 on_error=self._show_daemon_error,
                                 on_connected=self._on_daemon_connected)
            try:
                self.window = ThrotlWindow(self, self.gui)
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
    # --autostart is accepted for the autostart entry; the window is always
    # shown (there is no tray/background mode yet).
    argv = [a for a in argv if a != "--autostart"]

    app = ThrotlApplication()
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
