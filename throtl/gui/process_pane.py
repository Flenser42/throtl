"""Network traffic table: one editable row per active process (NetLimiter-style).

Each row shows a process's live download/upload rates and lets the user set a
per-process download/upload limit + priority. Setting a limit creates/updates a
ThrottToll rule (matched by exe/name) via the daemon.
"""

from gi.repository import Gtk, Gio, GObject, GLib

from ..units import format_rate, parse_rate_lenient
from .widgets import RateEntry, PriorityDropdown


def _short_name(raw: str, limit: int = 22) -> str:
    """Shorten a process exe/name for display (keep last path segment)."""
    if not raw:
        return "?"
    name = raw.rsplit("/", 1)[-1]
    if len(name) > limit:
        return name[: limit - 1] + "…"
    return name


class ProcessRow(GObject.Object):
    """A row model handled by ProcessTable."""

    __gtype_name__ = "ThrotlProcessRow"

    pid = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    down = GObject.Property(type=str, default="0")
    up = GObject.Property(type=str, default="0")
    dl_limit = GObject.Property(type=str, default="")
    ul_limit = GObject.Property(type=str, default="")
    priority = GObject.Property(type=int, default=0)  # index into PRIORITY_NAMES

    def __init__(self, blob, unit: str):
        super().__init__()
        self.pid = str(blob.get("pid", "?"))
        self.name = _short_name(blob.get("name", "؟"))
        self.down = format_rate(blob.get("download", 0.0), unit, 1)
        self.up = format_rate(blob.get("upload", 0.0), unit, 1)
        rule = blob.get("rule") or {}
        dl = rule.get("download_limit")
        ul = rule.get("upload_limit")
        self.dl_limit = "" if dl is None else str(dl)
        self.ul_limit = "" if ul is None else str(ul)
        prio = rule.get("priority", "normal")
        self.priority = _PRIORITY_INDEX(prio)


def _PRIORITY_INDEX(name: str) -> int:
    from .widgets import PRIORITY_NAMES

    return PRIORITY_NAMES.index(name) if name in PRIORITY_NAMES else 2


