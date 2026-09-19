"""Reusable GTK4 widgets for the Throtl GUI."""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk

PRIORITY_NAMES = ("kritisch", "hoch", "normal", "niedrig")
PRIORITY_LABELS = {
    "kritisch": "Critical",
    "hoch": "High",
    "normal": "Normal",
    "niedrig": "Low",
}

# Anzeige-Einheiten: (Config-Wert, Label). Gemeinsam fuer app.py und Tabellen.
UNIT_CHOICES = (
    ("mBs", "MB/s"),
    ("mbps", "Mbit/s"),
    ("kBs", "KB/s"),
    ("kbps", "kbit/s"),
)
UNIT_LABELS = dict(UNIT_CHOICES)
UNIT_IDS = [unit for unit, _label in UNIT_CHOICES]


class RateEntry(Gtk.Entry):
    """Input field for a bandwidth rate.

    A bare number is interpreted in the currently selected unit (see
    units.parse_rate_in_unit); an explicit suffix such as '2 kbps' or
    '1.5 MB/s' always wins. Empty means 'unlimited'.
    """

    def __init__(self, unit: str = "mBs"):
        super().__init__(width_chars=8)
        # Adwaita gibt Eingabefeldern eine grosse NATUERLICHE Breite (~168px).
        # Ohne Deckelung werden die Tabellenspalten breiter als der Header und
        # die Spalten laufen auseinander.
        self.set_max_width_chars(9)
        self.add_css_class("throtl-rate-entry")
        self.set_unit_hint(unit)

    def set_unit_hint(self, unit: str):
        label = UNIT_LABELS.get(unit, unit or "")
        self.set_tooltip_text(
            f"Limit in {label}. Leave empty for unlimited. "
            "An explicit suffix (e.g. '2 kbps') also works.")
        self.set_placeholder_text("unlimited")


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
