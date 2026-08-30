"""Live-Prozess-Pane im Hauptfenster."""

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ..units import format_rate


class ProcessPanel(Gtk.Box):
    """Kombination aus Live-Prozessliste (read-only) und Regel-Editor.

    Die Live-Liste zeigt alle aktuell aktiven Prozesse (aus dem Daemon-Monitor).
    Die Regel-Liste verwaltet TrafficToll-Regeln (die Limits scharf schalten).
    """

    def __init__(self, gui, state: dict, unit: str = "kbps"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.gui = gui
        self.unit = unit

        # Live-Prozesse
        live_label = Gtk.Label(label="Aktive Prozesse mit Netzwerkverbindung")
        live_label.add_css_class("dim-label")
        live_label.set_xalign(0.0)
        self.append(live_label)

        self._live_total = Gtk.Label(label="Gesamt: 0 kbit/s von 0 kbit/s")
        self._live_total.set_xalign(0.0)
        self.append(self._live_total)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(140)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroller.set_child(box)
        self._live_box = box
        self.append(scroller)

        # Regel-Kopfzeile (Wird vom Hauptfenster in einem separaten Teil gezeigt)
        rules_header = Gtk.Label(
            label="Regeln (Bandbreiten-Limits & Prioritaeten)")
        rules_header.add_css_class("heading")
        rules_header.set_xalign(0.0)
        self.append(rules_header)

        self._rules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self._rules_box)

        self._add_rule_button = Gtk.Button(label="Neue Regel +")
        self._add_rule_button.connect("clicked", lambda *_: self.gui.add_rule())
        self._rules_box.append(self._add_rule_button)

        self.refresh(state)

    # --- Live-Liste ------------------------------------------------------

    def set_unit(self, unit: str) -> None:
        """Anzeige-Einheit wechseln (kbps|kBs); wirkt beim naechsten refresh."""
        if unit in ("kbps", "kBs"):
            self.unit = unit

    def refresh(self, state: dict) -> None:
        """State vom Daemon in die Live-Anzeige umsetzen (ohne Regel-Editor)."""
        processes = state.get("processes", [])
        total_d = sum(p.get("download", 0.0) for p in processes)
        total_u = sum(p.get("upload", 0.0) for p in processes)
        self._live_total.set_text(
            f"Gesamt: runter {format_rate(total_d, self.unit)} · rauf "
            f"{format_rate(total_u, self.unit)}"
        )

        # childs unter der Live-Liste neu aufbauen (kein virtuelles Modell noetig)
        while (child := self._live_box.get_first_child()) is not None:
            self._live_box.remove(child)

        if not processes:
            placeholder = Gtk.Label(label="Keine aktiven Prozesse mit Traffic.")
            placeholder.add_css_class("dim-label")
            placeholder.set_xalign(0)
            self._live_box.append(placeholder)
            return

        for proc in sorted(processes, key=lambda p: -(p.get("download", 0.0) or 0)):
            row = self._build_live_row(proc)
            self._live_box.append(row)

    def _build_live_row(self, proc: dict) -> Gtk.Widget:
        name = proc.get("name", "?")
        pid = proc.get("pid", "?")
        down = proc.get("download", 0.0)
        up = proc.get("upload", 0.0)
        has_rule = bool(proc.get("rule_name"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badge.add_css_class("cell")

        icon_box = Gtk.Label(label=name[:1].upper() if name else "?")
        icon_box.add_css_class("throtl-icon-circle")
        icon_box.set_width_chars(2)
        badge.append(icon_box)

        name_label = Gtk.Label(label=f"{name}", hexpand=True, xalign=0.0)
        badge.append(name_label)

        if has_rule:
            rule_badge = Gtk.Label(label="●")
            rule_badge.set_tooltip_text("Fuer diesen Prozess ist eine Regel gesetzt")
            badge.append(rule_badge)

        pid_label = Gtk.Label(label=pid)
        pid_label.add_css_class("dim-label")
        badge.append(pid_label)

        badge.append(Gtk.Label(label=f"▼ {format_rate(down, self.unit)}"))
        badge.append(Gtk.Label(label=f"▲ {format_rate(up, self.unit)}"))
        row.append(badge)
        return row

    # --- Regeln ----------------------------------------------------------

    def refresh_rules(self, rules: list) -> None:
        """Regeln-Liste aus dem Daemon rendern (im Hauptfenster selbst)."""
        # Der Regel-Editor wird vom Haupt-App-Container verwaltet; dieser
        # Pane zeigt ihn nur auf Anforderung. Fuer jetzt: delegieren.
        if hasattr(self.gui, "render_rules"):
            self.gui.render_rules(rules)