class ProcessTable(Gtk.ScrolledWindow):
    """Scrollable, editable table of processes + their throttling settings."""

    def __init__(self, gui, unit: str = "mBs"):
        super().__init__(vexpand=True)
        self.gui = gui
        self.unit = unit
        self._store = Gio.ListStore.new(ProcessRow)
        self._rows = {}

        columns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for title in ("PID", "Process", "▼ Download", "▲ Upload",
                      "DL limit", "UL limit", "Priority"):
            label = Gtk.Label(label=title, hexpand=True, xalign=0.0)
            label.add_css_class("table-header")
            header.append(label)
        columns_box.append(header)

        # Zeilen im vertikalen Stack (einfach & zuverlaessig)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._list)
        columns_box.append(scroll)

        self.set_child(columns_box)

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.refresh_rows()

    def refresh(self, state: dict) -> None:
        """Rebuild rows from a fresh daemon state snapshot."""
        self._apply_rules_to_processes(state)
        self.refresh_rows()

    def _apply_rules_to_processes(self, state: dict) -> None:
        rules = state.get("rules", [])
        for proc in state.get("processes", []):
            proc["rule"] = _find_rule_for(proc, rules)

    def refresh_rows(self) -> None:
        # Kill children
        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)

        processes = self.gui_client_state().get("processes", [])
        if not processes:
            empty = Gtk.Label(label="No processes with active traffic yet.", xalign=0)
            empty.add_css_class("dim-label")
            self._list.append(empty)
            return
        for blob in processes:
            self._list.append(self._build_row(ProcessRow(blob, self.unit)))

    def gui_client_state(self) -> dict:
        # self.gui ist die ThrotlWindow; deren .gui ist die GuiClient
        client = getattr(self.gui, "gui", None)
        return getattr(client, "state", {"processes": [], "rules": []})

    def _build_row(self, row: ProcessRow) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.add_css_class("row")

        pid_label = Gtk.Label(label=row.pid, hexpand=True, xalign=0.0)
        pid_label.add_css_class("dim-label")
        line.append(pid_label)

        name_label = Gtk.Label(label=row.name, hexpand=True, xalign=0.0)
        name_label.set_tooltip_text(_full_name_hint(row))
        line.append(name_label)

        line.append(Gtk.Label(label=row.down, hexpand=True, xalign=0.0))
        line.append(Gtk.Label(label=row.up, hexpand=True, xalign=0.0))

        # Editable limits — debounced on change (GTK4 has no focus-out-event)
        dl = RateEntry(self.unit)
        dl.set_text(row.dl_limit)
        dl.connect("changed", self._on_limit, row, "download_limit", dl)
        line.append(dl)

        ul = RateEntry(self.unit)
        ul.set_text(row.ul_limit)
        ul.connect("changed", self._on_limit, row, "upload_limit", ul)
        line.append(ul)

        prio = PriorityDropdown()
        prio.set_priority_name(_prio_name(row.priority))
        prio.connect("notify::selected", self._on_priority, row, prio)
        line.append(prio)

        return line

    # --- Callbacks -------------------------------------------------------

    def _on_limit(self, *_args):
        entry = _args[-1]
        key = _args[-2]
        row = _args[-3]
        text = entry.get_text()
        # Debounce so we don't hit the daemon on every keystroke.
        timer_attr = f"_lim_timer_{row.pid}_{key}"
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
            self._set_rule_field(row, key, rate)
            setattr(self, timer_attr, None)
            return False

        setattr(self, timer_attr, GLib.timeout_add(500, _send))

    def _on_priority(self, _dd, _pspec, row, dropdown):
        self._set_rule_field(row, "priority", dropdown.get_priority_name())

    def _set_rule_field(self, row: ProcessRow, field: str, value) -> None:
        """Create/update a rule for the row, then reload state from daemon."""
        blob = self._blob_for(row)
        if blob is None:
            self.gui.show_error("Process is no longer active.")
            return
        rule = dict(blob.get("rule") or {})
        # determine match from process name/exe
        match_type = _match_type_for(blob)
        match_value = _match_value_for(blob, match_type)
        if not rule.get("key"):
            rule["name"] = _short_name(blob.get("name", "Process"), 24)
            rule["match_type"] = match_type
            rule["match_value"] = match_value
            rule["download_limit"] = None
            rule["upload_limit"] = None
            rule["priority"] = "normal"
            rule["recursive"] = False
        rule[field] = value
        try:
            self.gui.client.call("set_process", rule)
            self.gui.reload()
        except Exception as error:
            self.gui.show_error(str(error))

    def _blob_for(self, row: ProcessRow) -> dict | None:
        for blob in self.gui_client_state().get("processes", []):
            if str(blob.get("pid")) == row.pid:
                return blob
        return None


def _full_name_hint(row: ProcessRow) -> str:
    return row.name


def _find_rule_for(proc: dict, rules: list) -> dict:
    """Heuristic: first rule whose literal match_value appears in the name, or
    whose 'name' equals the process name. Used purely for display/edit mapping."""
    name = proc.get("name", "")
    for rule in rules:
        if rule.get("name") and rule["name"].lower() in name.lower():
            return rule
        mv = rule.get("match_value")
        if mv and (mv in name or name.endswith(mv)):
            return rule
    return {}


def _match_type_for(blob: dict) -> str:
    """Choose exe/name match based on the shape of the process name."""
    name = blob.get("name", "")
    if name.startswith("/"):
        return "exe"
    return "name"


def _match_value_for(blob: dict, match_type: str) -> str:
    vals = {
        "exe": blob.get("name", ""),
        "name": blob.get("name", "").rsplit("/", 1)[-1],
    }
    return vals.get(match_type, "")


def _prio_name(index: int) -> str:
    from .widgets import PRIORITY_NAMES

    if 0 <= index < len(PRIORITY_NAMES):
        return PRIORITY_NAMES[index]
    return "normal"
