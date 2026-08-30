"""Regel-Editor: interaktive Liste der TrafficToll-Regeln.

Jede Zeile steuert eine Regel: Live-Limit-Eingaben (download/upload), ein
Prioritaets-Dropdown und ein Loeschen-Button. Aenderungen werden sofort an den
Daemon geschickt.
"""

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GObject, Gio

from ..config import PRIORITY_NAMES
from ..units import parse_rate
from .widgets import RateEntry, PriorityDropdown, PRIORITY_LABELS


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
        # Einfache Neue-Regel-Dialog: Name + exe-Pfad erfragen
        name = "Neue Regel"
        # Minimale Interaktion: fuege eine leere (cmdline)Regel fuer einen
        # nächsten Einrichtungs-Schritt hinzu — praktisch offen lassen.
        rule = {"name": name, "match_type": "cmdline", "match_value": ".*",
                "download_limit": None, "upload_limit": None,
                "priority": "normal", "recursive": False,
                "key": "cmdline:.*"}
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
