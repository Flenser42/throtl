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

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Gtk, Pango

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

    def __init__(self, gui, unit: str = "mBs"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_vexpand(True)
        self.gui = gui
        self.unit = unit
        self._rows = {}          # pid -> RowWidgets
        self._procs = {}         # pid -> current blob (for sorting)
        self._rules = []
        self._syncing = False
        self._empty = None
        self._sort_key = "download"
        self._sort_desc = True
        self._sort_labels = {}

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

    def _sync_limit_entry(self, entry, kbit, unit) -> None:
        """Feldtext an Einheit/Regel anpassen — nie waehrend des Tippens."""
        if entry.has_focus():
            return
        self._syncing = True
        try:
            entry.set_text(format_rate_for_entry(kbit, unit))
        finally:
            self._syncing = False

    # --- State application ------------------------------------------------

    def set_state(self, state: dict) -> None:
        """Called on every poll; updates live values in place, then re-sorts.

        Bevorzugt ``apps`` (pro Anwendung gruppiert, Summe aller PIDs) — sonst
        sieht man z. B. acht Zeilen "python3" statt einmal "legendary".
        """
        self._rules = state.get("rules", [])
        grouped = "apps" in state
        items = (state.get("apps") if grouped else state.get("processes")) or []
        self._procs = {}
        for blob in items:
            key = str(blob.get("name") if grouped else blob.get("pid"))
            self._procs[key] = blob

        if not items:
            self._clear_rows()
            self._show_empty()
            return
        self._hide_empty()

        for key, blob in self._procs.items():
            if key in self._rows:
                self._update_row(self._rows[key], blob)
            else:
                roww = self._build_row(key, blob)
                self._rows[key] = roww
                self._list.append(roww.box)

        for key in list(self._rows):
            if key not in self._procs:
                roww = self._rows.pop(key)
                if roww.box.get_parent() is not None:
                    self._list.remove(roww.box)

        self._apply_sort()

    # Backwards compatible alias
    def refresh(self, state: dict) -> None:
        self.set_state(state)

    # --- Empty state ------------------------------------------------------

    def _show_empty(self) -> None:
        if self._empty is None:
            self._empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                  halign=Gtk.Align.CENTER)
            self._empty.set_margin_top(32)
            self._empty.set_margin_bottom(24)
            icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
            icon.set_pixel_size(48)
            icon.add_css_class("dim-label")
            title = Gtk.Label(label="No active traffic yet")
            title.add_css_class("empty-title")
            hint = Gtk.Label(
                label="Applications appear here as soon as they use the network.",
                justify=Gtk.Justification.CENTER, wrap=True)
            hint.add_css_class("empty-hint")
            self._empty.append(icon)
            self._empty.append(title)
            self._empty.append(hint)
        if self._empty.get_parent() is None:
            self._list.append(self._empty)

    def _hide_empty(self) -> None:
        if self._empty is not None and self._empty.get_parent() is not None:
            self._list.remove(self._empty)

    def _clear_rows(self) -> None:
        for pid in list(self._rows):
            roww = self._rows.pop(pid)
            if roww.box.get_parent() is not None:
                self._list.remove(roww.box)
        self._procs.clear()

    # --- Helpers ---------------------------------------------------------

    def gui_client_state(self) -> dict:
        client = getattr(self.gui, "gui", None)
        return getattr(client, "state", {"processes": [], "rules": []})

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
        # Nur Raten — fokussierte/editierte Widgets bleiben unberuehrt.
        roww.down.set_text(format_rate(blob.get("download", 0.0), self.unit, 2))
        roww.up.set_text(format_rate(blob.get("upload", 0.0), self.unit, 2))

    def _build_row(self, key: str, blob) -> "RowWidgets":
        # ``key`` ist der stabile Zeilen-Schluessel (App-Name bei gruppierten
        # Zeilen, sonst PID) und wird an die Callbacks gebunden. Die PID-Spalte
        # zeigt davon unabhaengig die echte(n) PID(s).
        pids = [str(p) for p in (blob.get("pids") or [])]
        count = int(blob.get("pid_count") or len(pids) or 1)
        real_pid = blob.get("pid")
        if count > 1:
            pid_text = f"{count} pids"
        elif pids and pids[0] not in ("", "-"):
            pid_text = pids[0]
        elif real_pid not in (None, "", "-"):
            pid_text = str(real_pid)
        else:
            pid_text = "—"
        unattributed = bool(blob.get("unattributed"))
        rule = {} if unattributed else self._rule_for(blob)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("row")
        if unattributed:
            box.add_css_class("unattributed")

        pid_l = Gtk.Label(label=pid_text, xalign=0.0)
        pid_l.add_css_class("dim-label")
        box.append(self._cell(pid_l, _COLUMNS[0][2]))

        app_name = _short_name(blob.get("name", "?"), 40)
        name_l = Gtk.Label(label=app_name, xalign=0.0)
        name_l.set_ellipsize(Pango.EllipsizeMode.END)
        tip = blob.get("exe") or blob.get("name", "")
        if count > 1:
            tip = f"{tip}\n({count} processes: {', '.join(blob.get('pids', [])[:8])})"
        name_l.set_tooltip_text((tip or "")[:500])
        box.append(self._cell(name_l, _COLUMNS[1][2], True))

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
        box.append(self._cell(dl, _COLUMNS[4][2]))

        ul = RateEntry(self.unit)
        ul.set_text(format_rate_for_entry(rule.get("upload_limit"), self.unit))
        ul.connect("changed", self._on_limit, key, "upload_limit")
        box.append(self._cell(ul, _COLUMNS[5][2]))

        prio = PriorityDropdown()
        prio.set_priority_name(rule.get("priority", "normal") or "normal")
        prio.connect("notify::selected", self._on_priority, key)
        box.append(self._cell(prio, _COLUMNS[6][2]))

        if unattributed:
            # Kein Prozess -> keine Regel moeglich. Felder nur anzeigen.
            for widget in (dl, ul, prio):
                widget.set_sensitive(False)
            dl.set_tooltip_text("Traffic that nethogs could not map to a process")
            name_l.set_tooltip_text("Traffic nethogs could not attribute "
                                    "(VPN, UDP, other users, short-lived sockets)")

        return RowWidgets(box=box, pid=key, down=down, up=up, dl=dl, ul=ul, prio=prio)

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
        rule = dict(self._rule_for(blob))
        match_type = _match_type_for(blob)
        if not rule.get("key"):
            rule["name"] = _short_name(blob.get("name", "Process"), 24)
            rule["match_type"] = match_type
            rule["match_value"] = _match_value_for(blob, match_type)
            rule["download_limit"] = None
            rule["upload_limit"] = None
            rule["priority"] = "normal"
            rule["recursive"] = False
        rule[field] = value
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

    # --- Accessors (tests) -------------------------------------------------

    def row_count(self) -> int:
        return len(self._rows)


class RowWidgets:
    """Per-row widgets, updated in place (never rebuilt on poll)."""

    def __init__(self, box, pid, down, up, dl, ul, prio):
        self.box = box
        self.pid = pid
        self.down = down
        self.up = up
        self.dl = dl
        self.ul = ul
        self.prio = prio


def _match_type_for(blob: dict) -> str:
    exe = blob.get("exe") or _first_token(blob.get("name", ""))
    return "exe" if exe.startswith("/") else "name"


def _match_value_for(blob: dict, match_type: str) -> str:
    raw = blob.get("name", "")
    if match_type == "exe":
        return blob.get("exe") or _first_token(raw) or raw
    return _short_name(raw, 64) or raw
