"""Throtl daemon: Unix-socket server, TrafficToll engine control, monitoring.

Architecture:
    [Dashboard / CLI] --Unix socket (JSON)-- [Daemon]
        ├── TrafficTollEngine  -> tt subprocess (tc + cgroups, root)
        ├── NethogsMonitor     -> live per-process bandwidth
        └── ConfigStore        -> ~/.config/throtl/config.toml

IPC messages: see throtl.protocol (request/response/event).

Daemon methods:
    status                      -> daemon/engine/monitor status
    get_config / get_state      -> full resp. summarised config
    set_global {key:value}      -> adjust global limits/priorities
    set_process {...}           -> create/update a rule (by key)
    remove_process {key}        -> delete a rule
    toggle_enabled {enabled}    -> global shaping on/off
    set_unit {unit}             -> GUI display unit (kbps|kBs)
    list_processes []           -> live stats snapshot

Events:
    stats  (after every monitoring tick; carries process stats + rules)
"""

import argparse
import atexit
import copy
import os
import re
import signal
import socket
import sys
import threading
import time

from . import RUN_DIR, SOCKET_GROUP, SOCKET_MODE, SOCKET_PATH, __version__
from .config import (
    active_scheduled_profile,
    apply_profile,
    capture_profile,
    config_dir_default,
    config_path_for,
    delete_profile,
    detect_default_interface,
    last_config_warning,
    load_config,
    make_rule,
    normalize,
    normalize_schedule,
    normalize_window,
    priority_to_int,
    priority_to_name,
    profile_names,
    rule_active,
    save_config,
    unescape_pattern,
    validate_profile_name,
)
from .engine import SimEngine, TrafficTollEngine
from .monitor import (
    UNATTRIBUTED_NAME,
    UNATTRIBUTED_PID,
    NethogsMonitor,
    is_interpreted_cmdline,
    pretty_app_name,
)
from .protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    ProtocolError,
    iter_messages,
    make_error,
    send_message,
)
from .stats import VALID_WINDOWS, StatsStore
from .units import parse_rate  # noqa: F401  (re-export, CLI/protocol compatibility)

# Sentinel: "argument not passed" -> use the default monitor. An explicit
# ``monitor_factory=None`` means "monitoring off" (tests/simulation), so no
# nethogs subprocess is started.
_MONITOR_DEFAULT = object()


class ConfigStore:
    """Loads/holds/persists the config in TOML."""

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        self.path = config_path_for(config_dir)
        self._config = load_config(self.path)
        # Keep the load warning (broken TOML -> defaults). The daemon still
        # starts, but status() makes the problem visible.
        self.load_warning = last_config_warning()

    def get(self):
        return self._config

    def _persist(self) -> None:
        os.makedirs(self.config_dir, exist_ok=True)
        save_config(self.path, self._config)

    def update_global(self, **changes) -> dict:
        g = self._config["global"]
        allowed = {
            "enabled", "download_limit", "upload_limit",
            "download_minimum", "upload_minimum",
            "download_priority", "upload_priority",
        }
        for key in changes:
            if key not in allowed:
                raise ValueError(f"unknown global key {key!r}")
        if "enabled" in changes:
            g["enabled"] = _as_bool(changes["enabled"])
        for key in ("download_limit", "upload_limit", "download_minimum", "upload_minimum"):
            if key in changes:
                if changes[key] in (None, ""):
                    g[key] = None if key.endswith("limit") else g[key]
                else:
                    from .units import parse_rate

                    rate = parse_rate(changes[key])
                    if key.endswith("minimum"):
                        if rate is None:
                            continue
                    g[key] = rate
        for key in ("download_priority", "upload_priority"):
            if key in changes and changes[key] is not None:
                g[key] = priority_to_name(changes[key])
        self._persist()
        return dict(g)

    def upsert_process(self, rule: dict) -> dict:
        rules = self._config["processes"]
        for index, existing in enumerate(rules):
            if existing.get("key") == rule.get("key"):
                rules[index] = rule
                break
        else:
            rules.append(rule)
        self._persist()
        return rule

    def remove_process(self, key: str) -> bool:
        rules = self._config["processes"]
        before = len(rules)
        self._config["processes"] = [r for r in rules if r.get("key") != key]
        removed = len(self._config["processes"]) != before
        if removed:
            self._persist()
        return removed

    def set_unit(self, unit: str) -> str:
        from .units import DISPLAY_UNITS

        if unit not in DISPLAY_UNITS:
            raise ValueError(f"unit must be one of {DISPLAY_UNITS}")
        self._config["unit"] = unit
        self._persist()
        return unit

    def set_budget(self, app: str | None = None, **fields) -> dict:
        """Set/update a budget. ``app=None`` = the global budget.

        Only passed fields (``day``/``week``/``enabled``) are changed.
        """
        budgets = self._config.setdefault(
            "budgets", {"enabled": True, "day": None, "week": None, "rules": []}
        )
        allowed = {"day", "week", "enabled"}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown budget field {key!r}")
        if app:
            rules = budgets.setdefault("rules", [])
            target = None
            for rule in rules:
                if rule.get("app") == app:
                    target = rule
                    break
            if target is None:
                target = {"app": app, "day": None, "week": None}
                rules.append(target)
            for key, value in fields.items():
                if key != "enabled":
                    target[key] = value
        else:
            for key, value in fields.items():
                budgets[key] = value
        self._persist()
        return budgets

    def remove_budget(self, app: str) -> bool:
        budgets = self._config.get("budgets") or {}
        rules = budgets.get("rules") or []
        before = len(rules)
        budgets["rules"] = [r for r in rules if r.get("app") != app]
        removed = len(budgets["rules"]) != before
        if removed:
            self._persist()
        return removed

    def replace(self, config: dict) -> dict:
        """Replace the complete config (import/profiles) and persist it."""
        self._config = config
        self._persist()
        return self._config


