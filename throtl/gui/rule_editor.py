"""Regel-Editor: interaktive Liste der TrafficToll-Regeln.

Jede Zeile steuert eine Regel: Live-Limit-Eingaben (download/upload), ein
Prioritaets-Dropdown und ein Loeschen-Button. Aenderungen werden sofort an den
Daemon geschickt.
"""

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ..units import parse_rate
from .widgets import RateEntry, PriorityDropdown


def _parse_or_none(text: str):
    text = (text or "").strip()
    if not text or text in ("unbegrenzt", "∞", "none", "-"):
        return None
    try:
        return parse_rate(text)
    except ValueError:
        return None


class RuleRow(Gtk.Box):
    """Eine Zeile im Regel-Editor."""

    def __init__(self, editor, rule: dict, unit: str = "kbps"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.editor = editor
        self.rule = dict(rule)
        self.unit_loss = False
        self.set_margin_top(2)

        sest = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        name_label = Gtk.Label(label=rule.get("name") or rule.get("key", ""),
                               xalign=0.0, wrap=True)
        name_label.add_css_class("bold")
        sest.append(name_label)
        key_label = Gtk.Label(
            label=f"{rule.get('match_type')}:{rule.get('match_value','')}",
            xalign=0.0)
        key_label.add_css_class("dim-label")
        key_label.set_tooltip_text("Match-Kriterium fuer TrafficToll")
        sest.append(key_label)
        self.append(sest)

        # Download- und Upload-Limit-Spinner
        self.dl_entry = RateEntry()
        self.dl_entry.set_text(_limit_text(rule.get("download_limit")))
        self.dl_entry.connect("activate", self._on_dl_commit)
        self.dl_entry.connect("focus-out-event", self._on_dl_commit)
        self.append(self._make_caption("DL", self.dl_entry))

        self.ul_entry = RateEntry()
        self.ul_entry.set_text(_limit_text(rule.get("upload_limit")))
        self.ul_entry.connect("activate", self._on_ul_commit)
        self.ul_entry.connect("focus-out-event", self._on_ul_commit)
        self.append(self._make_caption("UL", self.ul_entry))

        self.prio_dd = PriorityDropdown()
        self.prio_dd.set_priority_name(rule.get("priority", "normal"))
        self.prio_dd.connect("notify::selected", self._on_priority)
        self.append(self.prio_dd)

        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.add_css_class("destructive-action")
        remove_btn.set_tooltip_text("Regel entfernen")
        remove_btn.connect("clicked", self.editor.remove_rule, self.rule)
        self.append(remove_btn)

    def _make_caption(self, label, widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        caption = Gtk.Label(label=label)
        caption.add_css_class("dim-label")
        box.append(caption)
        box.append(widget)
        return box

    def _on_dl_commit(self, *args):
        value = _parse_or_none(self.dl_entry.get_text())
        if value is not None and self.rule.get("download_limit") != value:
            self._apply({"download_limit": value})
        elif value is None and self.rule.get("download_limit") is not None:
            self._apply({"download_limit": None})

    def _on_ul_commit(self, *args):
        value = _parse_or_none(self.ul_entry.get_text())
        if self.rule.get("upload_limit") != value:
            self._apply({"upload_limit": value})

    def _on_priority(self, *args):
        name = self.prio_dd.get_priority_name()
        if self.rule.get("priority") != name:
            self._apply({"priority": name})

    def _apply(self, changes: dict) -> None:
        params = dict(self.rule)
        params.update(changes)
        try:
            updated = self.editor.gui.client.call("set_process", params)
            self.rule = updated
        except Exception as error:
            self.editor.gui.show_error(str(error))


def _limit_text(kbps):
    if kbps is None:
        return ""
    return str(int(kbps))


class RuleEditor(Gtk.Box):
    """Container fuer mehrere RuleRow."""

    def __init__(self, gui, unit: str = "kbps"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.gui = gui
        self.unit = unit
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(160)
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroller.set_child(self.inner)
        self.append(self.scroller)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_btn = Gtk.Button(label="Neue Regel")
        add_btn.connect("clicked", lambda *_: self.add_blank())
        toolbar.append(add_btn)
        hint = Gtk.Label(label="Feld leer = unbegrenzt", xalign=1.0)
        hint.add_css_class("dim-label")
        toolbar.append(hint)
        self.append(toolbar)

        self._rules = []
        self.refresh([])

    def refresh(self, rules: list) -> None:
        """Regeln aus dem Daemon rendern (immer deren aktuelle Werte)."""
        self._clear_children()
        self._rules = list(rules or [])
        for rule in self._rules:
            self.inner.append(RuleRow(self, rule, self.unit))
        if not self._rules:
            empty = Gtk.Label(label="Noch keine Regeln. Fuege eine hinzu, um "
                                    "ein Limit/Prioritaet zu setzen.")
            empty.add_css_class("dim-label")
            empty.set_xalign(0)
            self.inner.append(empty)

    def _clear_children(self) -> None:
        while (child := self.inner.get_first_child()) is not None:
            self.inner.remove(child)

    def add_blank(self) -> None:
        """Neue-Regel-Dialog: Name, Match-Typ und Match-Ziel erfragen.

        Erzeugt bewusst KEINE automatisch-aktive Regel (eine cmdline:.*-Regel
        wuerde alle Prozesse matchen). Stattdessen ein Eingabedialog.
        """
        dialog = Gtk.AlertDialog()
        dialog.set_title("Neue Regel")
        dialog.set_message("Lege ein Bandbreiten-Limit / Prioritaet für einen "
                           "Prozess fest.")

        name_entry = Gtk.Entry(placeholder_text="Name (z.B. Firefox)")
        exe_entry = Gtk.Entry(placeholder_text="exe-Pfad (z.B. /usr/bin/firefox)")
        dl_entry = RateEntry()
        dl_entry.set_placeholder_text("Download-Limit (leer = unbegrenzt)")
        ul_entry = RateEntry()
        ul_entry.set_placeholder_text("Upload-Limit (leer = unbegrenzt)")
        prio = PriorityDropdown()
        prio.set_priority_name("normal")

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form.append(Gtk.Label(label="Name:", xalign=0))
        form.append(name_entry)
        form.append(Gtk.Label(label="Prozess (exe-Pfad):", xalign=0))
        form.append(exe_entry)
        form.append(Gtk.Label(label="Download-Limit:", xalign=0))
        form.append(dl_entry)
        form.append(Gtk.Label(label="Upload-Limit:", xalign=0))
        form.append(ul_entry)
        form.append(Gtk.Label(label="Prioritaet:", xalign=0))
        form.append(prio)

        extra = Gtk.Box()
        extra.append(form)
        dialog.set_extra_child(extra)

        def _on_response(dialog_, response):
            if response != Gtk.ResponseType.ACCEPT:
                return
            name = name_entry.get_text().strip() or "Regel"
            exe = exe_entry.get_text().strip()
            self._submit_new_rule(name, exe, dl_entry.get_text(),
                                  ul_entry.get_text(), prio.get_priority_name())

        dialog.connect("response", _on_response)
        dialog.show(parent=self.gui.window)

    def _submit_new_rule(self, name, exe, dl_text, ul_text, priority):
        # Ein konkreter exe-Pfad wird benoetigt; ohne ihn verweigern wir, um
        # kein versehentliches Alles-Matchen (cmdline:.*) zuzulassen.
        from ..units import parse_rate

        if not exe:
            self.gui.show_error("Bitte einen exe-Pfad angeben (z.B. /usr/bin/firefox).")
            return

        def rate(text):
            text = (text or "").strip()
            if not text:
                return None
            return parse_rate(text)

        rule = {
            "name": name,
            "match_type": "exe",
            "match_value": exe,   # wird im Daemon re.escape-t -> Literal-Match
            "download_limit": rate(dl_text),
            "upload_limit": rate(ul_text),
            "priority": priority,
            "recursive": False,
        }
        try:
            self.gui.client.call("set_process", rule)
            self.gui.reload()
        except Exception as error:
            self.gui.show_error(str(error))

    def remove_rule(self, btn, rule) -> None:
        try:
            self.gui.client.call("remove_process", {"key": rule.get("key")})
            self.gui.reload()
        except Exception as error:
            self.gui.show_error(str(error))
