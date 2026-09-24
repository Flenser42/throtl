"""Network traffic table: one editable row per active application.

Each row shows a process's live download/upload rates and lets the user set a
per-process download/upload limit + priority. Setting a limit creates/updates a
TrafficToll rule (matched by exe/name) via the daemon.

Design notes:
* The table is a vertical Box (header + one expanding ScrolledWindow), so the
  list grows to the bottom edge of the window.
* Rows are built once and updated IN PLACE on every poll — only the rate labels
  change, so typing in the limit fields keeps focus.
* Rows are re-ordered in place according to the current sort (default:
  download rate, highest first). Click a column header to sort by it; click
  again to flip the direction.
* Limit fields are shown and parsed in the currently selected unit.
* Programmatic widget updates are wrapped in ``_syncing`` so they never fire
  daemon RPCs.
"""

import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..config import format_window, normalize_window
from ..monitor import UNATTRIBUTED_NAME
from ..rowinfo import explain
from ..units import format_rate, format_rate_for_entry, parse_rate_in_unit
from .widgets import UNIT_LABELS, PriorityDropdown, RateEntry

# (sort key | None, title, width) — identische Breiten fuer Kopf und Zeilen
_COLUMNS = (
    ("pid", "PID", 66),
    ("name", "Process", 200),
    ("download", "Download", 116),
    ("upload", "Upload", 116),
    (None, "DL limit", 112),
    (None, "UL limit", 112),
    ("priority", "Priority", 124),
)

# Sortier-Richtung, die beim Klick auf eine Spalte sinnvoll ist
_DEFAULT_DESC = {"download": True, "upload": True, "name": False,
                 "pid": False, "priority": False}

_PRIORITY_RANK = {"kritisch": 0, "hoch": 1, "normal": 2, "niedrig": 3}


def _first_token(raw: str) -> str:
    """nethogs liefert als 'name' die ganze Kommandozeile; das erste Token ist
    das eigentliche Binary (z.B. '/usr/lib/electron43/electron')."""
    parts = (raw or "").split()
    return parts[0] if parts else ""


def _short_name(raw: str, limit: int = 26) -> str:
    if not raw:
        return "?"
    base = (_first_token(raw) or raw).strip().strip('"').strip("'")
    name = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if len(name) > limit:
        return name[: limit - 1] + "…"
    return name


def _unescape(pattern: str) -> str:
    """re.escape()-Rueckgaengig machen (Regeln speichern exe-Pfade escaped)."""
    try:
        return re.sub(r"\\(.)", r"\1", pattern or "")
    except re.error:
        return pattern or ""


