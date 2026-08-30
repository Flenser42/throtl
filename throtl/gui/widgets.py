"""GTK-Widget: Ein Reihe fuer eine Regel (Name, Limits, Prioritaet, Loeschen).

Speichert jede Aenderung sofort an den Daemon ueber den aufrufbaren ``apply``.
"""

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Gio  # noqa: F401

from ..config import PRIORITY_NAMES, priority_to_int


PRIORITY_LABELS = {
    "kritisch": "Kritisch",
    "hoch": "Hoch",
    "normal": "Normal",
    "niedrig": "Niedrig",
}


class RateEntry(Gtk.Entry):
    """Eingabefeld fuer eine Rate (kbit/s oder flexible Einheit)."""

    def __init__(self):
        super().__init__(placeholder_text="unbegrenzt", width_chars=10)
        self.add_css_class("throtl-rate-entry")


class PriorityDropdown(Gtk.DropDown):
    """Prioritaet-Dropdown (kritisch..niedrig)."""

    def __init__(self):
        model = Gio.ListStore.new(Gtk.StringObject)
        for name in PRIORITY_NAMES:
            model.append(Gtk.StringObject.new(PRIORITY_LABELS.get(name, name)))
        self._names = list(PRIORITY_NAMES)
        super().__init__(model=model, factory=None)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        self.set_factory(factory)
        self._pending = False

    @staticmethod
    def _on_factory_setup(factory, list_item):
        label = Gtk.Label()
        list_item.set_child(label)

    @staticmethod
    def _on_factory_bind(factory, list_item):
        label = list_item.get_child()
        string_obj = list_item.get_item()
        label.set_text(string_obj.get_string())

    def get_priority_name(self) -> str:
        pos = self.get_selected()
        if pos < 0:
            return "normal"
        return self._names[pos]

    def set_priority_name(self, name: str) -> None:
        if name in self._names:
            self.set_selected(self._names.index(name))
