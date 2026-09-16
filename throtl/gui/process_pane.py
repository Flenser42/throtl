"""Network traffic table: one editable row per active process (NetLimiter-style).

Each row shows a process's live download/upload rates and lets the user set a
per-process download/upload limit + priority. Setting a limit creates/updates a
TrafficToll rule (matched by exe/name) via the daemon.

The table updates IN PLACE (rates only) on each poll so the user can keep
typing in the limit/priority fields without losing focus — the GUI is never
rebuilt wholesale on live updates.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GLib

from ..units import format_rate, parse_rate_lenient
from .widgets import RateEntry, PriorityDropdown


def _first_token(raw: str) -> str:
    """nethogs liefert als 'name' die ganze Kommandozeile; das erste Token ist
    das eigentliche Binary (z.B. '/usr/lib/electron43/electron')."""
    parts = (raw or "").split()
    return parts[0] if parts else ""


def _short_name(raw: str, limit: int = 24) -> str:
    if not raw:
        return "?"
    base = _first_token(raw) or raw
    name = base.rsplit("/", 1)[-1]
    if len(name) > limit:
        return name[: limit - 1] + "…"
    return name


class ProcessTable(Gtk.ScrolledWindow):
    """Scrollable, editable table of processes + their throttling settings.

    - `rows`: dict pid -> RowWidgets (built once, updated in place)
    - `set_state(state)`: refresh live rates + rule mapping without recreating
      rows, so focus/typing in the limit fields survives poll updates.
    """

    def __init__(self, gui, unit: str = "mBs"):
        super().__init__(vexpand=True)
        self.gui = gui
        self.unit = unit
        self._rows = {}      # pid -> RowWidgets
        self._order = []     # pids in display order
        self._rules = []     # last known rules

        columns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for title in ("PID", "Process", "▼ Download", "▲ Upload",
                      "DL limit", "UL limit", "Priority"):
            label = Gtk.Label(label=title, hexpand=True, xalign=0.0)
            label.add_css_class("table-header")
            header.append(label)
        columns_box.append(header)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._list)
        columns_box.append(scroll)
        self.set_child(columns_box)

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        # Re-format rates in place (limit fields untouched so typing survives)
        for pid, roww in list(self._rows.items()):
            blob = self._blob_for_pid(self.gui_client_state(), pid)
            if blob:
                roww.down.set_text(format_rate(blob.get("download", 0.0), unit, 2))
                roww.up.set_text(format_rate(blob.get("upload", 0.0), unit, 2))

    # --- State application ----------------------------------------------

    def set_state(self, state: dict) -> None:
        """Call this on every poll; updates live values in place."""
        self._rules = state.get("rules", [])
        processes = state.get("processes", [])
        # reconcile: add new, update existing, remove gone
        for blob in processes:
            pid = str(blob.get("pid"))
            if pid in self._rows:
                self._update_row(self._rows[pid], blob)
            else:
                roww = self._build_row(blob)
                self._rows[pid] = roww
                self._list.append(roww.box)
                self._order.append(pid)
        # remove rows that disappeared
        alive = {str(b.get("pid")) for b in processes}
        for pid in list(self._order):
            if pid not in alive:
                box = self._rows.pop(pid)
                if box.box.get_parent() is not None:
                    self._list.remove(box.box)
                self._order.remove(pid)
        if not processes:
            while (child := self._list.get_first_child()) is not None:
                self._list.remove(child)
            self._rows.clear()
            self._order.clear()
            empty = Gtk.Label(label="No processes with active traffic yet.", xalign=0)
            empty.add_css_class("dim-label")
            self._list.append(empty)

    # keep old name for app.py backward-compat
    def refresh(self, state: dict) -> None:
        self.set_state(state)

    # --- Helpers ---------------------------------------------------------

    def gui_client_state(self) -> dict:
        client = getattr(self.gui, "gui", None)
        return getattr(client, "state", {"processes": [], "rules": []})

    def _blob_for_pid(self, state, pid: str) -> dict | None:
        for b in state.get("processes", []):
            if str(b.get("pid")) == pid:
                return b
        return None

    def _rule_for(self, blob: dict) -> dict:
        name = blob.get("name", "")
        for rule in self._rules:
            if rule.get("name") and rule["name"].lower() in name.lower():
                return rule
            mv = rule.get("match_value")
            if mv and (mv in name or name.endswith(mv.rstrip("/"))):
                return rule
        return {}

    def _update_row(self, roww, blob) -> None:
        # Rates only — never touch focus/editable widgets here.
        roww.down.set_text(format_rate(blob.get("download", 0.0), self.unit, 2))
        roww.up.set_text(format_rate(blob.get("upload", 0.0), self.unit, 2))

    def _build_row(self, blob) -> "RowWidgets":
        rule = self._rule_for(blob)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("row")

        pid_l = Gtk.Label(label=str(blob.get("pid", "?")), hexpand=True, xalign=0.0)
        pid_l.add_css_class("dim-label")
        box.append(pid_l)

        name_l = Gtk.Label(label=_short_name(blob.get("name", "?")), hexpand=True,
                           xalign=0.0)
        box.append(name_l)

        down = Gtk.Label(label=format_rate(blob.get("download", 0.0), self.unit, 2),
                         hexpand=True, xalign=0.0)
        box.append(down)

        up = Gtk.Label(label=format_rate(blob.get("upload", 0.0), self.unit, 2),
                       hexpand=True, xalign=0.0)
        box.append(up)

        dl = RateEntry(self.unit)
        dl.set_text("" if rule.get("download_limit") is None else str(rule.get("download_limit")))
        dl.connect("changed", self._on_limit, str(blob.get("pid")), "download_limit", dl)
        box.append(dl)

        ul = RateEntry(self.unit)
        ul.set_text("" if rule.get("upload_limit") is None else str(rule.get("upload_limit")))
        ul.connect("changed", self._on_limit, str(blob.get("pid")), "upload_limit", ul)
        box.append(ul)

        prio = PriorityDropdown()
        prio.set_priority_name(rule.get("priority", "normal") or "normal")
        prio.connect("notify::selected", self._on_priority, str(blob.get("pid")), prio)
        box.append(prio)

        return RowWidgets(box=box, pid=str(blob.get("pid")), down=down, up=up,
                          dl=dl, ul=ul, prio=prio)

    # --- Callbacks -------------------------------------------------------

    def _on_limit(self, entry, pid, key, _entry_alias):
        text = entry.get_text()
        timer_attr = f"_lim_{pid}_{key}"
        old = getattr(self, timer_attr, None)
        if old is not None:
            GLib.source_remove(old)

        def _send():
            try:
                rate = None if not (text or "").strip() else parse_rate_lenient(text)
            except ValueError as error:
                self.gui.show_error(f"Invalid limit: {error}")
                setattr(self, timer_attr, None)
                return False
            self._set_rule_field(pid, key, rate)
            setattr(self, timer_attr, None)
            return False

        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_priority(self, _dd, _pspec, pid, dropdown):
        self._set_rule_field(pid, "priority", dropdown.get_priority_name())

    def _set_rule_field(self, pid: str, field: str, value) -> None:
        blob = self._blob_for_pid(self.gui_client_state(), pid)
        if blob is None:
            self.gui.show_error("Process is no longer active.")
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
        try:
            self.gui.client.call("set_process", rule)
            self.gui.show_info("Rule saved")
        except Exception as error:
            self.gui.show_error(str(error))

    # --- Public accessors for app tests ----------------------------------

    def row_count(self) -> int:
        return len(self._rows)


class RowWidgets:
    """Per-row widgets that get updated in place (never rebuilt on poll)."""

    def __init__(self, box, pid, down, up, dl, ul, prio):
        self.box = box
        self.pid = pid
        self.down = down
        self.up = up
        self.dl = dl
        self.ul = ul
        self.prio = prio


def _match_type_for(blob: dict) -> str:
    name = blob.get("name", "")
    if name.startswith("/"):
        return "exe"
    return "name"


def _match_value_for(blob: dict, match_type: str) -> str:
    raw = blob.get("name", "")
    if match_type == "exe":
        # Voller Pfad des Binaries (erstes Token der nethogs-Kommandozeile)
        return _first_token(raw) or raw
    return _short_name(raw, 64) or raw
