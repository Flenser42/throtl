"""Throtl GUI — a per-application bandwidth manager for Linux (GTK4 + libadwaita).

Layout:
    HeaderBar:  [Throttling switch]   [Profile ▾] [Unit ▾] [Menu]
    ─────────────────────────────────────────────────────────────────────
    Banner (only while a persistent problem exists)
    Global limits:  Download [____]  Upload [____]  Priority [▾]   hint…
    Total traffic:  ▼ 12.3 Mbit/s   ▲ 480 kbit/s   N apps · matched to apps …
    Live bandwidth graph (download / upload over time)
    Filter applications…
    Network table:  PID | Process | ▼ Download | ▲ Upload | DL limit | UL limit | Priority

Feedback is a toast for confirmations and a banner for persistent problems
(GNOME HIG); there is no status bar. Keyboard: Ctrl+R reload, Ctrl+I
statistics, Ctrl+S save profile, Ctrl+F focus the filter, Ctrl+1…9 pick a
profile, Esc clears the filter.

All user-facing text is English. Limit fields are displayed and interpreted in
the selected unit (MB/s, Mbit/s, KB/s, kbit/s); an explicit suffix like
"2 kbps" always wins. Editing is debounced, and programmatic updates are
guarded so they never fire daemon calls.
"""

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import __version__ as THROTL_VERSION
from ..units import format_rate, format_rate_for_entry, parse_rate_in_unit
from ..version import RELEASES_URL, fetch_latest, is_newer
from .budget_dialog import BudgetDialog
from .client import GuiClient
from .graph import BandwidthGraph, theme_colors
from .prefs import load_prefs, save_prefs
from .process_pane import ProcessTable
from .widgets import PRIORITY_LABELS, UNIT_CHOICES, UNIT_IDS, UNIT_LABELS

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
        self.set_default_size(1000, 760)
        self.unit = "mBs"
        self._syncing = False
        # Eigener Guard: das Befuellen des Profil-Dropdowns feuert
        # notify::selected. Ohne diesen Guard ruft das activate_profile ->
        # reload() -> _reload_profiles() -> ... in einer Endlosschleife.
        self._loading_profiles = False
        # Budget-Ueberwachung (gedrosselt gepollt) + Dedupe fuer Notifications.
        self._budget_counter = 0
        self._budget_notified = set()
        self._profile_names = []
        self._prefs = load_prefs()
        self._style_manager = Adw.StyleManager.get_default()

        self._build_actions()
        self._apply_appearance()

        # Toast-Overlay umschliesst die Toolbar-Ansicht: Toasts fuer kurzes
        # Feedback ("Rule saved"), der Banner unter der Headerbar fuer
        # dauerhafte Probleme (GNOME-HIG: Toasts/Banners statt Statusleiste).
        self.toast_overlay = Adw.ToastOverlay()
        self.content = Adw.ToolbarView()
        self.toast_overlay.set_child(self.content)
        self.set_content(self.toast_overlay)

        self._build_headerbar()

        self.banner = Adw.Banner(revealed=False)
        self.banner.set_button_label("Dismiss")
        self._banner_action = None
        self.banner.connect("button-clicked", self._on_banner_button)
        self.content.add_top_bar(self.banner)

        self._build_body()
        self._build_shortcuts()

        # Versionscheck: Netzwerk, Browser und Abbruchstelle sind austauschbar,
        # damit Tests ohne Netz und ohne Browser laufen.
        self._update_check_running = False
        self._update_version = None
        self._fetch_latest = fetch_latest
        self._open_uri = _open_uri

        # Graph-Farben folgen Hell/Dunkel-Wechseln.
        self._style_manager.connect("notify::dark",
                                    lambda *_a: self.graph.queue_draw())

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

        # Hauptmenue. Headerbar-Buttons sind icon-only und flach (GNOME-HIG);
        # alles Weitere lebt hier drin.
        menu = Gio.Menu()
        menu.append("Reload", "win.reload")
        menu.append("Statistics…", "win.stats")
        menu.append("Budgets…", "win.budgets")
        profile_menu = Gio.Menu()
        profile_menu.append("Save settings as profile…", "win.save-profile")
        profile_menu.append("Startup profile…", "win.start-profile")
        profile_menu.append("Delete profile", "win.delete-profile")
        menu.append_section("Profile", profile_menu)
        unit_menu = Gio.Menu()
        for unit_id, label in UNIT_CHOICES:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(
                "win.unit", GLib.Variant.new_string(unit_id))
            unit_menu.append_item(item)
        menu.append_section("Display unit", unit_menu)
        updates_menu = Gio.Menu()
        updates_menu.append("Check for updates", "win.check-updates")
        auto_item = Gio.MenuItem.new("Check for updates on start", None)
        auto_item.set_action_and_target_value(
            "win.check-updates-on-start", GLib.Variant.new_boolean(True))
        updates_menu.append_item(auto_item)
        menu.append_section("Updates", updates_menu)
        appearance_menu = Gio.Menu()
        for key, label in (("system", "Follow System"),
                           ("light", "Light"), ("dark", "Dark")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(
                "win.appearance", GLib.Variant.new_string(key))
            appearance_menu.append_item(item)
        menu.append_section("Appearance", appearance_menu)
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("Main menu")
        header.pack_end(menu_btn)

        self._build_profile_controls(header)

    def _build_profile_controls(self, header):
        """Profil-Auswahl in der Headerbar.

        Ohne Textlabel: der Dropdown zeigt den aktiven Namen, der Tooltip
        erklaert ihn (GNOME-Headerbars tragen wenige, klar benannte Elemente).
        """
        box = Gtk.Box(spacing=6)
        self.profile_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        self.profile_dd.set_tooltip_text(
            "Active profile (Ctrl+1…9 switches by position)")
        self.profile_dd.set_size_request(150, -1)
        self.profile_dd.set_valign(Gtk.Align.CENTER)
        self.profile_dd.connect("notify::selected", self._on_profile_selected)
        box.append(self.profile_dd)
        header.pack_end(box)

    def _build_actions(self):
        for name, handler in (
            ("reload", lambda *_a: self.reload()),
            ("save-profile", self._on_save_profile),
            ("delete-profile", self._on_delete_profile),
            ("start-profile", self._on_start_profile),
            ("stats", self._on_show_stats),
            ("budgets", self._on_show_budgets),
            ("check-updates", self._on_check_updates),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        auto_update = Gio.SimpleAction.new_stateful(
            "check-updates-on-start", GLib.VariantType.new("b"),
            GLib.Variant.new_boolean(
                bool(self._prefs.get("check_updates", True))))
        auto_update.connect("activate", self._on_check_updates_on_start)
        self.add_action(auto_update)
        unit = Gio.SimpleAction.new_stateful(
            "unit", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.unit))
        unit.connect("activate", self._on_unit_action)
        self.add_action(unit)
        appearance = Gio.SimpleAction.new_stateful(
            "appearance", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self._prefs.get("appearance", "system")))
        appearance.connect("activate", self._on_appearance)
        self.add_action(appearance)

    def _on_appearance(self, action, param):
        value = param.get_string() if param is not None else "system"
        action.set_state(GLib.Variant.new_string(value))
        self._prefs["appearance"] = value
        save_prefs(self._prefs)
        self._apply_appearance()

    def _apply_appearance(self):
        """Hell/Dunkel/System anwenden (Adw.StyleManager)."""
        scheme = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(self._prefs.get("appearance"), Adw.ColorScheme.DEFAULT)
        self._style_manager.set_color_scheme(scheme)

    def _build_body(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add_css_class("throtl-page")
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_margin_start(12)
        page.set_margin_end(12)
        self.content.set_content(page)

        # --- Global limits (compact card) ---
        globals_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=20)
        globals_card.add_css_class("card")
        globals_card.add_css_class("throtl-globals")

        self.global_dl_entry = self._rate_entry()
        globals_card.append(
            self._labelled("Download limit", self.global_dl_entry, "D"))
        self.global_ul_entry = self._rate_entry()
        globals_card.append(
            self._labelled("Upload limit", self.global_ul_entry, "U"))
        self.global_prio = self._priority_dropdown()
        globals_card.append(
            self._labelled("Priority", self.global_prio, "P"))

        hint = Gtk.Label(
            label="Empty = unlimited. A global cap enables prioritisation.",
            xalign=0.0, wrap=True, hexpand=True)
        hint.add_css_class("dim-label")
        hint.set_valign(Gtk.Align.END)
        globals_card.append(hint)
        page.append(globals_card)

        self.global_dl_entry.connect("changed", self._on_global_dl)
        self.global_ul_entry.connect("changed", self._on_global_ul)
        self.global_prio.connect("notify::selected", self._on_global_prio)

        # --- Totals: zwei Zeilen statt sechs Labels in einer Reihe ---
        self.totals_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=2)
        self.totals_box.add_css_class("throtl-totals")

        # Zeile 1: was die Leitung insgesamt macht.
        self.total_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=8)
        self.total_label = Gtk.Label(xalign=0.0)
        self.total_label.add_css_class("throtl-total")
        self.total_down = Gtk.Label(xalign=0.0)
        self.total_down.add_css_class("rate-down")
        self.total_up = Gtk.Label(xalign=0.0)
        self.total_up.add_css_class("rate-up")
        for widget in (self.total_label, self.total_down, self.total_up):
            self.total_row.append(widget)

        # Zeile 2: der Teil, der sich Anwendungen zuordnen liess.
        self.meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=8)
        self.total_meta = Gtk.Label(xalign=0.0)
        self.total_meta.add_css_class("dim-label")
        self.total_meta_down = Gtk.Label(xalign=0.0)
        self.total_meta_down.add_css_class("rate-down")
        self.total_meta_up = Gtk.Label(xalign=0.0)
        self.total_meta_up.add_css_class("rate-up")
        for widget in (self.total_meta, self.total_meta_down,
                       self.total_meta_up):
            self.meta_row.append(widget)

        self.totals_box.append(self.total_row)
        self.totals_box.append(self.meta_row)
        self.total_label.set_tooltip_text(
            "Total traffic is everything the kernel measured on the network "
            "interface.\n"
            "“matched to apps” is the part that could be mapped to a process.")
        page.append(self.totals_box)

        # --- Graph card ---
        graph_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        graph_card.add_css_class("card")
        graph_card.add_css_class("throtl-graph-card")
        self.graph = BandwidthGraph(max_samples=900, unit=self.unit)
        self.graph.set_vexpand(False)
        graph_card.append(self.graph)
        page.append(graph_card)

        # --- Filter + process table ---
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter applications…")
        self.search_entry.set_hexpand(True)
        self.search_entry.add_css_class("throtl-search")
        self.search_entry.set_tooltip_text(
            "Show only apps whose name or executable matches this text "
            "(Ctrl+F — Esc clears)")
        self.search_entry.connect("search-changed", self._on_search)
        page.append(self.search_entry)

        self.table = ProcessTable(self, unit=self.unit,
                                  on_sort_change=self._on_sort_change)
        saved_sort = self._prefs.get("sort_key")
        if saved_sort in ("pid", "name", "download", "upload", "priority"):
            self.table.set_sort(saved_sort, bool(self._prefs.get("sort_desc", True)))
        saved_filter = self._prefs.get("filter") or ""
        if saved_filter:
            self.search_entry.set_text(saved_filter)
            self.table.set_filter(saved_filter)
        self.table.set_vexpand(True)
        page.append(self.table)

    def _build_shortcuts(self) -> None:
        """Tastaturkuerzel fuer alles, was keine Menue-Action ist.

        Reload/Statistics/Save laufen ueber App-Accels (siehe
        ``ThrotlApplication.do_startup``), damit das Menue sie anzeigt. Hier
        bleiben Fokus, Filter und Profilwahl.
        """
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)

        def add(accel: str, fn) -> None:
            def run(*_args):
                return bool(fn())

            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(run)))

        add("<Primary>f", self._focus_filter)
        add("Escape", self._clear_filter)
        for index in range(9):
            add(f"<Primary>{index + 1}",
                lambda index=index: self._select_profile_index(index))
        self.shortcuts = controller
        self.add_controller(controller)

    def _focus_filter(self) -> bool:
        self.search_entry.grab_focus()
        return True

    def _clear_filter(self) -> bool:
        # False gibt das Ereignis weiter, damit Esc Dialoge weiterhin schliesst.
        if not self.search_entry.get_text():
            return False
        self.search_entry.set_text("")
        return True

    def _select_profile_index(self, index: int) -> bool:
        if index >= len(self._profile_names):
            return False
        self.profile_dd.set_selected(index)
        return True

    def _on_search(self, entry) -> None:
        """Filtertext der Prozessliste anwenden und merken."""
        text = entry.get_text()
        self.table.set_filter(text)
        self._prefs["filter"] = text
        save_prefs(self._prefs)

    def _on_sort_change(self, key: str, desc: bool) -> None:
        """Spalten-Sortierung der Prozessliste merken (User-Prefs)."""
        self._prefs["sort_key"] = key
        self._prefs["sort_desc"] = bool(desc)
        save_prefs(self._prefs)

    def _labelled(self, caption: str, widget, mnemonic: str | None = None):
        """Caption ueber einem Feld; ``mnemonic`` ergaenzt ein Alt-Kuerzel."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text = caption
        if mnemonic:
            text = caption.replace(mnemonic, f"_{mnemonic}", 1)
        label = Gtk.Label(label=text, xalign=0.0)
        label.set_use_underline(bool(mnemonic))
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        if mnemonic:
            label.set_mnemonic_widget(widget)
        box.append(label)
        box.append(widget)
        return box

    def _rate_entry(self) -> Gtk.Entry:
        entry = Gtk.Entry(width_chars=10)
        entry.set_valign(Gtk.Align.CENTER)
        entry.set_placeholder_text("unlimited")
        entry.add_css_class("throtl-rate-entry")
        return entry

    def _priority_dropdown(self):
        from .widgets import PriorityDropdown

        dd = PriorityDropdown()
        dd.set_priority_name("normal")
        dd.set_valign(Gtk.Align.CENTER)
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
        # model.remove_all()/append() aendern die Auswahl und feuern
        # notify::selected — waehrend des Ladens darf das NICHT als Nutzeraktion
        # gelten (sonst Endlosschleife ueber activate_profile -> reload).
        self._loading_profiles = True
        try:
            model.remove_all()
            for name in names:
                model.append(Gtk.StringObject.new(name))
            index = names.index(active) if active in names else 0
            self.profile_dd.set_selected(index)
        finally:
            self._loading_profiles = False

    def _current_profile(self) -> str | None:
        idx = self.profile_dd.get_selected()
        if 0 <= idx < len(self._profile_names):
            return self._profile_names[idx]
        return None

    def _on_profile_selected(self, dd, *_args):
        if self._syncing or self._loading_profiles:
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
        dialog = Adw.AlertDialog(
            heading="Save settings as profile",
            body=("The current global limits and process rules are stored "
                  "under the given name."))
        entry = Gtk.Entry()
        entry.set_placeholder_text("Profile name")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_save_profile_response, entry)
        dialog.present(self)

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
        dialog = Adw.AlertDialog(heading="Delete profile?",
                                 body=f'Delete the profile "{name}"?')
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_profile_response, name)
        dialog.present(self)

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

    def _on_show_budgets(self, *_args):
        BudgetDialog(self, self.gui).present()

    # --- Versionscheck ----------------------------------------------------

    def _should_check(self, manual: bool) -> bool:
        """Manuell immer, automatisch nur wenn in den Prefs erlaubt."""
        return manual or bool(self._prefs.get("check_updates", True))

    def check_for_updates(self, manual: bool = False) -> None:
        """Version im Hintergrund pruefen (nie blockierend, nie ein Dialog)."""
        if not self._should_check(manual) or self._update_check_running:
            return
        self._update_check_running = True
        threading.Thread(target=self._update_worker, args=(manual,),
                         daemon=True).start()

    def _update_worker(self, manual: bool) -> None:
        try:
            latest = self._fetch_latest()
        except Exception:
            latest = None
        GLib.idle_add(self._finish_update_check, latest, manual)

    def _finish_update_check(self, latest, manual: bool):
        self._update_check_running = False
        if latest is None:
            if manual:
                self.show_info("Could not check for updates — are you online?")
            return False
        self._on_update_available(latest, force=manual)
        return False

    def _on_update_available(self, latest: str, force: bool = False) -> None:
        """Neue Version als Banner melden.

        Die App installiert nichts: der Knopf oeffnet die Release-Seite. Eine
        weggeklickte Version kommt nicht wieder, ein manueller Check ignoriert
        das.
        """
        if not is_newer(THROTL_VERSION, latest):
            if force:
                self.show_info(f"Throtl {THROTL_VERSION} is up to date.")
            return
        if not force and self._prefs.get("dismissed_update") == latest:
            return
        self._update_version = latest
        self._banner_action = self._open_release_page
        self.banner.set_title(
            f"Throtl {latest} is available (installed: {THROTL_VERSION}). "
            "Throtl never installs updates by itself.")
        self.banner.set_button_label("Download")
        self.banner.set_revealed(True)

    def _open_release_page(self) -> None:
        """Release-Seite im Standardbrowser oeffnen (kein Root, kein Download)."""
        if self._update_version:
            self._prefs["dismissed_update"] = self._update_version
            save_prefs(self._prefs)
        uri = (f"{RELEASES_URL}/tag/v{self._update_version}"
               if self._update_version else RELEASES_URL)
        try:
            self._open_uri(self, uri)
        except Exception as error:
            self.show_error(f"Could not open the release page: {error}")

    def _on_check_updates(self, *_args):
        self.check_for_updates(manual=True)

    def _on_check_updates_on_start(self, action, param):
        enabled = (param.get_boolean() if param is not None
                   else not action.get_state().get_boolean())
        action.set_state(GLib.Variant.new_boolean(enabled))
        self._prefs["check_updates"] = enabled
        save_prefs(self._prefs)
        if enabled:
            self.check_for_updates()

    def _on_start_profile(self, *_args):
        try:
            result = self.gui.call("list_profiles")
            cfg = self.gui.call("get_config")
        except Exception as error:
            self.show_error(str(error))
            return
        names = ["(none)"] + (result.get("profiles") or [])
        current = cfg.get("start_profile") or "(none)"
        dialog = Adw.AlertDialog(
            heading="Startup profile",
            body=("This profile is activated whenever the daemon starts. A "
                  "matching schedule still takes priority over it."))
        dropdown = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        for name in names:
            dropdown.get_model().append(Gtk.StringObject.new(name))
        dropdown.set_selected(names.index(current) if current in names else 0)
        dialog.set_extra_child(dropdown)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_start_profile_response,
                       dropdown, names)
        dialog.present(self)

    def _on_start_profile_response(self, _dialog, response, dropdown, names):
        if response != "save":
            return
        index = dropdown.get_selected()
        name = names[index] if 0 <= index < len(names) else "(none)"
        payload = {} if name == "(none)" else {"name": name}
        self.gui.call_async(
            "set_start_profile", payload,
            on_done=lambda _r: self.show_info(
                f"Startup profile: {name}"),
            on_error=self.show_error)

    # --- Feedback ---------------------------------------------------------

    def show_error(self, message: str, action_label: str = "Dismiss",
                   action=None) -> None:
        """Dauerhaftes Problem: Banner unter der Headerbar (GNOME-HIG).

        Der Text wird in Klartext uebersetzt; die rohe Meldung bleibt im
        Tooltip und (mit THROTL_DEBUG=1) im Log, damit sie nicht verloren geht.
        """
        title, details, offline = _plain_error(message)
        if offline and action is None:
            action_label, action = "Retry now", self._retry_connection
        self._banner_action = action
        self.banner.set_title(title)
        self.banner.set_button_label(action_label)
        self.banner.set_tooltip_text(details or None)
        _debug(f"error banner: {message}")
        self.banner.set_revealed(True)

    def _on_banner_button(self, *_args) -> None:
        action, self._banner_action = self._banner_action, None
        self.banner.set_revealed(False)
        if action is not None:
            try:
                action()
            except Exception as error:   # ein kaputter Callback darf nichts reissen
                self.show_error(str(error))

    def _retry_connection(self) -> None:
        """Vom Banner aus: sofort neu verbinden statt auf den Poller zu warten."""
        try:
            self.gui.connect(timeout=2.0)
        except Exception:
            return                      # Meldung kommt ueber on_error
        if self.gui.connected:
            self.reload()

    def show_info(self, message: str) -> None:
        """Kurzes Feedback: Toast."""
        self.toast_overlay.add_toast(Adw.Toast.new(str(message)))

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
            self.show_error(f"Live process data is unavailable — {err}",
                            action_label="Retry now", action=self.reload)
        elif not st.get("monitoring"):
            self.show_error("Live process data is unavailable.",
                            action_label="Retry now", action=self.reload)

    def _sync_unit_widgets(self) -> None:
        self.table.set_unit(self.unit)
        self.graph.set_unit(self.unit)
        action = self.lookup_action("unit")
        if action is not None:
            action.set_state(GLib.Variant.new_string(self.unit))
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
        self.total_label.set_text("Total traffic")
        self.total_label.set_tooltip_text(
            f"Everything the kernel measured on {iface}, including traffic that "
            "cannot be matched to an application.\n"
            "“matched to apps” is the part that could be mapped to a process.")
        if g.get("download") is None:
            graph_d = a.get("download", 0.0)
            graph_u = a.get("upload", 0.0)
            for widget in (self.total_down, self.total_up,
                           self.total_meta_down, self.total_meta_up):
                widget.set_text("")
            self.total_meta.set_text("measuring…")
        else:
            graph_d, graph_u = g.get("download"), g.get("upload")
            self.total_down.set_text(
                f"▼ {format_rate(g.get('download'), self.unit, 1)}")
            self.total_up.set_text(
                f"▲ {format_rate(g.get('upload'), self.unit, 1)}")
            if "apps" in state:
                count = len(state.get("apps") or [])
            else:
                count = len(processes)
            self.total_meta.set_text(f"matched to apps ({count})")
            self.total_meta_down.set_text(
                f"▼ {format_rate(a.get('download', 0.0), self.unit, 1)}")
            self.total_meta_up.set_text(
                f"▲ {format_rate(a.get('upload', 0.0), self.unit, 1)}")
        self.graph.push(graph_d, graph_u)
        if not self._syncing:
            enabled = bool(state.get("enabled", True))
            if self.toggle_switch.get_active() != enabled:
                self._syncing = True
                try:
                    self.toggle_switch.set_active(enabled)
                finally:
                    self._syncing = False

        self._budget_counter += 1
        if self._budget_counter % 15 == 0:
            self.gui.call_async("get_budgets", {}, on_done=self._on_budgets)

    def _on_budgets(self, result: dict) -> None:
        """Ueberschrittene Budgets melden (Statusleiste + Desktop-Notification)."""
        exceeded = [e for e in (result.get("entries") or []) if e.get("exceeded")]
        current = {
            f"{e.get('scope')}:{e.get('app')}:{e.get('window')}" for e in exceeded
        }
        # Verlaesst ein Budget das Ueberschreiten, darf spaeter wieder gemeldet werden.
        self._budget_notified &= current
        if not exceeded:
            return
        parts = []
        for entry in exceeded:
            scope = "global" if entry.get("scope") == "global" else entry.get("app")
            parts.append(
                f"{scope} {entry.get('window')} "
                f"{_format_bytes(entry.get('used'))}/{_format_bytes(entry.get('limit'))}"
            )
        self.show_error("Budget exceeded — " + "; ".join(parts))
        for entry in exceeded:
            scope = "global" if entry.get("scope") == "global" else entry.get("app")
            key = f"{entry.get('scope')}:{entry.get('app')}:{entry.get('window')}"
            if key in self._budget_notified:
                continue
            self._budget_notified.add(key)
            note = Gio.Notification.new(f"Throtl: {scope} budget exceeded")
            note.set_body(
                f"{entry.get('window')}: {_format_bytes(entry.get('used'))} of "
                f"{_format_bytes(entry.get('limit'))}"
            )
            try:
                self.app.send_notification(f"throtl-budget-{key}", note)
            except Exception:
                pass

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

    def _on_unit_action(self, action, param) -> None:
        """Menue-Action "win.unit": Anzeigeeinheit wechseln."""
        unit = param.get_string() if param is not None else "mBs"
        if unit not in UNIT_IDS:
            return
        action.set_state(GLib.Variant.new_string(unit))
        self._set_unit(unit)

    def _set_unit(self, unit: str) -> None:
        """Einheit auf Tabelle, Graph, Felder und Daemon anwenden."""
        if unit not in UNIT_IDS:
            return
        self.unit = unit
        self.table.set_unit(self.unit)
        self.graph.set_unit(self.unit)
        hint = f"limit in {UNIT_LABELS.get(self.unit, 'MB/s')}"
        for entry in (self.global_dl_entry, self.global_ul_entry):
            entry.set_placeholder_text(hint)
        if self._syncing:
            return
        # Global limit fields zeigen denselben Wert in der neuen Einheit
        try:
            cfg = self.gui.call("get_config")
            self._sync_global_fields(cfg)
            self.gui.call("set_unit", {"unit": self.unit})
        except Exception as error:
            self.show_error(str(error))

    def _on_global_dl(self, entry, *_args):
        self._debounce_global("download_limit", entry, "Download limit")

    def _on_global_ul(self, entry, *_args):
        self._debounce_global("upload_limit", entry, "Upload limit")

    def _debounce_global(self, key, entry, label: str):
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
                on_done=lambda _r: self.show_info(f"{label} updated"),
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
        label = PRIORITY_LABELS.get(name, name)
        self.gui.call_async(
            "set_global", {"download_priority": name, "upload_priority": name},
            on_done=lambda _r: self.show_info(f"Global priority: {label}"),
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


def _open_uri(parent, uri: str) -> None:
    """Externe URL im Standardbrowser oeffnen.

    Die App laedt nichts herunter und installiert nichts — sie zeigt nur die
    Release-Seite; das Update macht der Nutzer selbst.
    """
    try:
        Gtk.UriLauncher.new(uri).launch(parent, None, None)
    except Exception:
        Gio.AppInfo.launch_default_for_uri(uri, None)


def _plain_error(message: str) -> tuple[str, str, bool]:
    """Rohe Daemon-/Socket-Meldung in Klartext uebersetzen.

    Rueckgabe: (Satz fuer das Banner, Detailtext fuer den Tooltip, ob es ein
    Verbindungsproblem ist). Unbekannte Meldungen werden unveraendert
    durchgelassen — eine erfundene Ursache waere schlimmer als der Rohtext.
    """
    text = str(message).strip()
    low = text.lower()
    first = text.splitlines()[0].strip() if text else ""
    # Gruppierungs- und Rechteprobleme zuerst: die Diagnose des Daemons ist
    # wertvoller als jede generische Meldung.
    if any(marker in low for marker in
           ("newgrp", "group", "permission", "access denied", "errno 13")):
        return (first or "Permission denied.", text, False)
    if any(marker in low for marker in
           ("connection refused", "connection reset", "broken pipe",
            "no such file", "socket", "connect", "timed out", "timeout")):
        return (f"{first or 'The Throtl service is not reachable.'} "
                "Reconnecting automatically.", text, True)
    return (text or "Something went wrong.", "", False)


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


class StatsDialog(Adw.Dialog):
    """Statistik-Ansicht: Volumen pro App + Verlaufsgraph je Zeitraum.

    Tabelle + Balkengraph (Download gruen, Upload orange) mit umschaltbarem
    Zeitraum (1 h / 2 Tage / 30 Tage). Ein ``Adw.Dialog`` (GNOME-HIG) statt
    eines eigenen Fensters; die Graphfarben folgen dem System-Theme.
    """

    WINDOW_CHOICES = (
        ("minute", "Last hour"),
        ("hour", "Last 2 days"),
        ("day", "Last 30 days"),
    )

    def __init__(self, parent, gui):
        super().__init__()
        self.gui = gui
        self._parent = parent
        self.set_title("Statistics")
        self.set_content_width(520)
        self.set_content_height(520)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

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

        # Verlaufsgraph: Download (gruen) + Upload (orange) je Bucket.
        self._series = []
        self.graph = Gtk.DrawingArea()
        self.graph.set_content_height(150)
        self.graph.set_draw_func(self._draw_graph)
        self.graph.add_css_class("throtl-graph")
        body.append(self.graph)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.listbox)
        body.append(scroll)

        # Graphfarben folgen Hell/Dunkel-Wechseln (wie der Live-Graph).
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_a: self.graph.queue_draw())

        self._refresh()

    def present(self):  # noqa: D102 - Adw.Dialog.present(parent)
        Adw.Dialog.present(self, self._parent)

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
            history = self.gui.call("get_stats_history",
                                    {"window": self._window_key()})
        except Exception as error:
            self.totals_label.set_text(f"⚠  {error}")
            return
        self._series = history.get("series") or []
        self.graph.queue_draw()
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

    def _draw_graph(self, _area, cr, width, height, _data):
        """Balken je Bucket: unten Download (gruen), darueber Upload (orange)."""
        colors = theme_colors()
        cr.set_source_rgba(*colors["bg"])
        cr.rectangle(0, 0, width, height)
        cr.fill()
        series = self._series
        if not series:
            cr.set_source_rgba(*colors["text"])
            cr.set_font_size(11)
            cr.move_to(8, height / 2)
            cr.show_text("No data yet")
            return
        peak = max((s.get("download", 0.0) + s.get("upload", 0.0))
                   for s in series) or 1.0
        count = len(series)
        slot = width / count
        bar = max(1.0, slot * 0.72)
        base = height - 18.0
        for index, sample in enumerate(series):
            total = sample.get("download", 0.0) + sample.get("upload", 0.0)
            if total <= 0:
                continue
            bar_h = (total / peak) * (base - 8.0)
            down_h = (sample.get("download", 0.0) / total) * bar_h
            x = index * slot + (slot - bar) / 2.0
            cr.set_source_rgba(*colors["down"])
            cr.rectangle(x, base - down_h, bar, down_h)
            cr.fill()
            cr.set_source_rgba(*colors["up"])
            cr.rectangle(x, base - bar_h, bar, bar_h - down_h)
            cr.fill()
        # Grundlinie + Maximalwert
        cr.set_source_rgba(*colors["grid"])
        cr.set_line_width(1.0)
        cr.move_to(0, base)
        cr.line_to(width, base)
        cr.stroke()
        cr.set_source_rgba(*colors["text"])
        cr.set_font_size(10)
        cr.move_to(6, 12)
        cr.show_text(_format_bytes(peak))

    def _on_reset(self, *_args):
        """Zuruecksetzen loescht Verlauf — vorher nachfragen."""
        dialog = Adw.AlertDialog(
            heading="Reset statistics?",
            body="This erases the recorded per-application history. Budgets "
                 "and this view start from zero again. It cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_response)
        dialog.present(self)

    def _on_reset_response(self, _dialog, response):
        if response != "reset":
            return
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
        # Menue-Actions bekommen ihre Kuerzel hier, damit das Menue sie anzeigt.
        for action, accel in (("win.reload", "<Primary>r"),
                              ("win.stats", "<Primary>i"),
                              ("win.save-profile", "<Primary>s")):
            self.set_accels_for_action(action, [accel])
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
        # Erst wenn das Fenster steht: Versionstill im Hintergrund pruefen.
        self.window.check_for_updates()

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