class ProcessTable(Gtk.Box):
    """Editable, sortable table of processes + their throttling settings."""

    def __init__(self, gui, unit: str = "mBs", on_sort_change=None,
                 on_budget=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_vexpand(True)
        self.gui = gui
        self.unit = unit
        self._on_sort_change = on_sort_change
        self._on_budget = on_budget
        self._global_limits = {}
        self._rows = {}          # pid -> RowWidgets
        self._procs = {}         # pid -> current blob (for sorting)
        self._last_state = None  # letzter Zustand (fuer Filter-Redraw)
        self._filter = ""        # Suchtext (App-Name/exe, case-insensitiv)
        self._rules = []
        self._syncing = False
        self._empty = None
        self._no_match = None
        self._sort_key = "download"
        self._sort_desc = True
        self._sort_labels = {}
        # Einmal ein Limit-Feld fokussiert, pausieren wir Sortierung/Entfernen
        # fuer ein paar Sekunden. Sonst kann das Umsortieren den Fokus klauen,
        # bevor der Nutzer ueberhaupt tippen kann.
        self._edit_latch_until = 0.0

        # --- header (clickable = sort) ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("table-header-row")
        for index, (key, title, width) in enumerate(_COLUMNS):
            expand = index == 1          # nur "Process" waechst mit
            if key is None:
                label = Gtk.Label(label=title, xalign=0.0)
                label.add_css_class("table-header")
                header.append(self._cell(label, width, expand))
            else:
                button = Gtk.Button(label=title)
                button.add_css_class("table-header")
                button.add_css_class("flat")
                button.set_halign(Gtk.Align.FILL)
                button.connect("clicked", self._on_sort_clicked, key)
                self._sort_labels[key] = (button, title)
                header.append(self._cell(button, width, expand))
        self._header = header

        # --- rows ---
        # Header und Zeilen liegen im SELBEN Container: nur so sind die
        # Spalten exakt deckungsgleich (sonst verschiebt die Breite der
        # vertikalen Scrollbar die Zeilen gegenueber dem Header).
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._list.set_valign(Gtk.Align.START)
        self._list.append(header)
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("table-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._list)
        self.append(scroll)
        self._update_sort_labels()

    @staticmethod
    def _cell(child, width: int, expand: bool = False) -> Gtk.Box:
        """Zelle mit fester Breite; nur die Process-Spalte darf wachsen.

        Wichtig: Header und Zeilen benutzen dieselben Breiten und dieselbe
        Expand-Regel — sonst driften die Spalten auseinander (Header-Buttons
        haben andere Naturgroessen als Labels/Eingabefelder).
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_size_request(width, -1)
        child.set_hexpand(expand)
        box.append(child)
        return box

    # --- Sorting ----------------------------------------------------------

    def set_sort(self, key: str, desc: bool) -> None:
        self._sort_key = key
        self._sort_desc = desc
        self._update_sort_labels()
        self._apply_sort()

    def _on_sort_clicked(self, _button, key: str) -> None:
        if key == self._sort_key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = _DEFAULT_DESC.get(key, False)
        self._update_sort_labels()
        self._apply_sort()
        if self._on_sort_change is not None:
            self._on_sort_change(self._sort_key, self._sort_desc)

    def _update_sort_labels(self) -> None:
        for key, (button, title) in self._sort_labels.items():
            if key == self._sort_key:
                arrow = "▼" if self._sort_desc else "▲"
                button.set_label(f"{title}  {arrow}")
            else:
                button.set_label(title)

    def _sort_value(self, pid: str):
        blob = self._procs.get(pid, {})
        key = self._sort_key
        if key == "download":
            return float(blob.get("download", 0.0) or 0.0)
        if key == "upload":
            return float(blob.get("upload", 0.0) or 0.0)
        if key == "name":
            return _short_name(blob.get("name", "")).lower()
        if key == "pid":
            return int(pid) if str(pid).isdigit() else 0
        if key == "priority":
            rule = self._rule_for(blob)
            return _PRIORITY_RANK.get(rule.get("priority", "normal"), 2)
        return 0

    def _apply_sort(self) -> None:
        """Zeilen IM PLATZ umsortieren (keine Widgets neu bauen)."""
        pids = sorted(self._procs.keys(), key=self._sort_value,
                      reverse=self._sort_desc)
        previous = self._header           # Header bleibt ganz oben
        for pid in pids:
            roww = self._rows.get(pid)
            if roww is None:
                continue
            self._list.reorder_child_after(roww.box, previous)
            previous = roww.box

    def visible_order(self) -> list:
        """Aktuelle Anzeige-Reihenfolge (Tests/Hilfe)."""
        order = []
        child = self._list.get_first_child()
        while child is not None:
            for pid, roww in self._rows.items():
                if roww.box is child:
                    order.append(pid)
                    break
            child = child.get_next_sibling()
        return order

    # --- Unit -------------------------------------------------------------

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        for pid, roww in list(self._rows.items()):
            blob = self._procs.get(pid)
            if not blob:
                continue
            roww.down.set_text(format_rate(blob.get("download", 0.0), unit, 2))
            roww.up.set_text(format_rate(blob.get("upload", 0.0), unit, 2))
            rule = self._rule_for(blob)
            self._sync_limit_entry(roww.dl, rule.get("download_limit"), unit)
            self._sync_limit_entry(roww.ul, rule.get("upload_limit"), unit)
            roww.dl.set_unit_hint(unit)
            roww.ul.set_unit_hint(unit)
            self._sync_why(roww, blob)

    def _sync_limit_entry(self, entry, kbit, unit) -> None:
        """Feldtext an Einheit/Regel anpassen — nie waehrend des Tippens."""
        if entry.has_focus() or time.monotonic() < self._edit_latch_until:
            return
        self._syncing = True
        try:
            entry.set_text(format_rate_for_entry(kbit, unit))
        finally:
            self._syncing = False

    # --- Filter -----------------------------------------------------------

    def set_filter(self, text: str) -> None:
        """Nur Apps anzeigen, deren Name/exe den Text enthaelt (case-insensitiv)."""
        text = (text or "").strip().lower()
        if text == self._filter:
            return
        self._filter = text
        if self._last_state is not None:
            self.set_state(self._last_state)

    def _matches(self, blob: dict) -> bool:
        if not self._filter:
            return True
        haystack = f"{blob.get('name', '')} {blob.get('exe', '')}".lower()
        return self._filter in haystack

    # --- State application ------------------------------------------------

    def set_state(self, state: dict) -> None:
        """Called on every poll; updates live values in place, then re-sorts.

        Bevorzugt ``apps`` (pro Anwendung gruppiert, Summe aller PIDs) — sonst
        sieht man z. B. acht Zeilen "python3" statt einmal "legendary".
        """
        self._last_state = state
        self._rules = state.get("rules", [])
        grouped = "apps" in state
        items = (state.get("apps") if grouped else state.get("processes")) or []
        self._procs = {}
        for blob in items:
            key = str(blob.get("name") if grouped else blob.get("pid"))
            self._procs[key] = blob

        # Waehrend der Nutzer in einem Limit-Feld tippt, NICHT umsortieren und
        # keine Zeilen entfernen: das Reordering nimmt dem Feld sonst den Fokus
        # und die Eingabe geht verloren.
        editing = self._editing()
        if not self._procs:
            if editing:
                return
            self._clear_rows()
            self._show_empty()
            return
        visible = {key: blob for key, blob in self._procs.items()
                   if self._matches(blob)}
        if not visible:
            if editing:
                return
            self._clear_rows()
            self._show_no_match()
            return
        self._hide_empty()

        for key, blob in visible.items():
            if key in self._rows:
                self._update_row(self._rows[key], blob)
            else:
                roww = self._build_row(key, blob)
                self._sync_why(roww, blob)
                self._rows[key] = roww
                self._list.append(roww.box)
                self._update_row(roww, blob)

        for key in list(self._rows):
            if key not in visible:
                roww = self._rows[key]
                if editing and (roww.dl.has_focus() or roww.ul.has_focus()):
                    continue  # Zeile behalten, in der gerade getippt wird
                del self._rows[key]
                if roww.box.get_parent() is not None:
                    self._list.remove(roww.box)

        if not editing:
            self._apply_sort()

    def _editing(self) -> bool:
        """True, wenn der Nutzer gerade in einem Limit-Feld tippt (oder eben)."""
        if time.monotonic() < self._edit_latch_until:
            return True
        for roww in self._rows.values():
            if roww.dl.has_focus() or roww.ul.has_focus():
                return True
        return False

    def _on_entry_focus(self, entry, _pspec=None) -> None:
        if entry.has_focus():
            self._edit_latch_until = time.monotonic() + 6.0

    # --- Empty state ------------------------------------------------------

    def _show_empty(self) -> None:
        if self._empty is None:
            self._empty = self._placeholder(
                "network-offline-symbolic", "No active traffic yet",
                "Applications appear here as soon as they use the network.")
        if self._empty.get_parent() is None:
            self._list.append(self._empty)

    def _show_no_match(self) -> None:
        if self._no_match is None:
            self._no_match = self._placeholder(
                "system-search-symbolic", "No matching applications",
                "Clear the filter to see all traffic again.")
        if self._no_match.get_parent() is None:
            self._list.append(self._no_match)

    @staticmethod
    def _placeholder(icon_name: str, title_text: str, hint_text: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      halign=Gtk.Align.CENTER)
        box.set_margin_top(32)
        box.set_margin_bottom(24)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        title = Gtk.Label(label=title_text)
        title.add_css_class("empty-title")
        hint = Gtk.Label(label=hint_text, justify=Gtk.Justification.CENTER,
                         wrap=True)
        hint.add_css_class("empty-hint")
        box.append(icon)
        box.append(title)
        box.append(hint)
        return box

    def _hide_empty(self) -> None:
        for box in (self._empty, self._no_match):
            if box is not None and box.get_parent() is not None:
                self._list.remove(box)

    def _clear_rows(self) -> None:
        for pid in list(self._rows):
            roww = self._rows.pop(pid)
            if roww.box.get_parent() is not None:
                self._list.remove(roww.box)
        self._procs.clear()

    # --- Helpers ---------------------------------------------------------

    def _rule_for(self, blob: dict) -> dict:
        """Regel zur Anwendung finden (exe-Pfad bevorzugt, dann Name)."""
        raw = blob.get("name", "")
        exe = blob.get("exe") or _first_token(raw)
        base = _short_name(raw, 64)
        for rule in self._rules:
            mv = _unescape(rule.get("match_value", ""))
            if not mv:
                continue
            if rule.get("match_type") == "exe" and (mv == exe or exe.endswith(mv.rstrip("/"))):
                return rule
            if rule.get("match_type") == "name" and mv == base:
                return rule
            if rule.get("name") == base:
                return rule
        return {}

    def _update_row(self, roww, blob) -> None:
        # Raten immer aktualisieren; Limits/Prioritaet koennen sich ausserhalb der
        # Zeile geaendert haben (Profilwechsel, CLI, globales Limit), also
        # ebenfalls abgleichen — aber nie in ein Feld schreiben, in dem gerade
        # getippt oder ausgewaehlt wird (_sync_limit_entry prueft das selbst).
        rule = {} if blob.get("unattributed") else self._rule_for(blob)
        roww.down.set_text(format_rate(blob.get("download", 0.0), self.unit, 2))
        roww.up.set_text(format_rate(blob.get("upload", 0.0), self.unit, 2))
        if not blob.get("unattributed"):
            self._sync_limit_entry(roww.dl, rule.get("download_limit"), self.unit)
            self._sync_limit_entry(roww.ul, rule.get("upload_limit"), self.unit)
            if not roww.prio.has_focus():
                name = rule.get("priority", "normal") or "normal"
                if roww.prio.get_priority_name() != name:
                    self._syncing = True
                    try:
                        roww.prio.set_priority_name(name)
                    finally:
                        self._syncing = False
        self._sync_why(roww, blob)

    def _sync_why(self, roww, blob) -> None:
        """Erklaerzeile aktualisieren (Regel, Fenster, globales Limit)."""
        rule = {} if blob.get("unattributed") else self._rule_for(blob)
        text = explain(rule, self._global_limits, self.unit) or ""
        if roww.why.get_text() != text:
            roww.why.set_text(text)
        roww.why.set_visible(bool(text))

    def _build_row(self, key: str, blob) -> "RowWidgets":
        # ``key`` ist der stabile Zeilen-Schluessel (App-Name bei gruppierten
        # Zeilen, sonst PID) und wird an die Callbacks gebunden. Die PID-Spalte
        # zeigt davon unabhaengig die echte(n) PID(s).
        pids = [str(p) for p in (blob.get("pids") or [])]
        count = int(blob.get("pid_count") or len(pids) or 1)
        real_pid = blob.get("pid")
        if count > 1:
            pid_text = f"{count} processes"
        elif pids and pids[0] not in ("", "-"):
            pid_text = pids[0]
        elif real_pid not in (None, "", "-"):
            pid_text = str(real_pid)
        else:
            pid_text = "—"
        unattributed = bool(blob.get("unattributed"))
        rule = {} if unattributed else self._rule_for(blob)

        # Zwei Zeilen pro App: oben die Werte, darunter (optional) ein Satz, der
        # erklaert, welche Regel gilt. Damit beantwortet die Tabelle die Frage
        # "warum ist das gedrosselt?", die sonst nur das README beantwortet.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("row")
        if unattributed:
            outer.add_css_class("unattributed")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        outer.append(box)

        pid_l = Gtk.Label(label=pid_text, xalign=0.0)
        pid_l.add_css_class("dim-label")
        box.append(self._cell(pid_l, _COLUMNS[0][2]))

        # Der synthetische Name ist kein Kommandopfad — _short_name wuerde ihn
        # am ersten Leerzeichen zerschneiden.
        app_name = (UNATTRIBUTED_NAME if unattributed
                    else _short_name(blob.get("name", "?"), 40))
        name_l = Gtk.Label(label=app_name, xalign=0.0)
        name_l.set_ellipsize(Pango.EllipsizeMode.END)
        name_l.set_hexpand(True)
        if unattributed:
            name_l.set_tooltip_text(
                "Traffic measured on the interface that could not be matched "
                "to a running process. It cannot be limited.")
        else:
            tip = blob.get("exe") or blob.get("name", "")
            if count > 1:
                tip = (f"{tip}\n({count} processes: "
                       f"{', '.join(blob.get('pids', [])[:8])})")
            name_l.set_tooltip_text((tip or "")[:500])

        # Zeitfenster-Button: klein, mit dem App-Namen in einer Zelle.
        window = rule.get("window")
        sched = Gtk.Button()
        sched.add_css_class("flat")
        sched.add_css_class("row-window")
        sched.set_icon_name("alarm-symbolic")
        if window:
            sched.add_css_class("armed")
            sched.set_tooltip_text(
                f"Time window: {format_window(window)}\nClick to edit")
        else:
            sched.set_tooltip_text("No time window (always active) — click to add")
        # Der Zustand darf nicht allein an der Farbe haengen: der „armed"-Fall
        # setzt zusaetzlich eine Hintergrundflaeche (siehe style.css) und hier
        # einen Namen fuer Screenreader.
        sched.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Time window for {app_name}: {format_window(window)}"
             if window else f"No time window for {app_name}"])
        sched.connect("clicked", self._on_edit_window, key)
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        name_box.set_size_request(_COLUMNS[1][2], -1)
        name_box.append(name_l)
        name_box.append(sched)
        box.append(name_box)

        down = Gtk.Label(label=format_rate(blob.get("download", 0.0), self.unit, 2),
                         xalign=0.0)
        down.add_css_class("rate-down")
        box.append(self._cell(down, _COLUMNS[2][2]))

        up = Gtk.Label(label=format_rate(blob.get("upload", 0.0), self.unit, 2),
                       xalign=0.0)
        up.add_css_class("rate-up")
        box.append(self._cell(up, _COLUMNS[3][2]))

        dl = RateEntry(self.unit)
        dl.set_text(format_rate_for_entry(rule.get("download_limit"), self.unit))
        dl.set_tooltip_text(
            f"Limit in {UNIT_LABELS.get(self.unit, self.unit)} — empty = unlimited")
        dl.connect("changed", self._on_limit, key, "download_limit")
        dl.connect("notify::has-focus", self._on_entry_focus)
        box.append(self._cell(dl, _COLUMNS[4][2]))

        ul = RateEntry(self.unit)
        ul.set_text(format_rate_for_entry(rule.get("upload_limit"), self.unit))
        ul.connect("changed", self._on_limit, key, "upload_limit")
        ul.connect("notify::has-focus", self._on_entry_focus)
        box.append(self._cell(ul, _COLUMNS[5][2]))

        prio = PriorityDropdown()
        prio.set_priority_name(rule.get("priority", "normal") or "normal")
        prio.connect("notify::selected", self._on_priority, key)
        box.append(self._cell(prio, _COLUMNS[6][2]))

        menu_button = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.add_css_class("row-menu")
        menu_button.set_menu_model(self._row_menu())
        menu_button.set_tooltip_text(f"More actions for {app_name}")
        box.append(menu_button)
        outer.insert_action_group("row", self._row_actions(key, unattributed))

        why = Gtk.Label(label="", xalign=0.0)
        why.add_css_class("row-why")
        why.set_margin_start(_COLUMNS[0][2] + 6)
        why.set_ellipsize(Pango.EllipsizeMode.END)
        outer.append(why)

        if unattributed:
            # Kein Prozess -> keine Regel moeglich. Felder nur anzeigen.
            for widget in (dl, ul, prio, sched):
                widget.set_sensitive(False)
            dl.set_tooltip_text("Traffic that nethogs could not map to a process")
            sched.set_tooltip_text("Time windows need an application rule")
            name_l.set_tooltip_text("Traffic nethogs could not attribute "
                                    "(VPN, UDP, other users, short-lived sockets)")

        return RowWidgets(box=outer, line=box, why=why, pid=key, down=down,
                          up=up, dl=dl, ul=ul, prio=prio)

    def _row_menu(self) -> Gio.Menu:
        """Was man mit einer Zeile tun kann, ohne sie zuerst zu treffen."""
        menu = Gio.Menu()
        menu.append("Set a budget…", "row.set-budget")
        menu.append("Edit time window…", "row.set-window")
        return menu

    def _row_actions(self, key: str, unattributed: bool) -> Gio.SimpleActionGroup:
        group = Gio.SimpleActionGroup()
        budget = Gio.SimpleAction.new("set-budget", None)
        budget.connect("activate", lambda *_a: self._request_budget(key))
        # Ohne Prozess gibt es keine App, an der ein Budget haengen koennte.
        budget.set_enabled(not unattributed)
        group.add_action(budget)
        window = Gio.SimpleAction.new("set-window", None)
        window.connect("activate", lambda *_a: self._on_edit_window(None, key))
        window.set_enabled(not unattributed)
        group.add_action(window)
        return group

    def _request_budget(self, key: str) -> None:
        if self._on_budget is not None:
            self._on_budget(key)

    def set_global_limits(self, limits: dict) -> None:
        """Konfigurierte globale Limits, damit die Erklaerzeile sie nennen kann."""
        self._global_limits = limits or {}

    # --- Callbacks --------------------------------------------------------

    def _on_limit(self, entry, row_key, field):
        if self._syncing:
            return
        timer_attr = f"_lim_{row_key}_{field}"
        old = getattr(self, timer_attr, None)
        if old is not None:
            GLib.source_remove(old)

        def _send():
            setattr(self, timer_attr, None)
            if self._syncing:
                return False
            text = entry.get_text()
            try:
                rate = parse_rate_in_unit(text, self.unit)
            except ValueError as error:
                self.gui.show_error(f"Invalid limit: {error}")
                return False
            self._set_rule_field(row_key, field, rate)
            return False

        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_priority(self, dd, _pspec, row_key):
        if self._syncing:
            return
        self._set_rule_field(row_key, "priority", dd.get_priority_name())

    def _set_rule_field(self, row_key: str, field: str, value) -> None:
        blob = self._procs.get(row_key)  # Key: App-Name (gruppiert) oder PID
        if blob is None:
            # Prozess ist inzwischen weg — Eingabe verwerfen (kein Fehler-Popup).
            return
        rule = self._base_rule(blob)
        rule[field] = value
        self._send_rule(rule)

    def _set_rule_window(self, row_key: str, window) -> None:
        blob = self._procs.get(row_key)
        if blob is None:
            return
        rule = self._base_rule(blob)
        rule["window"] = window
        self._send_rule(rule)

    def _base_rule(self, blob: dict) -> dict:
        """Gespeicherte Regel oder neue Regel aus dem Live-Prozess bauen."""
        rule = dict(self._rule_for(blob))
        if not rule.get("key"):
            match_type = _match_type_for(blob)
            rule["name"] = _short_name(blob.get("name", "Process"), 24)
            rule["match_type"] = match_type
            rule["match_value"] = _match_value_for(blob, match_type)
            rule["download_limit"] = None
            rule["upload_limit"] = None
            rule["priority"] = "normal"
            rule["recursive"] = False
        return rule

    def _send_rule(self, rule: dict) -> None:
        client = self.gui.client
        name = rule.get("name")
        call_async = getattr(client, "call_async", None)
        if call_async is not None:
            # Nicht blockierend: der Engine-Neustart darf die UI nicht einfrieren.
            call_async(
                "set_process", rule,
                on_done=lambda _r: self.gui.show_info(f"Rule saved ({name})"),
                on_error=self.gui.show_error,
            )
        else:  # pragma: no cover - Test-Stub ohne Worker
            try:
                client.call("set_process", rule)
                self.gui.show_info(f"Rule saved ({name})")
            except Exception as error:
                self.gui.show_error(str(error))

    def _on_edit_window(self, _button, row_key: str) -> None:
        blob = self._procs.get(row_key)
        if blob is None:
            return
        rule = self._rule_for(blob)
        dialog = RuleWindowDialog(self, rule.get("window"), row_key,
                                  self._on_window_saved)
        dialog.present()

    def _on_window_saved(self, row_key: str, window) -> None:
        self._set_rule_window(row_key, window)

    # --- Accessors (tests) -------------------------------------------------

    def row_count(self) -> int:
        return len(self._rows)


class RowWidgets:
    """Per-row widgets, updated in place (never rebuilt on poll)."""

    def __init__(self, box, line, why, pid, down, up, dl, ul, prio):
        self.box = box        # outer: hover area, two lines
        self.line = line      # the value line (cells)
        self.why = why        # explanation line (may be empty)
        self.pid = pid
        self.down = down
        self.up = up
        self.dl = dl
        self.ul = ul
        self.prio = prio


class RuleWindowDialog:
    """Kleiner Dialog zum Setzen/Entfernen eines Zeitfensters einer Regel."""

    _DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def __init__(self, parent, window, row_key, on_save):
        self._row_key = row_key
        self._on_save = on_save
        self._root = parent.get_root() if parent is not None else None
        self.dialog = Adw.AlertDialog(
            heading="Time window",
            body=("The rule applies only inside this window. Pick days and "
                  "times, then Save. 'Clear' removes the window."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        days_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        days_box.set_halign(Gtk.Align.CENTER)
        selected = set(window.get("days", range(7))) if window else set(range(7))
        self._day_buttons = {}
        for index, label in enumerate(self._DAYS):
            button = Gtk.ToggleButton(label=label)
            button.set_active(index in selected)
            button.connect("toggled", self._update_sensitivity)
            self._day_buttons[index] = button
            days_box.append(button)
        box.append(days_box)

        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        time_box.set_halign(Gtk.Align.CENTER)
        self._start = Gtk.Entry()
        self._start.set_placeholder_text("20:00")
        self._start.set_width_chars(6)
        self._start.set_text(window.get("start", "") if window else "")
        self._start.connect("changed", self._update_sensitivity)
        self._end = Gtk.Entry()
        self._end.set_placeholder_text("00:00")
        self._end.set_width_chars(6)
        self._end.set_text(window.get("end", "") if window else "")
        self._end.connect("changed", self._update_sensitivity)
        time_box.append(Gtk.Label(label="From"))
        time_box.append(self._start)
        time_box.append(Gtk.Label(label="To"))
        time_box.append(self._end)
        box.append(time_box)
        self.dialog.set_extra_child(box)

        self.dialog.add_response("cancel", "Cancel")
        self.dialog.add_response("clear", "Clear")
        self.dialog.add_response("save", "Save")
        self.dialog.set_response_appearance("save",
                                            Adw.ResponseAppearance.SUGGESTED)
        self.dialog.set_response_appearance("clear",
                                            Adw.ResponseAppearance.DESTRUCTIVE)
        self.dialog.set_default_response("save")
        self.dialog.set_close_response("cancel")
        self.dialog.connect("response", self._on_response)
        self._update_sensitivity()

    def present(self):
        self.dialog.present(self._root)

    def _selected_days(self):
        return [index for index, button in self._day_buttons.items()
                if button.get_active()]

    def _current_window(self):
        return normalize_window({
            "days": self._selected_days(),
            "start": self._start.get_text(),
            "end": self._end.get_text(),
        })

    def _update_sensitivity(self, *_args):
        self.dialog.set_response_enabled(
            "save", self._current_window() is not None)

    def _on_response(self, _dialog, response):
        if response == "clear":
            self._on_save(self._row_key, None)
        elif response == "save":
            window = self._current_window()
            if window is not None:
                self._on_save(self._row_key, window)


def _match_type_for(blob: dict) -> str:
    exe = blob.get("exe") or _first_token(blob.get("name", ""))
    return "exe" if exe.startswith("/") else "name"


def _match_value_for(blob: dict, match_type: str) -> str:
    raw = blob.get("name", "")
    if match_type == "exe":
        return blob.get("exe") or _first_token(raw) or raw
    return _short_name(raw, 64) or raw
