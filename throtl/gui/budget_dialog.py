"""Budgets bearbeiten: Volumen pro Tag/Woche, global und pro Anwendung.

Bisher konnte die GUI Budgets nur *melden* (Banner + Desktop-Notification);
anlegen und aendern ging ausschliesslich ueber ``throtl-cli budget-set``. Fuer
den Fall, dass die App ohne Terminal benutzt wird, schliesst dieser Dialog die
Luecke.

RPC: ``get_budgets``, ``set_budget`` (``app``/``day``/``week``/``enabled``),
``remove_budget``. Die Zuordnung der Antwort in Anzeigezeilen macht
``budgets.budget_rows`` (rein und ohne Widgets); hier bleibt nur die
Oberflaeche.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from ..budgets import budget_rows, format_volume
from ..units import parse_size

# Fenster, die ein Budget kennen kann.
WINDOWS = (("day", "Daily"), ("week", "Weekly"))


class AppBudgetRow:
    """Eine Anwendungszeile im Dialog (App-Name + Widgets)."""

    __slots__ = ("app", "row")

    def __init__(self, app: str, row):
        self.app = app
        self.row = row


class BudgetDialog(Adw.Dialog):
    """Editor fuer Verbrauchs-Budgets."""

    def __init__(self, parent, gui, preselect: str | None = None):
        super().__init__()
        self.gui = gui
        self._parent = parent
        self.app_rows: list[AppBudgetRow] = []
        self._apps: list[str] = []

        self.set_title("Budgets")
        self.set_content_width(560)
        self.set_content_height(600)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.error_banner = Adw.Banner(revealed=False)
        self.error_banner.set_button_label("Dismiss")
        self.error_banner.connect(
            "button-clicked", lambda *_a: self.error_banner.set_revealed(False))
        toolbar.add_top_bar(self.error_banner)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)
        toolbar.set_content(body)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self.set_child(self._toasts)

        body.append(self._build_switch_group())
        body.append(self._build_global_group())
        body.append(self._build_apps_group())
        body.append(self._build_add_group())

        self._reload()
        if preselect:
            self._preselect(preselect)

    def _preselect(self, app: str) -> None:
        """Aus einer Tabellenzeile heraus eine App vorwaehlen.

        Die App laeuft vielleicht gerade nicht mehr, ist aber der Grund, warum
        der Dialog offen ist — also aufnehmen statt ignorieren.
        """
        if app and app not in self._apps:
            self._apps.append(app)
            self._apps.sort()
            model = self.add_dd.get_model()
            model.remove_all()
            for name in self._apps:
                model.append(Gtk.StringObject.new(name))
        if app in self._apps:
            self.add_dd.set_selected(self._apps.index(app))
            self.add_dd.set_sensitive(True)
            self.add_day.grab_focus()

    # --- Aufbau -----------------------------------------------------------

    def _build_switch_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        row = Adw.ActionRow(
            title="Budgets enabled",
            subtitle="Warn and notify when a rolling limit is reached. "
                     "Shaping keeps working when this is off.")
        self.enabled_switch = Gtk.Switch()
        self.enabled_switch.set_valign(Gtk.Align.CENTER)
        self.enabled_switch.connect("notify::active", self._on_enabled)
        row.add_suffix(self.enabled_switch)
        row.set_activatable_widget(self.enabled_switch)
        group.add(row)
        return group

    def _build_global_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="All applications",
            description="Rolling volume for the whole connection. "
                        "Leave a field empty for no limit.")
        self.global_day = Adw.EntryRow(title="Daily limit")
        self.global_day.set_show_apply_button(True)
        self.global_day.connect("apply", lambda *_a: self._save_global())
        self.global_week = Adw.EntryRow(title="Weekly limit")
        self.global_week.set_show_apply_button(True)
        self.global_week.connect("apply", lambda *_a: self._save_global())
        group.add(self.global_day)
        group.add(self.global_week)
        save = Gtk.Button(label="Save limits")
        save.add_css_class("suggested-action")
        save.set_halign(Gtk.Align.START)
        save.connect("clicked", lambda *_a: self._save_global())
        group.add(save)
        return group

    def _build_apps_group(self) -> Adw.PreferencesGroup:
        """Bestehende App-Budgets.

        Adw.PreferencesGroup sammelt alle ``Adw.PreferencesRow``-Kinder in
        einer eigenen Liste und haengt andere Widgets DANACH an. Deshalb ist
        hier alles eine Row (auch der Leerzustand) und das Hinzufuegen-Formular
        liegt in einer eigenen Gruppe.
        """
        group = Adw.PreferencesGroup(title="Per application")
        self.apps_group = group
        self.empty_row = Adw.ActionRow(
            title="No application budget yet",
            subtitle="A budget warns you when an application has used a set "
                     "volume within the last 24 hours or 7 days.")
        group.add(self.empty_row)
        return group

    def _build_add_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Add a budget",
            description="Only applications that currently use the network "
                        "can be chosen.")
        self.add_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        self.add_dd.set_valign(Gtk.Align.CENTER)
        app_row = Adw.ActionRow(title="Application")
        app_row.add_suffix(self.add_dd)
        group.add(app_row)

        self.add_day = Adw.EntryRow(title="Daily limit")
        self.add_week = Adw.EntryRow(title="Weekly limit")
        group.add(self.add_day)
        group.add(self.add_week)

        add = Gtk.Button(label="Add budget")
        add.add_css_class("suggested-action")
        add.set_halign(Gtk.Align.START)
        add.connect("clicked", lambda *_a: self._on_add_clicked())
        group.add(add)
        self.add_button = add
        return group

    # --- Daten ------------------------------------------------------------

    def _reload(self, result=None) -> None:
        payload = result
        if payload is None:
            try:
                payload = self.gui.call("get_budgets")
            except Exception as error:
                self._show_error(str(error))
                payload = {"enabled": True, "entries": []}
        rows = budget_rows(payload)

        self._syncing = True
        try:
            self.enabled_switch.set_active(rows["enabled"])
            self.global_day.set_text(format_volume(
                (rows["global"]["day"] or {}).get("limit")))
            self.global_week.set_text(format_volume(
                (rows["global"]["week"] or {}).get("limit")))
        finally:
            self._syncing = False

        self._fill_app_choices()
        self._rebuild_app_rows(rows["apps"])

    def _fill_app_choices(self) -> None:
        try:
            state = self.gui.call("list_processes")
        except Exception:
            state = {}
        names = []
        for app in state.get("apps") or state.get("processes") or []:
            if app.get("unattributed"):
                continue
            name = str(app.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        self._apps = sorted(names)
        model = self.add_dd.get_model()
        model.remove_all()
        for name in self._apps:
            model.append(Gtk.StringObject.new(name))
        self.add_dd.set_sensitive(bool(self._apps))

    def _rebuild_app_rows(self, apps: list) -> None:
        for holder in self.app_rows:
            self.apps_group.remove(holder.row)
        self.app_rows = []
        if not apps:
            self.apps_group.add(self.empty_row)
            return
        self.apps_group.remove(self.empty_row)
        for entry in apps:
            row = self._app_row(entry)
            self.apps_group.add(row)
            self.app_rows.append(AppBudgetRow(entry["app"], row))

    def _app_row(self, entry: dict) -> Adw.ActionRow:
        parts = []
        for window, label in WINDOWS:
            cell = entry.get(window)
            if not cell:
                continue
            text = (f"{label} {format_volume(cell['used'])} of "
                    f"{format_volume(cell['limit'])}")
            # Der Ueberschreitungsfall darf nicht allein an der Farbe haengen.
            if cell["exceeded"]:
                text += " — over budget"
            parts.append(text)
        row = Adw.ActionRow(title=entry["app"], subtitle=" · ".join(parts))
        if any((entry.get(w) or {}).get("exceeded") for w, _ in WINDOWS):
            row.add_css_class("error")
        remove = Gtk.Button(icon_name="user-trash-symbolic")
        remove.add_css_class("flat")
        remove.set_valign(Gtk.Align.CENTER)
        remove.set_tooltip_text(f"Remove the budget for {entry['app']}")
        remove.connect("clicked", lambda *_a, app=entry["app"]:
                       self._remove_app(app))
        row.add_suffix(remove)
        return row

    # --- Aktionen ---------------------------------------------------------

    def _on_enabled(self, switch, _pspec=None) -> None:
        if getattr(self, "_syncing", False):
            return
        self._send({"enabled": bool(switch.get_active())},
                   on_done=lambda _r: self._saved("Budgets "
                                                  + ("enabled"
                                                     if switch.get_active()
                                                     else "disabled")))

    def _save_global(self) -> None:
        try:
            day = parse_size(self.global_day.get_text())
            week = parse_size(self.global_week.get_text())
        except ValueError as error:
            self._show_error(f"Invalid limit: {error}")
            return
        self._send({"day": day, "week": week},
                   on_done=lambda _r: self._saved("Global limits saved"))

    def _on_add_clicked(self) -> None:
        self._add_app(self._current_app(), self.add_day.get_text(),
                      self.add_week.get_text())

    def _current_app(self) -> str:
        index = self.add_dd.get_selected()
        if 0 <= index < len(self._apps):
            return self._apps[index]
        return ""

    def _add_app(self, app: str, day_text: str, week_text: str) -> None:
        app = (app or "").strip()
        if not app:
            self._show_error("Choose an application first.")
            return
        try:
            day = parse_size(day_text)
            week = parse_size(week_text)
        except ValueError as error:
            self._show_error(f"Invalid limit: {error}")
            return
        if day is None and week is None:
            self._show_error("Enter a daily or weekly limit for this "
                             "application.")
            return
        params = {"app": app}
        if day is not None:
            params["day"] = day
        if week is not None:
            params["week"] = week
        self._send(params, on_done=lambda _r: self._added(app))

    def _remove_app(self, app: str) -> None:
        self._send({"app": app}, method="remove_budget",
                   on_done=lambda _r: self._saved(f"Budget for {app} removed"))

    def _send(self, params: dict, method: str = "set_budget", on_done=None) -> None:
        def done(result):
            self._reload()
            if on_done is not None:
                on_done(result)

        self.gui.call_async(method, params, on_done=done,
                            on_error=lambda message: self._show_error(message))

    def _saved(self, message: str) -> None:
        self.error_banner.set_revealed(False)
        self._toasts.add_toast(Adw.Toast.new(message))

    def _added(self, app: str) -> None:
        self.add_day.set_text("")
        self.add_week.set_text("")
        self._saved(f"Budget for {app} saved")

    def _show_error(self, message: str) -> None:
        self.error_banner.set_title(str(message))
        self.error_banner.set_revealed(True)

    def present(self):  # noqa: D102 - Adw.Dialog.present(parent)
        Adw.Dialog.present(self, self._parent)
