"""Reusable GTK4 widgets for the Throtl GUI."""

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Gio


PRIORITY_NAMES = ("kritisch", "hoch", "normal", "niedrig")
PRIORITY_LABELS = {
    "kritisch": "Critical",
    "hoch": "High",
    "normal": "Normal",
    "niedrig": "Low",
}


class RateEntry(Gtk.Entry):
    """Input field for a bandwidth rate (accepts e.g. '2 MB/s', '512 kbps').

    Empty text means 'unlimited'. The placeholder hints at the current unit.
    """

    def __init__(self, unit_hint: str = ""):
        super().__init__(width_chars=12)
        self.add_css_class("throtl-rate-entry")
        self._unit_hint = unit_hint
        self.set_placeholder_text("unlimited")

    def set_unit_hint(self, unit_hint: str):
        self._unit_hint = unit_hint
        if unit_hint:
            self.set_placeholder_text(f"limit in {unit_hint}")


class PriorityDropdown(Gtk.DropDown):
    """Priority dropdown (Critical / High / Normal / Low). Stores symbolic name."""

    def __init__(self):
        self._names = list(PRIORITY_NAMES)
        model = Gio.ListStore.new(Gtk.StringObject)
        for name in self._names:
            model.append(Gtk.StringObject.new(PRIORITY_LABELS.get(name, name)))
        super().__init__(model=model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        self.set_factory(factory)

    @staticmethod
    def _on_factory_setup(factory, list_item):
        list_item.set_child(Gtk.Label(xalign=0))

    @staticmethod
    def _on_factory_bind(factory, list_item):
        item = list_item.get_item()
        list_item.get_child().set_text(item.get_string())

    def get_priority_name(self) -> str:
        pos = self.get_selected()
        if pos < 0 or pos >= len(self._names):
            return "normal"
        return self._names[pos]

    def set_priority_name(self, name: str) -> None:
        if name in self._names:
            self.set_selected(self._names.index(name))


def _priority_name_list() -> list:
    return list(PRIORITY_NAMES)