def _as_bool(value) -> bool:
    """Read socket booleans robustly: ``"false"``/``"0"``/``"off"`` are False.

    ``bool("false")`` is True — a bug that flips budgets or shaping exactly
    opposite to what was wanted.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "ja", "an", "yes")
    return bool(value)


def parse_limit_param(value):
    """Accept a limit param: None->unlimited, number, or rate string."""
    if value is None or value in ("", "null", "none", "unbegrenzt", "unlimited"):
        return None
    from .units import parse_rate as pr

    return pr(value)


def _resolve_interface(value) -> str:
    """Resolve the config value 'auto'/None/'' into the real routing interface."""
    if value in (None, "", "auto", "automatic"):
        return detect_default_interface() or "lo"
    return value


def available_interfaces() -> list:
    """Real, shapeable network interfaces (no loopback, mirrors or container
    bridges)."""
    names = []
    try:
        for name in os.listdir("/sys/class/net"):
            if name == "lo" or name.startswith(("ifb", "docker", "veth", "br-")):
                continue
            names.append(name)
    except OSError:
        pass
    return sorted(names)


def _resolve_tt_command(value) -> str:
    """Determine the tt binary when none was given explicitly.

    Order: argument -> $THROTL_TT -> venv path from install.sh -> PATH. Before,
    the default was the bare name 'tt', which does not exist outside the venv
    PATH ("TrafficToll (tt) was not found").
    """
    import shutil

    if value:
        return value
    env = os.environ.get("THROTL_TT")
    if env:
        return env
    venv_tt = "/opt/throtl/venv/bin/tt"
    if os.path.exists(venv_tt):
        return venv_tt
    return shutil.which("tt") or "tt"


def preflight(tt_command: str, interface: str) -> list:
    """Root/tool preflight: returns a list of warnings/errors."""
    import shutil

    issues = []
    if os.geteuid() != 0:
        issues.append("daemon is not running as root (tc/cgroups/nethogs need root)")
    if not os.path.exists(tt_command):
        issues.append(f"tt not found: {tt_command}")
    for tool in ("tc", "ip", "iptables"):
        if shutil.which(tool) is None:
            issues.append(f"command missing: {tool}")
    # Check the ifb module (needed for TrafficToll's download shaping)
    try:
        if not os.path.exists("/sys/module/ifb"):
            with open("/proc/modules", "r", encoding="utf-8") as handle:
                if "ifb" not in handle.read():
                    issues.append("kernel module 'ifb' not loaded (sh -c 'modprobe ifb')")
    except OSError:
        pass
    if interface in (None, "", "auto", "lo"):
        issues.append(f"non-local interface missing (current: {interface!r}). "
                      "Shaping needs a real routing interface (e.g. enp34s0).")
    return issues


class Daemon:
    def __init__(self, socket_path: str = SOCKET_PATH, config_dir: str | None = None,
                 engine=None, monitor_factory=_MONITOR_DEFAULT, interval: float = 1.0,
                 tt_command: str = "tt"):
        self.socket_path = socket_path
        self.config_dir = config_dir or config_dir_default()
        self.store = ConfigStore(self.config_dir)
        self._state_lock = threading.RLock()
        # Serialises monitor ticks: the ticker thread and RPC threads (after
        # rule changes) must not write stats in parallel.
        self._tick_lock = threading.Lock()
        # Leaf lock for the /proc/net/dev sample: several callers (monitor tick
        # and GUI polls) share it, and it must not be the re-entrancy-prone
        # _tick_lock because _collect_snapshot runs under it.
        self._iface_lock = threading.Lock()
        self.interval = interval
        # Persistent bandwidth statistics (item 5): ring buffer next to
        # config.toml, fed in the monitor tick.
        self.stats = StatsStore(self.config_dir, interval=interval)

        cfg = self.store.get()
        self.interface = _resolve_interface(cfg.get("interface"))

        # Choose the engine: explicit (tests) or TrafficTollEngine (tt via venv)
        self.engine = engine
        self._tt_command = tt_command
        if self.engine is None:
            run_dir = os.environ.get("THROTL_RUN_DIR") or RUN_DIR
            self.engine = TrafficTollEngine(
                self.interface, command=tt_command,
                log_path=os.path.join(run_dir, "tt.log"),
            )

        self.monitor = None
        self.monitor_error = None
        self.monitor_last_crash = None
        self._monitor_starts = 0
        self.engine_error = None
        self._monitor_retry_tick = 0
        self._monitor_factory = (
            (lambda dev, i: NethogsMonitor(dev, interval=i))
            if monitor_factory is _MONITOR_DEFAULT
            else monitor_factory
        )

        self._server = None
        self._running = True
        self._monitor_thread = None
        self._iface_sample = None
        self._iface_rate = (None, None)
        # Signature of the currently active time-window rules (engine re-apply).
        self._window_signature = None
        # Signature of the currently over-budget apps (engine re-apply).
        self._budget_signature = None
        # Exponential backoff for automatically re-applying a dead tt process.
        self._engine_recovery_attempts = 0
        self._engine_recovery_last = 0.0
        # Last alert level per budget key (f"{scope}:{app}:{window}"): lets
        # get_budgets emit an alert only when a budget crosses a level.
        self._budget_levels: dict = {}
        # Engine restarts run in their own thread (a tt apply takes ~2 s and
        # must block neither RPC responses nor the GUI).
        self._apply_event = threading.Event()
        self._apply_thread = None
        self._engine_applying = False

        atexit.register(self.shutdown)

    # --- Lifecycle ---

    def _socket_is_live(self) -> bool:
        """Is a reachable daemon listening on the socket path?"""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect(self.socket_path)
        except OSError:
            return False
        finally:
            probe.close()
        return True

    def _prepare_socket_path(self) -> None:
        """Remove a stale socket file; abort when a daemon is running.

        A plain unlink() would steal the socket of a running daemon: it would
        stay root and active with tt/tc, but no longer be reachable, and a
        second daemon would go after the same state.
        """
        if not os.path.exists(self.socket_path):
            return
        if self._socket_is_live():
            raise RuntimeError(
                f"A Throtl daemon is already running on {self.socket_path}. "
                "Start aborted so it does not become unreachable."
            )
        try:
            os.unlink(self.socket_path)
        except OSError as error:
            print(f"Warning: socket preparation: {error}")

    def start(self) -> None:
        # Check FIRST (before engine/monitor side effects) whether a daemon is
        # already listening on the path.
        self._prepare_socket_path()
        # Activate a configured startup profile before the first apply.
        self._apply_start_profile()
        # Apply the initial config synchronously; then the worker takes over.
        self._apply_engine()
        self._start_apply_worker()
        self._start_monitor()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        except OSError as error:
            print(f"Warning: socket preparation: {error}")
        self._server.bind(self.socket_path)
        self._server.listen(8)
        self._server.settimeout(0.25)
        self._secure_socket()
        # Start the monitoring ticker
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="throtl-monitor-ticker"
        )
        self._monitor_thread.start()
        print(f"Throtl daemon {__version__} running on {self.socket_path}")

    def _secure_socket(self) -> None:
        """Restrict access to the daemon socket to the group ``throtl``.

        As root the socket path is created rw-------; without write permission
        the user process's (GUI/CLI) connect fails with "Permission denied". The
        previously used mode 0666 fixed that, but gave EVERY local account full
        access: an unprivileged user could set global limits, throttle the
        network or delete rules.

        Now: ``chown root:throtl`` + mode 0660. Only group members (via
        ``usermod -aG throtl $USER``) reach the daemon.

        If the group is missing (e.g. a manual start without install.sh), the
        socket is NOT opened for everyone: it stays owner-only (0600). Before,
        0666 was set here, giving every local account full access to the root
        daemon (fail open).
        """
        import grp

        gid = None
        if os.geteuid() == 0:
            try:
                gid = grp.getgrnam(SOCKET_GROUP).gr_gid
            except KeyError:
                print(
                    f"Warning: group '{SOCKET_GROUP}' does not exist — the "
                    "daemon socket stays locked (root only). Fix: "
                    f"'groupadd {SOCKET_GROUP}', "
                    f"'usermod -aG {SOCKET_GROUP} $USER', then restart the "
                    "daemon (install.sh does this).",
                    flush=True,
                )
        try:
            if gid is not None:
                os.chown(self.socket_path, 0, gid)
                os.chmod(self.socket_path, SOCKET_MODE)
            else:
                # Fail closed: group missing or not root. Only the owner may
                # access. No 0666 fallback for the root daemon.
                os.chmod(self.socket_path, 0o600)
        except OSError as error:
            print(f"Warning: cannot set socket permissions: {error}", flush=True)

    def _monitor_loop(self) -> None:
        while self._running:
            # A faulty tick must not kill the ticker permanently: otherwise
            # monitoring, schedules, time windows and statistics stop.
            self._safe_tick_monitor()
            time.sleep(self.interval)

    def _safe_tick_monitor(self, record_stats: bool = True) -> None:
        """Run a tick that never leaves the caller with an exception."""
        try:
            self._tick_monitor(record_stats)
        except Exception as error:
            print(
                f"Warning: monitor tick failed: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    def _tick_monitor(self, record_stats: bool = True) -> None:
        with self._tick_lock:
            self._tick_monitor_locked(record_stats)

    def _tick_monitor_locked(self, record_stats: bool = True) -> None:
        # Detect a dead monitor (nethogs exited/crashed): remember the error,
        # clean up; the retry logic below restarts it. Monitor stubs without
        # is_alive() are conservatively treated as alive.
        if self.monitor is not None:
            checker = getattr(self.monitor, "is_alive", None)
            alive = True
            if checker is not None:
                try:
                    alive = bool(checker())
                except Exception:
                    alive = True
            if not alive:
                error = getattr(self.monitor, "last_error", None) or "nethogs process exited"
                self.monitor_error = error
                self.monitor_last_crash = error
                print(f"Monitor error: {error}", flush=True)
                self._stop_monitor()
        # Pull the monitor up if (still) none is running. Every ~3 ticks again.
        if self.monitor is None:
            self._monitor_retry_tick += 1
            if self._monitor_retry_tick >= 3:
                self._monitor_retry_tick = 0
                self._start_monitor()
        # Automatic profile switch (only when a schedule exists).
        self._apply_schedule()
        # Time-window rules: re-apply the engine when a window flips.
        self._apply_time_windows()
        # Budget enforcement: re-apply when the over-budget set changes.
        self._apply_budget_enforcement()
        # tt crashed? Then re-apply automatically (otherwise shaping would stay
        # silently off until the user happens to change a rule).
        self._recover_engine()
        self._check_interface_change()
        # Real monitoring tick: advance the statistics. RPC snapshots
        # (list_processes) must NOT count additionally, otherwise the GUI poll
        # would book the rates twice.
        self._collect_snapshot(record_stats=record_stats)

    def _apply_schedule(self) -> None:
        """Activate the matching schedule profile (in the monitor tick).

        Only when rules exist AND one matches NOW. Outside all windows we
        deliberately do NOT switch back, but keep the last choice (conservative:
        a manually chosen profile must not be overwritten mid-day).
        """
        with self._state_lock:
            cfg = self.store.get()
            if not cfg.get("schedule"):
                return
            target = active_scheduled_profile(cfg)
            if not target or target == cfg.get("active_profile"):
                return
            try:
                apply_profile(cfg, target)
            except Exception as error:
                print(f"Warning: schedule profile {target!r}: {error}", flush=True)
                return
            self.store._persist()
        self._schedule_engine_apply()

    def _apply_start_profile(self) -> None:
        """Activate a configured ``start_profile`` at daemon start.

        A schedule has priority: it is applied at the next monitor tick and
        overwrites the startup profile if a window matches right now.
        """
        with self._state_lock:
            cfg = self.store.get()
            name = cfg.get("start_profile")
            if not name or name == cfg.get("active_profile"):
                return
            try:
                apply_profile(cfg, name)
            except Exception as error:
                print(f"Warning: startup profile {name!r}: {error}", flush=True)
                return
            self.store._persist()

    def _apply_time_windows(self) -> None:
        """Re-apply the engine when the active time-window rules change.

        TrafficToll has no dynamic reload; so that "Firefox 20-24h" works, an
        apply is triggered on the transition into/out of a window. The signature
        is the set of currently active rule keys.
        """
        with self._state_lock:
            rules = list(self.store.get().get("processes") or [])
        if not any(rule.get("window") for rule in rules):
            self._window_signature = None
            return
        signature = tuple(sorted(
            rule.get("key", "") for rule in rules if rule_active(rule)
        ))
        if signature != self._window_signature:
            self._window_signature = signature
            self._schedule_engine_apply()

    def _enforced_config(self, cfg: dict) -> dict:
        """Apply the budget floor to over-budget apps when enforcement is on.

        Returns the (possibly overridden) config WITHOUT touching the stored
        rules — the floor is applied to the copy that gets rendered for tt.
        """
        budgets = cfg.get("budgets") or {}
        if not budgets.get("enforce"):
            return cfg
        floor = budgets.get("floor")
        if floor is None:
            return cfg
        from .budgets import budget_status

        status = budget_status(cfg, self.stats)
        over_global = any(e["scope"] == "global" and e["exceeded"] for e in status)
        over_apps = {e["app"] for e in status
                     if e["scope"] == "app" and e["exceeded"] and e["app"]}
        if not over_global and not over_apps:
            return cfg
        cfg = copy.deepcopy(cfg)
        if over_global:
            cfg["global"]["download_limit"] = floor
            cfg["global"]["upload_limit"] = floor
        for rule in cfg.get("processes", []):
            if rule.get("name") in over_apps:
                rule["download_limit"] = floor
                rule["upload_limit"] = floor
        return cfg

    def _apply_budget_enforcement(self) -> None:
        """Re-apply the engine when the set of over-budget apps changes."""
        with self._state_lock:
            cfg = self.store.get()
        if not (cfg.get("budgets") or {}).get("enforce"):
            self._budget_signature = None
            return
        from .budgets import budget_status

        status = budget_status(cfg, self.stats)
        over_global = any(e["scope"] == "global" and e["exceeded"] for e in status)
        over_apps = frozenset(e["app"] for e in status
                              if e["scope"] == "app" and e["exceeded"] and e["app"])
        signature = (over_global, over_apps)
        if signature != self._budget_signature:
            self._budget_signature = signature
            self._schedule_engine_apply()

    def _iface_throughput(self):
        """Real interface rate (kbit/s) from /proc/net/dev deltas.

        This is the reliable "global" number: it contains EVERYTHING that goes
        over the interface (also traffic nethogs cannot attribute to a process,
        e.g. VPN/UDP/other users). Without a comparison value -> (None, None).
        """
        import time as _time

        with self._iface_lock:
            try:
                rx = tx = None
                with open("/proc/net/dev", "r", encoding="utf-8") as handle:
                    for line in handle:
                        if ":" not in line:
                            continue
                        name, rest = line.split(":", 1)
                        if name.strip() != self.interface:
                            continue
                        fields = rest.split()
                        rx, tx = int(fields[0]), int(fields[8])
                        break
            except (OSError, ValueError, IndexError):
                return self._iface_rate
            if rx is None:
                return self._iface_rate
            now = _time.monotonic()
            previous = self._iface_sample
            if previous is None:
                self._iface_sample = (now, rx, tx)
                return self._iface_rate
            elapsed = now - previous[0]
            # Only update the sample when enough time has passed. Several
            # callers (monitor tick + GUI poll) share this sample; earlier every
            # call reset the sample and short gaps yielded None -> the global
            # line flickered to "measuring...".
            if elapsed >= 0.5:
                down = max(0, rx - previous[1]) * 8.0 / 1000.0 / elapsed
                up = max(0, tx - previous[2]) * 8.0 / 1000.0 / elapsed
                self._iface_rate = (round(down, 1), round(up, 1))
                self._iface_sample = (now, rx, tx)
            return self._iface_rate

    def _collect_snapshot(self, record_stats: bool = False) -> dict:
        """Process stats + real interface rate + applied rules.

        ``record_stats=True`` (only from :meth:`_tick_monitor`) additionally
        writes the per-app sums into the persistent :class:`StatsStore`.
        """
        raw = {}
        if self.monitor is not None:
            try:
                raw = self.monitor.snapshot()
            except Exception:
                raw = {}
        with self._state_lock:
            # Copy the rule list: a concurrent set_process/import must not blow
            # up the iteration below ("list changed size").
            cfg = self.store.get()
            rules = list(cfg.get("processes", []))
            enabled = cfg["global"].get("enabled", True)
        processes = []
        attributed_down = attributed_up = 0.0
        for pid, info in raw.items():
            matches = _match_rules(rules, info.get("name"), info.get("pid", pid))
            download = round(info.get("download", 0.0), 3)
            upload = round(info.get("upload", 0.0), 3)
            # nethogs sees ingress BEFORE the shaping (on ifb0), so a limit does
            # not lower the measured download. Cap at the rule's limit so the
            # GUI/stats show the effective rate the process actually receives.
            dl = matches.get("download_limit")
            ul = matches.get("upload_limit")
            if dl is not None:
                download = min(download, dl)
            if ul is not None:
                upload = min(upload, ul)
            if pid != UNATTRIBUTED_PID:
                attributed_down += download
                attributed_up += upload
            processes.append({
                "pid": pid,
                "name": info.get("name", "?"),
                "download": download,
                "upload": upload,
                "unattributed": pid == UNATTRIBUTED_PID,
                # via rules: limits/priority for the display
                "rule_name": matches.get("name"),
            })
        # Group by application: an app often runs in many processes (e.g. a
        # downloader with 8 workers). The app should appear as ONE row with the
        # sum — otherwise you see 8x "python3" at ~0.2 MB/s each instead of one
        # "legendary" at ~2 MB/s.
        apps = {}
        for pid, info in raw.items():
            if pid == UNATTRIBUTED_PID:
                app, exe, matched, interpreted = UNATTRIBUTED_NAME, "", {}, False
            else:
                cmdline = info.get("name", "")
                app = pretty_app_name(cmdline)
                exe = (cmdline.split() or [""])[0]
                matched = _match_rules(rules, cmdline)
                interpreted = is_interpreted_cmdline(cmdline)
            entry = apps.get(app)
            if entry is None:
                entry = apps[app] = {
                    "name": app,
                    "exe": exe,
                    "download": 0.0,
                    "upload": 0.0,
                    "pids": [],
                    "unattributed": pid == UNATTRIBUTED_PID,
                    "rule": matched or {},
                    "interpreted": interpreted,
                }
            elif matched and not entry.get("rule"):
                entry["rule"] = matched
            download = info.get("download", 0.0)
            upload = info.get("upload", 0.0)
            dl = matched.get("download_limit")
            ul = matched.get("upload_limit")
            if dl is not None:
                download = min(download, dl)
            if ul is not None:
                upload = min(upload, ul)
            entry["download"] += download
            entry["upload"] += upload
            if len(entry["pids"]) < 16:
                entry["pids"].append(pid)
        app_list = []
        for entry in apps.values():
            entry["download"] = round(entry["download"], 3)
            entry["upload"] = round(entry["upload"], 3)
            entry["pid_count"] = len(entry["pids"])
            matched_rule = entry.pop("rule", {}) or {}
            if entry["unattributed"]:
                entry["rule_name"] = None
                entry["rule_key"] = None
                entry["match_hint"] = None
            else:
                entry["rule_name"] = matched_rule.get("name")
                entry["rule_key"] = matched_rule.get("key")
                if entry.pop("interpreted", False):
                    # argv[0] is an interpreter: an exe rule would match every
                    # script under it. Pin the script name via a cmdline regex
                    # (the leading ".*" satisfies tt's start-anchored re.match).
                    entry["match_hint"] = {
                        "type": "cmdline",
                        "value": f".*{re.escape(entry['name'])}",
                    }
                else:
                    entry["match_hint"] = {"type": "exe", "value": entry["exe"]}
            app_list.append(entry)

        if record_stats:
            self._record_stats(app_list)

        global_down, global_up = self._iface_throughput()
        return {
            "interface": self.interface,
            "enabled": enabled,
            "processes": processes,
            "apps": app_list,  # grouped per application (sum of all PIDs)
            "rules": rules,  # for GUI: union of rule + live stats
            "monitored": self.monitor is not None,
            # Real interface rate (everything) vs. only attributed traffic
            "global": {"download": global_down, "upload": global_up},
            "attributed": {"download": round(attributed_down, 1),
                           "upload": round(attributed_up, 1)},
        }

    def _record_stats(self, app_list: list) -> None:
        """Book the per-app rates (kbit/s) into the statistics store."""
        for entry in app_list:
            self.stats.record(
                entry.get("name", "?"),
                entry.get("download", 0.0),
                entry.get("upload", 0.0),
            )

    # --- Engine ---

    def _apply_engine(self, config: dict | None = None) -> None:
        """Sync the engine (tt) with the current/passed config.

        Called once synchronously at start; afterwards the background worker
        (:meth:`_schedule_engine_apply`) takes over, so a ~2 s tt restart blocks
        neither RPC responses nor the GUI.
        """
        if config is None:
            config = self._snapshot_config()
        config = self._enforced_config(config)
        try:
            self.engine.apply(config)
        except Exception as error:
            self.engine_error = f"{type(error).__name__}: {error}"
            print(f"Engine error: {self.engine_error}", flush=True)
        else:
            self.engine_error = None

    def _snapshot_config(self) -> dict:
        """Deep copy of the config under the state lock (never render live data)."""
        with self._state_lock:
            return copy.deepcopy(self.store.get())

    def _start_apply_worker(self) -> None:
        self._apply_thread = threading.Thread(
            target=self._apply_loop, daemon=True, name="throtl-engine-apply"
        )
        self._apply_thread.start()

    def _apply_loop(self) -> None:
        """Serialise engine restarts and coalesce quick changes."""
        while self._running:
            self._apply_event.wait(timeout=0.5)
            if not self._running:
                break
            if not self._apply_event.is_set():
                continue
            self._apply_event.clear()
            self._engine_applying = True
            try:
                self._apply_engine()
            finally:
                self._engine_applying = False

    def _schedule_engine_apply(self) -> None:
        """Request an engine apply: non-blocking and coalesced."""
        self._apply_event.set()

    def _recover_engine(self) -> None:
        """Automatically re-apply a dead tt process, with exponential backoff.

        Only when shaping should be active at all (``enabled``); a deliberately
        disabled state is not restarted endlessly, and a crashing tt is retried
        at 1s, 2s, 4s, ... capped at 60s instead of every tick.
        """
        try:
            running = bool(self.engine.status().get("running"))
        except Exception:
            return
        if running:
            self._engine_recovery_attempts = 0
            self._engine_recovery_last = 0.0
            return
        if not self._snapshot_config()["global"].get("enabled", True):
            return
        delay = min(2.0 ** self._engine_recovery_attempts, 60.0)
        if time.monotonic() - self._engine_recovery_last < delay:
            return
        self._engine_recovery_attempts += 1
        self._engine_recovery_last = time.monotonic()
        self._schedule_engine_apply()

    def _check_interface_change(self) -> None:
        """Follow a default-route interface change (WLAN<->LAN, VPN up/down).

        Only when the config asks for auto-detection; a pinned interface is
        deliberately left alone.
        """
        with self._state_lock:
            requested = self.store.get().get("interface")
        if requested not in (None, "", "auto", "automatic"):
            return
        detected = detect_default_interface()
        if not detected or detected == self.interface:
            return
        self._rebind_interface(detected)

    def _rebind_interface(self, new: str) -> None:
        """Point the engine and monitor at a new interface."""
        print(f"Interface changed: {self.interface} -> {new}", flush=True)
        self._stop_monitor()
        set_device = getattr(self.engine, "set_device", None)
        if set_device is not None:
            set_device(new)
        else:
            self.engine.device = new
        self.interface = new
        # Drop the stale /proc/net/dev sample so the global rate re-measures on
        # the new interface instead of mixing old and new counters.
        self._iface_sample = None
        self._iface_rate = (None, None)
        self._schedule_engine_apply()
        self._start_monitor()

    def _start_monitor(self) -> None:
        if self._monitor_factory is None:
            return  # monitoring deliberately disabled (tests/simulation)
        if self.monitor is None:
            self.monitor = self._monitor_factory(self.interface, 1.0)
            try:
                self.monitor.start()
            except Exception as error:
                self.monitor_error = f"{type(error).__name__}: {error}"
                print(f"Monitor error: {self.monitor_error}", flush=True)
                self.monitor = None
            else:
                self.monitor_error = None
                self._monitor_starts += 1

    def _stop_monitor(self) -> None:
        if self.monitor is not None:
            try:
                self.monitor.stop()
            except Exception:
                pass
            self.monitor = None

    # --- IPC Server Loop ---

    def serve_forever(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._running:
                    break
                continue
            thread = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
            thread.start()

    def _handle_connection(self, conn) -> None:
        with conn:
            try:
                for message in iter_messages(conn):
                    if message is None:
                        break
                    response = self._dispatch(message)
                    if response is not None:
                        try:
                            send_message(conn, response)
                        except OSError:
                            break
            except ProtocolError:
                # A broken/too-large/incomplete frame ends only this connection —
                # earlier the exception escaped as a traceback from the thread
                # (log spam, cheap local DoS).
                pass

    def _dispatch(self, message) -> dict:
        if not isinstance(message, dict):
            # Valid JSON, but not an object (123, "x", [], true) would otherwise
            # kill the connection thread with an AttributeError.
            return {"id": None,
                    "error": make_error(INVALID_PARAMS, "message must be an object")}
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None or not method:
            return {"id": message_id, "error": make_error(INVALID_PARAMS, "id/method missing")}
        handler = self._handlers().get(method)
        if handler is None:
            return {"id": message_id, "error": make_error(METHOD_NOT_FOUND, f"unknown method: {method}")}
        try:
            result = handler(params)
            return {"id": message_id, "result": result}
        except (ValueError, TypeError) as error:
            return {"id": message_id, "error": make_error(INVALID_PARAMS, str(error))}
        except Exception as error:
            return {"id": message_id, "error": make_error(-32000, f"{type(error).__name__}: {error}")}

    def _handlers(self):
        return {
            "status": self._h_status,
            "get_config": self._h_get_config,
            "get_state": self._h_get_state,
            "set_global": self._h_set_global,
            "set_process": self._h_set_process,
            "remove_process": self._h_remove_process,
            "toggle_enabled": self._h_toggle_enabled,
            "set_unit": self._h_set_unit,
            "list_processes": self._h_list_processes,
            "get_stats": self._h_get_stats,
            "reset_stats": self._h_reset_stats,
            "get_stats_history": self._h_get_stats_history,
            "get_budgets": self._h_get_budgets,
            "set_budget": self._h_set_budget,
            "remove_budget": self._h_remove_budget,
            "list_profiles": self._h_list_profiles,
            "set_profile": self._h_set_profile,
            "delete_profile": self._h_delete_profile,
            "activate_profile": self._h_activate_profile,
            "set_schedule": self._h_set_schedule,
            "set_start_profile": self._h_set_start_profile,
            "list_interfaces": self._h_list_interfaces,
            "set_interface": self._h_set_interface,
            "import_config": self._h_import_config,
        }

    # --- Handler ---

    def _h_status(self, params):
        engine_status = None
        try:
            engine_status = self.engine.status() if self.engine else None
        except Exception:
            engine_status = None
        with self._state_lock:
            # No live dict: json.dumps would race a concurrent write.
            cfg = copy.deepcopy(self.store.get())
        tt_cmd = getattr(self, "_tt_command", "tt")
        return {
            "daemon": __version__,
            "pid": os.getpid(),
            "interface": self.interface,
            "enabled": cfg["global"].get("enabled", True),
            "monitoring": self.monitor is not None,
            "monitor_alive": bool(getattr(self.monitor, "is_alive", lambda: False)())
            if self.monitor is not None else False,
            "monitor_error": self.monitor_error,
            "monitor_last_crash": self.monitor_last_crash,
            "monitor_starts": self._monitor_starts,
            "engine_error": self.engine_error,
            "engine_applying": self._engine_applying,
            "engine": engine_status,
            "simulated": getattr(self.engine, "simulated", False),
            "preflight": preflight(tt_cmd, self.interface),
            # Broken/invalid config.toml: the daemon keeps running with the
            # defaults, but the reason is queryable instead of only in the journal.
            "config_warning": self.store.load_warning,
            "socket": self._socket_permissions(),
        }

    def _socket_permissions(self) -> dict:
        """Effective permissions of the daemon socket (for status()/doctor)."""
        try:
            info = os.stat(self.socket_path)
        except OSError:
            return {"path": self.socket_path, "exists": False}
        uid = info.st_uid
        gid = info.st_gid
        group = None
        try:
            import grp

            group = grp.getgrgid(gid).gr_name
        except (KeyError, ImportError):
            pass
        mode = info.st_mode & 0o777
        return {
            "path": self.socket_path,
            "exists": True,
            "mode": oct(mode),
            "uid": uid,
            "gid": gid,
            "group": group,
            # 0666 = every local user may set limits -> insecure.
            "restricted": mode != 0o666,
        }

    def _h_get_config(self, params):
        with self._state_lock:
            # Deep copy: json.dumps must not collide with a concurrent set_budget
            # ("dict changed size during iteration").
            return copy.deepcopy(self.store.get())

    def _h_get_state(self, params):
        return self._collect_snapshot()

    def _h_set_global(self, params):
        store = self.store
        changes = dict(params)
        with self._state_lock:
            store.update_global(**changes)
            # Copy instead of the live table: json.dumps runs only after the
            # handler, i.e. after the lock.
            result = copy.deepcopy(store.get()["global"])
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return result

    def _h_set_process(self, params):
        key = params.get("key")
        with self._state_lock:
            # Lookup under the lock: two concurrent updates of the same rule must
            # not both read the old state (lost update).
            existing = None
            if key:
                existing = next(
                    (r for r in self.store.get().get("processes", []) if r.get("key") == key),
                    None,
                )
        if existing is not None:
            # Update an existing rule: do NOT re-derive match_type/match_value.
            # The GUI sends the stored rule back; a fresh make_rule() would
            # escape the already re.escape()-d pattern again and the rule would
            # no longer match.
            rule = dict(existing)
            if params.get("name"):
                rule["name"] = str(params["name"])
            if "download_limit" in params:
                rule["download_limit"] = parse_limit_param(params.get("download_limit"))
            if "upload_limit" in params:
                rule["upload_limit"] = parse_limit_param(params.get("upload_limit"))
            if params.get("priority") is not None:
                # ``0`` (critical) is false-but-true and was skipped before;
                # str(1) turned integer priorities into errors.
                rule["priority"] = priority_to_name(
                    priority_to_int(params["priority"])
                )
            if "recursive" in params:
                rule["recursive"] = bool(params.get("recursive"))
            if "window" in params:
                rule["window"] = normalize_window(params.get("window"))
            with self._state_lock:
                self.store.upsert_process(rule)
            self._schedule_engine_apply()
            self._emit_rules_changed()
            return rule

        name = str(params.get("name", ""))
        match_type = str(params.get("match_type", "exe"))
        match_value = str(params.get("match_value", ""))
        if not match_value:
            raise ValueError("match_value missing")
        raw_priority = params.get("priority")
        priority = (
            priority_to_name(priority_to_int(raw_priority))
            if raw_priority is not None
            else "normal"
        )
        rule = make_rule(
            name=name,
            match_type=match_type,
            match_value=match_value,
            download_limit=parse_limit_param(params.get("download_limit")),
            upload_limit=parse_limit_param(params.get("upload_limit")),
            priority=priority,
            recursive=bool(params.get("recursive", False)),
            key=params.get("key"),
            window=params.get("window"),
        )
        with self._state_lock:
            self.store.upsert_process(rule)
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return rule

    def _h_remove_process(self, params):
        key = str(params.get("key", ""))
        if not key:
            raise ValueError("key missing")
        with self._state_lock:
            removed = self.store.remove_process(key)
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return {"removed": removed}

    def _h_toggle_enabled(self, params):
        enabled = _as_bool(params.get("enabled"))
        with self._state_lock:
            self.store.update_global(enabled=enabled)
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return {"enabled": enabled}

    def _h_set_unit(self, params):
        unit = str(params.get("unit", "kbps"))
        with self._state_lock:
            unit = self.store.set_unit(unit)
        return {"unit": unit}

    def _h_list_processes(self, params):
        return self._collect_snapshot()

    def _h_get_stats(self, params):
        window = str(params.get("window") or "minute")
        if window not in VALID_WINDOWS:
            raise ValueError(
                f"window must be one of {', '.join(VALID_WINDOWS)}"
            )
        return {
            "window": window,
            "apps": self.stats.snapshot(window),
            "totals": self.stats.totals(window),
        }

    def _h_reset_stats(self, params):
        self.stats.reset()
        return {"ok": True}

    def _h_get_stats_history(self, params):
        window = str(params.get("window") or "minute")
        if window not in VALID_WINDOWS:
            raise ValueError(
                f"window must be one of {', '.join(VALID_WINDOWS)}"
            )
        return {"window": window, "series": self.stats.series(window)}

    def _h_get_budgets(self, params):
        from .budgets import budget_level, budget_status

        with self._state_lock:
            cfg = copy.deepcopy(self.store.get())
        entries = budget_status(cfg, self.stats)
        alerts = []
        with self._state_lock:
            for entry in entries:
                key = f"{entry.get('scope')}:{entry.get('app')}:{entry.get('window')}"
                level = budget_level(entry)
                if level > self._budget_levels.get(key, 0):
                    alerts.append({
                        "scope": entry.get("scope"),
                        "app": entry.get("app"),
                        "window": entry.get("window"),
                        "level": level,
                        "ratio": entry.get("ratio"),
                        "used": entry.get("used"),
                        "limit": entry.get("limit"),
                    })
                self._budget_levels[key] = level
        return {
            "enabled": (cfg.get("budgets") or {}).get("enabled", True),
            "entries": entries,
            "alerts": alerts,
        }

    def _h_set_budget(self, params):
        from .units import parse_size

        app = str(params.get("app") or "").strip() or None
        fields = {}
        for key in ("day", "week"):
            if key in params:
                fields[key] = parse_size(params.get(key))
        if "enabled" in params:
            fields["enabled"] = _as_bool(params.get("enabled"))
        with self._state_lock:
            budgets = copy.deepcopy(self.store.set_budget(app=app, **fields))
        return {"ok": True, "budgets": budgets}

    def _h_remove_budget(self, params):
        app = str(params.get("app") or "").strip()
        if not app:
            raise ValueError("app missing")
        with self._state_lock:
            removed = self.store.remove_budget(app)
        return {"removed": removed}

    # --- Profiles / schedules ---------------------------------------------

    def _h_list_profiles(self, params):
        with self._state_lock:
            cfg = copy.deepcopy(self.store.get())
        return {
            "profiles": profile_names(cfg),
            "active": cfg.get("active_profile"),
        }

    def _h_set_profile(self, params):
        """Save the current state as a named profile (optionally activate it)."""
        name = validate_profile_name(params.get("name"))
        activate = bool(params.get("activate", True))
        with self._state_lock:
            cfg = self.store.get()
            previous = cfg.get("active_profile")
            capture_profile(cfg, name)
            if activate:
                apply_profile(cfg, name)
            elif previous is not None:
                cfg["active_profile"] = previous
            self.store._persist()
        if activate:
            self._schedule_engine_apply()
        self._emit_rules_changed()
        return {"name": name, "active": self.store.get().get("active_profile")}

    def _h_delete_profile(self, params):
        name = validate_profile_name(params.get("name"))
        with self._state_lock:
            deleted = delete_profile(self.store.get(), name)
            if deleted:
                self.store._persist()
        return {"deleted": deleted, "name": name}

    def _h_activate_profile(self, params):
        name = validate_profile_name(params.get("name"))
        with self._state_lock:
            apply_profile(self.store.get(), name)
            self.store._persist()
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return {"active": name}

    def _h_set_schedule(self, params):
        rules = normalize_schedule(params.get("rules") or [])
        with self._state_lock:
            self.store.get()["schedule"] = rules
            self.store._persist()
        return {"schedule": rules}

    def _h_set_start_profile(self, params):
        """Set/delete the startup profile (``name`` missing/empty = disable)."""
        raw = params.get("name")
        name = validate_profile_name(raw) if raw and str(raw).strip() else None
        with self._state_lock:
            self.store.get()["start_profile"] = name
            self.store._persist()
        return {"start_profile": name}

    def _h_list_interfaces(self, params):
        with self._state_lock:
            configured = self.store.get().get("interface") or "auto"
        return {
            "interfaces": available_interfaces(),
            "current": self.interface,
            "configured": configured,
        }

    def _h_set_interface(self, params):
        value = str(params.get("interface", "")).strip()
        if not value:
            raise ValueError("interface missing")
        if value in ("auto", "automatic"):
            stored = None
        else:
            if not os.path.exists(f"/sys/class/net/{value}"):
                raise ValueError(f"unknown interface {value!r}")
            stored = value
        with self._state_lock:
            cfg = self.store.get()
            cfg["interface"] = stored
            self.store._persist()
        resolved = _resolve_interface(stored)
        if resolved != self.interface:
            self._rebind_interface(resolved)
        return {"interface": stored or "auto", "resolved": self.interface}

    def _h_import_config(self, params):
        """Validate a raw (TOML) config and adopt it completely."""
        data = params.get("config")
        if not isinstance(data, dict):
            raise ValueError("config missing or not a table")
        config = normalize(data)
        # The interface is baked into engine and monitor at start. A change in
        # the running daemon would split config from reality (get_config reports
        # something other than what tt/nethogs do), so reject it clearly instead
        # of silently keeping the old one.
        requested = _resolve_interface(config.get("interface"))
        if requested != self.interface:
            raise ValueError(
                f"interface change {self.interface} -> {requested} is not possible in the "
                "running daemon. Then 'sudo systemctl restart throtl' and repeat the "
                "import."
            )
        with self._state_lock:
            self.store.replace(config)
            result = copy.deepcopy(config)
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return result

    def _emit_rules_changed(self) -> None:
        # After a change, pull a fresh snapshot immediately so the next query
        # returns current rates and the /proc sample delta is not stale. Safe: a
        # broken rule must not throw an exception out of the RPC handler.
        #
        # ``record_stats=False``: this extra tick measures the same moment as
        # the 1 Hz ticker; booking it as a full second bloated the byte sums on
        # rule changes.
        self._safe_tick_monitor(record_stats=False)

    # --- Shutdown ---

    def shutdown(self) -> None:
        self._running = False
        self._apply_event.set()          # wake the apply worker
        if self._apply_thread is not None:
            self._apply_thread.join(timeout=5.0)
            self._apply_thread = None
        self._stop_monitor()
        # Stop the ticker thread, so that after shutdown no tick can start a
        # nethogs process anymore (otherwise an orphan remained).
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0 * max(0.1, self.interval) + 2.0)
            self._monitor_thread = None
        # Save the statistics at shutdown (otherwise the last <save_every ticks
        # are lost).
        try:
            self.stats.flush()
        except Exception:
            pass
        if self.engine is not None:
            try:
                self.engine.stop()
            except Exception:
                pass
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass


def _match_rules(rules, name=None, pid=None) -> dict:
    """Find the first matching rule for a nethogs process name/PID.

    ``name`` is the nethogs command line (captured with ``-l``): its first token
    is the executable (argv[0]) and the rest are the arguments. Rules match:

    * ``exe``     — the executable path or its basename (literal);
    * ``name``    — the executable's basename (literal);
    * ``cmdline`` — a literal substring of the whole command line.

    Stored ``match_value`` patterns are regex-escaped for TrafficToll — for the
    comparison here they must be converted back, otherwise no rule matches.

    ``cmdline`` rules are deliberately checked **literally** (substring): the
    pattern is a regex, but a user-supplied regex must not be executed in the
    root daemon (ReDoS). The authoritative regex evaluation happens in ``tt``
    itself; here it is only about the display mapping (``rule_name``).
    """
    if not name:
        return {}
    tokens = name.split()
    exe = tokens[0] if tokens else name
    exe_base = os.path.basename(exe.rstrip("/")) or exe
    for rule in rules:
        match_type = rule.get("match_type")
        match_value = rule.get("match_value")
        if not match_value:
            continue
        if match_type == "cmdline":
            needle = match_value
            if needle.startswith(".*"):
                needle = needle[2:]
            elif needle.startswith("^"):
                needle = needle[1:]
            if needle and needle in name:
                return rule
            continue
        mv = unescape_pattern(match_value)
        if not mv:
            continue
        if match_type == "exe":
            mv_base = os.path.basename(mv.rstrip("/")) or mv
            if mv == exe or mv_base == exe_base:
                return rule
        elif match_type == "name":
            if mv == exe_base:
                return rule
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Throtl daemon")
    parser.add_argument("--foreground", action="store_true",
                        help="run in the foreground (for systemd/testing)")
    parser.add_argument("--socket", default=SOCKET_PATH, help="Unix socket path")
    parser.add_argument("--config-dir", default=None,
                        help="config directory (default: ~/.config/throtl)")
    parser.add_argument("--interface", default=None,
                        help="network interface (default: auto)")
    parser.add_argument("--simulate", action="store_true",
                        help="tooling demo: use SimEngine (no root / no tt yet)")
    parser.add_argument("--tt-command", default=None,
                        help="Pfad zum tt-Binary (Default: automatisch ermitteln)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.config_dir is None:
        cfg_dir = config_dir_default()
    else:
        cfg_dir = args.config_dir
    if args.interface:
        # Override the interface: persist it in the config
        store = ConfigStore(cfg_dir)
        if store.get().get("interface") != args.interface:
            store.get()["interface"] = args.interface
            store._persist()

    daemon = Daemon(
        socket_path=args.socket,
        config_dir=cfg_dir,
        interval=1.0,
        tt_command=_resolve_tt_command(args.tt_command),
    )
    if args.interface:
        daemon.interface = args.interface
    if args.simulate:
        interface = args.interface or daemon.engine.device
        daemon.engine = SimEngine(device=interface)

    # Handle SIGTERM/SIGINT cleanly: otherwise the cleanup (stop monitor/engine,
    # remove socket) does not run on 'systemctl stop' or 'kill', and nethogs/tt
    # stay behind as orphans.
    def _request_stop(_signum, _frame):
        daemon._running = False

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    try:
        daemon.start()
    except RuntimeError as error:
        # e.g. a second daemon on the same socket: report cleanly.
        print(f"Error: {error}", file=sys.stderr)
        daemon.shutdown()
        return 1
    try:
        daemon.serve_forever()
    finally:
        daemon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
