"""Throtl CLI: control and test the daemon without the GUI.

Examples:
    throtl-cli status
    throtl-cli list-processes
    throtl-cli set-global --download-limit 2mbps --upload-limit 1mbps
    throtl-cli set-global --download-priority hoch
    throtl-cli set-process --name Firefox --exe /usr/lib/firefox/firefox \\
        --download-limit 200kbps --priority normal
    throtl-cli remove-process --key 'exe:/usr/lib/firefox/firefox'
    throtl-cli toggle --enabled false
    throtl-cli monitor           # live output every second
    throtl-cli top               # full-screen ranking (htop-style)
    throtl-cli selftest          # checks end-to-end whether limits apply
    throtl-cli profiles          # manage profiles
    throtl-cli stats --window day
    throtl-cli export --output throtl.toml
    throtl-cli doctor
"""

import argparse
import sys
import time

from . import SOCKET_PATH, socket_access_hint
from .protocol import Client, RpcError, TimeoutError_


def _client(args) -> Client:
    client = Client(args.socket or SOCKET_PATH)
    try:
        client.connect()
    except ConnectionError as error:
        sys.stderr.write(f"Error: {error}\n")
        hint = socket_access_hint(args.socket or SOCKET_PATH)
        if hint:
            sys.stderr.write(f"  {hint}\n")
        else:
            sys.stderr.write("Is the daemon running? (systemctl status throtl)\n")
        sys.exit(2)
    return client


def _fmt_rate(value, unit="auto"):
    from .units import format_rate

    return format_rate(value, unit)


def _fmt_bytes(value) -> str:
    """Format a byte volume human-readably (SI, 1000-steps)."""
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1000 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1000.0
    return f"{amount:.1f} TB"


def _priority_int(name):
    from .config import priority_to_int

    return priority_to_int(name)


def cmd_status(client, args):
    status = client.call("status")
    print(f"Throtl-Daemon {status.get('daemon')} (PID {status.get('pid')})")
    print(f"  Interface:   {status.get('interface')}")
    print(f"  Shaping:     {'ON' if status.get('enabled') else 'OFF'}")
    monitoring = "yes" if status.get("monitoring") else "no"
    if status.get("monitoring") and status.get("monitor_alive") is not None:
        monitoring += f" (alive={status.get('monitor_alive')}"
        monitoring += f", starts={status.get('monitor_starts')})"
    print(f"  Monitoring:  {monitoring}")
    engine = status.get("engine") or {}
    print(f"  Engine:      running={engine.get('running')} "
          f"generation={engine.get('generation')}")
    if engine.get("applies") is not None:
        last = engine.get("last_apply_seconds")
        avg = engine.get("avg_apply_seconds")
        print(f"  Applies:     {engine.get('applies')} "
              f"(restarts={engine.get('restarts')}, "
              f"failures={engine.get('apply_failures')}, "
              f"last={last if last is not None else '-'}s, "
              f"avg={avg if avg is not None else '-'}s)")
    if status.get("engine_error"):
        print(f"  Engine error: {status['engine_error']}")
    if engine.get("last_error"):
        print(f"  Engine last error: {engine['last_error']}")
    if status.get("monitor_error"):
        print(f"  Monitor error: {status['monitor_error']}")
    if status.get("monitor_last_crash"):
        print(f"  Monitor last crash: {status['monitor_last_crash']}")
    if engine.get("exit_code") is not None:
        print(f"  Engine exit: {engine.get('exit_code')} "
              f"(latest tt crash; see stderr/below)")
    stderr = engine.get("stderr_tail") or []
    if stderr:
        print("  Engine stderr (last lines):")
        for line in stderr[-3:]:
            print(f"    | {line}")
    issues = status.get("preflight") or []
    if issues:
        print("  Preflight warnings:")
        for issue in issues:
            print(f"    ! {issue}")
    if status.get("simulated"):
        print("  (simulation mode: no real tc rules)")
    return 0


def _proc_display(name: str, limit: int = 32) -> str:
    """nethogs names the whole command line -> truncate for display."""
    first = (name or "?").split()
    base = (first[0] if first else name).strip().strip('"').strip("'")
    base = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base if len(base) <= limit else base[: limit - 1] + "…"


def cmd_list(client, args):
    from .config import format_window

    state = client.call("list_processes")
    print(f"{'PID':<8}{'Process':<34}{'Down (kbit/s)':<18}{'Up (kbit/s)':<16}Rule")
    print("-" * 90)
    for proc in state.get("processes", []):
        rule = proc.get("rule_name") or "-"
        print(
            f"{proc.get('pid','?'):<8}{_proc_display(proc.get('name','?')):<34}"
            f"{_fmt_rate(proc.get('download')):<18}{_fmt_rate(proc.get('upload')):<16}{rule}"
        )
    print(f"\nRules ({len(state.get('rules', []))}):")
    for rule in state.get("rules", []):
        dl = _fmt_rate(rule.get("download_limit"))
        ul = _fmt_rate(rule.get("upload_limit"))
        print(
            f"  {rule.get('key'):<44} dl={dl:<12} ul={ul:<12} "
            f"prio={rule.get('priority')}"
            + (f"  window={format_window(rule.get('window'))}"
               if rule.get("window") else "")
        )
    return 0


def cmd_set_global(client, args):
    params = {}
    if args.download_limit is not None:
        params["download_limit"] = args.download_limit
    if args.upload_limit is not None:
        params["upload_limit"] = args.upload_limit
    if args.download_minimum is not None:
        params["download_minimum"] = args.download_minimum
    if args.upload_minimum is not None:
        params["upload_minimum"] = args.upload_minimum
    if args.download_priority is not None:
        params["download_priority"] = args.download_priority
    if args.upload_priority is not None:
        params["upload_priority"] = args.upload_priority
    if args.clear_download_limit:
        params["download_limit"] = None
    if args.clear_upload_limit:
        params["upload_limit"] = None
    if not params:
        sys.stderr.write("No change given.\n")
        return 1
    result = client.call("set_global", params)
    print("Set:")
    for key in ("enabled", "download_limit", "upload_limit",
                "download_minimum", "upload_minimum",
                "download_priority", "upload_priority"):
        value = result.get(key)
        if key.endswith("limit") and value is not None:
            value = _fmt_rate(value)
        if value is not None:
            print(f"  {key}: {value}")
    return 0


def cmd_set_process(client, args):
    from .config import format_window

    if not args.exe and not args.name and not args.match:
        sys.stderr.write("Please give --exe, --name or --match.\n")
        return 1
    if args.exe:
        match_type, match_value = "exe", args.exe
    elif args.name:
        match_type, match_value = "name", args.name
    else:
        match_type, match_value = "cmdline", args.match

    has_window_args = bool(args.window_days or args.window_start or args.window_end)
    window = None
    if has_window_args:
        if not (args.window_days and args.window_start and args.window_end):
            sys.stderr.write(
                "A time window needs --window-days, --window-start and "
                "--window-end.\n")
            return 1
        window = {"days": args.window_days, "start": args.window_start,
                  "end": args.window_end}

    params = {
        "name": args.name or args.appname or None,
        "match_type": match_type,
        "match_value": match_value,
        "download_limit": args.download_limit,
        "upload_limit": args.upload_limit,
        "priority": args.priority,
        "recursive": args.recursive,
    }
    if has_window_args or args.clear_window:
        params["window"] = window
    result = client.call("set_process", params)
    print("Rule saved/updated:")
    print(f"  key:   {result.get('key')}")
    print(f"  name:  {result.get('name')}")
    print(f"  match: {result.get('match_type')}:{result.get('match_value')}")
    print(f"  dl:    {_fmt_rate(result.get('download_limit'))}")
    print(f"  ul:    {_fmt_rate(result.get('upload_limit'))}")
    print(f"  prio:  {result.get('priority')}")
    print(f"  window: {format_window(result.get('window')) or '—'}")
    return 0


def cmd_remove(client, args):
    result = client.call("remove_process", {"key": args.key})
    print("Deleted." if result.get("removed") else "Rule not found.")
    return 0 if result.get("removed") else 3


def cmd_toggle(client, args):
    enabled = str(args.enabled).lower() == "true"
    result = client.call("toggle_enabled", {"enabled": enabled})
    print(f"Shaping {'ON' if result.get('enabled') else 'OFF'}")
    return 0


def cmd_profiles(client, args):
    result = client.call("list_profiles")
    active = result.get("active")
    names = result.get("profiles") or []
    if not names:
        print("(no profiles)")
        return 0
    for name in names:
        marker = "*" if name == active else " "
        print(f"{marker} {name}")
    return 0


def cmd_profile_use(client, args):
    client.call("activate_profile", {"name": args.name})
    print(f"Profile active: {args.name}")
    return 0


def cmd_profile_save(client, args):
    result = client.call("set_profile", {
        "name": args.name,
        "activate": not args.no_activate,
    })
    print(f"Profile saved: {result.get('name')} "
          f"(active: {result.get('active')})")
    return 0


def cmd_profile_delete(client, args):
    result = client.call("delete_profile", {"name": args.name})
    if result.get("deleted"):
        print("Profile deleted.")
        return 0
    print("Profile not found.")
    return 3


def cmd_start_profile(client, args):
    """Show, set or remove the startup profile."""
    if args.clear:
        result = client.call("set_start_profile", {})
        print(f"Startup profile removed (now: {result.get('start_profile') or '—'}).")
        return 0
    if args.name:
        result = client.call("set_start_profile", {"name": args.name})
        print(f"Startup profile: {result.get('start_profile')}")
        return 0
    cfg = client.call("get_config")
    print(f"Startup profile: {cfg.get('start_profile') or '—'}")
    return 0


def cmd_budgets(client, args):
    """Show current consumption budgets and usage."""
    result = client.call("get_budgets")
    if not result.get("enabled", True):
        print("Budgets are disabled.")
        return 0
    entries = result.get("entries") or []
    if not entries:
        print("(no budgets configured)")
        print("  throtl-cli budget-set --day 20gb --week 100gb")
        print("  throtl-cli budget-set --app firefox --day 5gb")
        return 0
    for entry in entries:
        scope = "global" if entry.get("scope") == "global" else entry.get("app")
        mark = "⚠" if entry.get("exceeded") else " "
        percent = (entry.get("ratio") or 0.0) * 100
        print(f"{mark} {scope:<22}{entry.get('window'):>5}  "
              f"{_fmt_bytes(entry.get('used')):>12} / "
              f"{_fmt_bytes(entry.get('limit')):>12}  ({percent:.0f}%)")
    return 0


def cmd_budget_set(client, args):
    params = {}
    if args.app:
        params["app"] = args.app
    if args.day is not None:
        params["day"] = args.day
    if args.week is not None:
        params["week"] = args.week
    if args.enable:
        params["enabled"] = True
    if args.disable:
        params["enabled"] = False
    if not params:
        sys.stderr.write("Nothing to set (--day/--week/--enable/--disable).\n")
        return 1
    client.call("set_budget", params)
    target = args.app or "global"
    print(f"Budget saved ({target}).")
    return 0


def cmd_budget_remove(client, args):
    result = client.call("remove_budget", {"app": args.app})
    print("Budget deleted." if result.get("removed") else "Budget not found.")
    return 0 if result.get("removed") else 3


def cmd_stats(client, args):
    result = client.call("get_stats", {"window": args.window})
    apps = result.get("apps") or []
    totals = result.get("totals") or {}
    print(f"Statistics ({result.get('window')}):  "
          f"down={_fmt_bytes(totals.get('download'))}  "
          f"up={_fmt_bytes(totals.get('upload'))}")
    if not apps:
        print("  (no data recorded yet)")
        return 0
    print(f"  {'App':<32}{'Download':>14}{'Upload':>14}")
    for item in apps:
        name = _proc_display(str(item.get("app", "?")), 32)
        print(f"  {name:<32}{_fmt_bytes(item.get('download')):>14}"
              f"{_fmt_bytes(item.get('upload')):>14}")
    return 0


def cmd_export(client, args):
    from . import write_text_atomic
    from .config import dump_config

    text = dump_config(client.call("get_config"))
    if args.output:
        write_text_atomic(args.output, text)
        print(f"Config exported: {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_import(client, args):
    import tomllib

    try:
        with open(args.file, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        sys.stderr.write(f"Error reading {args.file}: {error}\n")
        return 1
    client.call("import_config", {"config": data})
    print(f"Config imported: {args.file}")
    return 0


def cmd_monitor(client, args):
    """Live per-second bandwidth output (from the daemon)."""
    try:
        while True:
            state = client.call("list_processes")
            print(f"--- Interface {state.get('interface')} "
                  f"({'ON' if state.get('enabled') else 'OFF'}) "
                  f"[{(time.strftime('%H:%M:%S'))}] ---", end="\r")
            rows = []
            for proc in state.get("processes", []):
                rows.append(
                    f"{proc.get('name','?'):<30} "
                    f"down={_fmt_rate(proc.get('download')):<14} "
                    f"up={_fmt_rate(proc.get('upload'))}"
                )
            if rows:
                print("\n".join(rows))
            else:
                print("  (no active traffic)")
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


def _term_size(fallback_cols=100, fallback_lines=30):
    import shutil

    try:
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return fallback_cols, fallback_lines


def cmd_top(client, args):
    """Full-screen live ranking of the apps (htop-style). Ctrl-C quits."""
    import os

    if not sys.stdout.isatty():
        sys.stderr.write("top needs a terminal (TTY).\n")
        return 2
    colors = not os.environ.get("NO_COLOR")
    green = "\033[32m" if colors else ""
    orange = "\033[33m" if colors else ""
    dim = "\033[2m" if colors else ""
    bold = "\033[1m" if colors else ""
    reset = "\033[0m" if colors else ""
    sort_key = args.sort
    try:
        interval = max(0.3, float(args.interval))
    except (TypeError, ValueError):
        sys.stderr.write("--interval must be a number.\n")
        return 2

    def sort_value(app):
        if sort_key == "name":
            return str(app.get("name", "")).lower()
        return float(app.get(sort_key, 0.0) or 0.0)

    try:
        while True:
            state = client.call("list_processes")
            apps = list(state.get("apps") or state.get("processes") or [])
            apps.sort(key=sort_value, reverse=(sort_key != "name"))
            cols, lines = _term_size()
            g = state.get("global") or {}
            out = ["\033[H\033[2J"]
            out.append(
                f"{bold}Throtl top{reset}  {state.get('interface')}  "
                f"shaping={'ON' if state.get('enabled') else 'OFF'}  "
                f"↓ {_fmt_rate(g.get('download'))}  ↑ {_fmt_rate(g.get('upload'))}  "
                f"[{time.strftime('%H:%M:%S')}]  "
                f"{dim}sort={sort_key} q/ctrl-c=quit{reset}")
            out.append("")
            out.append(f"{'PID':<9}{'App':<26}{'▼ Download':>16}{'▲ Upload':>16}")
            out.append("-" * min(cols, 70))
            for app in apps[: max(1, lines - 6)]:
                count = int(app.get("pid_count") or 1)
                if count > 1:
                    pid = f"{count} pids"
                else:
                    pids = app.get("pids") or []
                    pid = str(pids[0]) if pids and pids[0] != "-" else "—"
                name = _proc_display(str(app.get("name", "?")), 24)
                down = _fmt_rate(app.get("download"), "auto")
                up = _fmt_rate(app.get("upload"), "auto")
                out.append(f"{pid:<9}{name:<26}"
                           f"{green}{down:>16}{reset}{orange}{up:>16}{reset}")
            if not apps:
                out.append("  (no active traffic)")
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_watch(client, args):
    """Watch the processes for N seconds and print a report at the end.

    Script-friendly: with ``--alert`` the command exits with code 4 when the
    observed peak rate of an app exceeds the threshold.
    """
    from .units import parse_rate

    try:
        duration = max(1.0, float(args.duration))
    except (TypeError, ValueError):
        sys.stderr.write("--duration must be a number.\n")
        return 2
    try:
        interval = max(0.2, float(args.interval))
    except (TypeError, ValueError):
        sys.stderr.write("--interval must be a number.\n")
        return 2
    alert = None
    if args.alert:
        try:
            alert = parse_rate(args.alert)
        except ValueError:
            alert = None
        if not alert:
            sys.stderr.write(f"Invalid --alert value: {args.alert!r}\n")
            return 2
    needle = (args.app or "").lower()

    stats = {}
    state = {}
    deadline = time.monotonic() + duration
    try:
        while True:
            state = client.call("list_processes")
            for app in state.get("apps") or state.get("processes") or []:
                if app.get("unattributed"):
                    continue
                name = str(app.get("name") or "?")
                if needle and needle not in name.lower():
                    continue
                entry = stats.get(name)
                if entry is None:
                    entry = stats[name] = {
                        "name": name, "samples": 0,
                        "down_sum": 0.0, "up_sum": 0.0,
                        "down_peak": 0.0, "up_peak": 0.0,
                    }
                down = float(app.get("download") or 0.0)
                up = float(app.get("upload") or 0.0)
                entry["samples"] += 1
                entry["down_sum"] += down
                entry["up_sum"] += up
                entry["down_peak"] = max(entry["down_peak"], down)
                entry["up_peak"] = max(entry["up_peak"], up)
            if time.monotonic() + interval > deadline:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass

    rows = []
    for entry in stats.values():
        samples = entry["samples"] or 1
        rows.append({
            "name": entry["name"],
            "samples": entry["samples"],
            "download_avg": entry["down_sum"] / samples,
            "upload_avg": entry["up_sum"] / samples,
            "download_peak": entry["down_peak"],
            "upload_peak": entry["up_peak"],
        })
    rows.sort(key=lambda r: r["download_peak"] + r["upload_peak"], reverse=True)
    breaches = [r for r in rows
                if alert and (r["download_peak"] > alert or r["upload_peak"] > alert)]

    if args.json:
        import json as _json

        print(_json.dumps({
            "interface": state.get("interface"),
            "duration": duration,
            "alert": alert,
            "apps": rows,
            "breaches": [r["name"] for r in breaches],
        }, indent=2))
        return 4 if breaches else 0

    print(f"Watch: {duration:g}s @ {interval:g}s — "
          f"interface {state.get('interface', '?')}")
    if not rows:
        print("  (no traffic seen)")
    else:
        print(f"  {'App':<26}{'Samples':>8}{'Avg down':>14}{'Peak down':>14}"
              f"{'Avg up':>14}{'Peak up':>14}")
        for row in rows:
            print(f"  {_proc_display(row['name'], 26):<26}{row['samples']:>8}"
                  f"{_fmt_rate(row['download_avg']):>14}"
                  f"{_fmt_rate(row['download_peak']):>14}"
                  f"{_fmt_rate(row['upload_avg']):>14}"
                  f"{_fmt_rate(row['upload_peak']):>14}")
    if alert is not None:
        if breaches:
            print(f"⚠ ALERT: peak above {_fmt_rate(alert)} — "
                  + ", ".join(r["name"] for r in breaches))
            return 4
        print(f"OK: no app exceeded {_fmt_rate(alert)}.")
    return 0


def _wait_engine(client, timeout: float = 15.0) -> None:
    """Wait until a running engine apply finishes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not client.call("status").get("engine_applying"):
                return
        except Exception:
            return
        time.sleep(0.3)


def _find_app(state, needle):
    for app in state.get("apps") or []:
        if needle in str(app.get("name", "")).lower():
            return app
    return None


def cmd_selftest(client, args):
    """End-to-end proof: a real curl download must go through the limit.

    Deliberately measures the nethogs-reported rate *after* a warmup instead of
    the curl average: TrafficToll installs the tc filters only a few seconds
    after the connection is established, so the unthrottled start would skew the
    average.
    """
    import shutil
    import statistics
    import subprocess

    from .units import parse_rate

    def fail(message, code=1):
        print(f"❌ {message}")
        return code

    status = client.call("status")
    if status.get("simulated"):
        return fail("Simulation mode — the selftest needs the real daemon "
                    "(root + TrafficToll).", 2)
    curl = shutil.which("curl")
    if not curl:
        return fail("curl is not installed.", 2)
    if not status.get("enabled"):
        return fail("Shaping is OFF. First run 'throtl-cli toggle --enabled true'.", 2)
    if not status.get("monitoring"):
        return fail("Monitoring is off — the selftest needs nethogs rates.", 2)

    try:
        limit_kbit = parse_rate(args.limit)
    except ValueError:
        limit_kbit = None
    if not limit_kbit:
        return fail(f"Invalid limit: {args.limit!r}", 2)
    limit_bps = limit_kbit * 1000.0 / 8.0

    def measure_curl(seconds):
        proc = subprocess.run(
            [curl, "-sL", "-o", "/dev/null", "-w", "%{speed_download}",
             "--max-time", str(seconds), args.url],
            capture_output=True, text=True)
        try:
            return float((proc.stdout or "").strip())
        except ValueError:
            return None

    print(f"Throtl selftest — limit {_fmt_rate(limit_kbit)} on {curl}")
    print(f"  URL: {args.url}")
    print("  1) Baseline without limit …")
    base_bps = measure_curl(args.time)
    if base_bps is None:
        return fail("Baseline measurement failed (network/URL?).", 2)

    existing = None
    for rule in client.call("get_config").get("processes", []):
        if rule.get("match_type") == "exe" and "curl" in str(rule.get("match_value", "")):
            existing = rule
            break

    rule = None
    samples = []
    try:
        rule = client.call("set_process", {
            "name": "throtl-selftest", "match_type": "exe",
            "match_value": curl, "download_limit": limit_kbit,
            "priority": "normal",
        })
        _wait_engine(client)
        print(f"  2) Download with limit ({args.warmup}s warmup, then {args.measure}s measurement) …")
        proc = subprocess.Popen(
            [curl, "-sL", "-o", "/dev/null", "--max-time",
             str(int(args.warmup) + int(args.measure)), args.url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(args.warmup)
            for _ in range(int(args.measure)):
                app = _find_app(client.call("list_processes"), "curl")
                if app is not None:
                    samples.append(float(app.get("download", 0.0) or 0.0))
                time.sleep(1.0)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        # Always remove the selftest rule: its key is the full curl path and can
        # differ from an already-existing curl rule. Earlier only
        # set_process(existing) was called, which left the selftest rule behind
        # as a second, permanently active throttle.
        if rule is not None:
            client.call("remove_process", {"key": rule.get("key")})
        if existing is not None:
            client.call("set_process", existing)
        _wait_engine(client)

    limit_kbit_measured = statistics.median(samples) if samples else None
    if limit_kbit_measured is None:
        return fail("No curl rate received from the daemon (nethogs/attribution?). "
                    "Please check 'throtl-cli status'.", 2)

    ratio = (limit_kbit_measured / limit_kbit) if limit_kbit else 0.0
    print(f"  Baseline:  {_fmt_rate(base_bps * 8 / 1000)}  ({base_bps / 1000:.0f} KB/s)")
    print(f"  Limited:   {_fmt_rate(limit_kbit_measured)}  "
          f"(median)  = {ratio:.2f}× limit")

    if base_bps < limit_bps * 1.5:
        print("⚠️  The unthrottled rate is close to the limit — the line is too "
              "slow for a meaningful test.")
        return 2
    if limit_kbit_measured <= limit_kbit * 1.8:
        print("✅ Passed: the download was actually throttled.")
        return 0
    print("❌ Failed: the measured rate is above the limit "
          "(does the rule apply? the right interface?).")
    return 1


def _is_fatal_issue(issue: str) -> bool:
    """Rough classification: missing rights/tools are a real problem, the rest a warning."""
    text = (issue or "").lower()
    return ("not root" in text or "not found" in text
            or "command missing" in text)


def _slow_apply_message(engine) -> str | None:
    """Doctor warning when a tt re-apply takes longer than a second."""
    engine = engine or {}
    last = engine.get("last_apply_seconds")
    avg = engine.get("avg_apply_seconds")
    slow = (isinstance(last, (int, float)) and last > 1.0) or \
           (isinstance(avg, (int, float)) and avg > 1.0)
    if not slow:
        return None
    last_s = f"{last:.2f}s" if isinstance(last, (int, float)) else "?"
    avg_s = f"{avg:.2f}s" if isinstance(avg, (int, float)) else "?"
    return f"engine restarts are slow (last {last_s}, avg {avg_s})"


def _socket_permissions_local(path: str) -> dict:
    """Read the socket permissions from the filesystem without a running daemon."""
    import grp
    import os

    try:
        info = os.stat(path)
    except OSError:
        return {"path": path, "exists": False}
    group = None
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except (KeyError, ImportError):
        pass
    mode = info.st_mode & 0o777
    return {
        "path": path,
        "exists": True,
        "mode": oct(mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "group": group,
        "restricted": mode != 0o666,
    }


def cmd_doctor(args) -> int:
    """Check the environment/preflight + effective socket permissions.

    Deliberately runs even WITHOUT a reachable daemon (local preflight fallback),
    so a broken installation stays diagnosable. Exit code 1 as soon as at least
    one real problem was found.
    """
    from . import SOCKET_PATH
    from .daemon import _resolve_interface, _resolve_tt_command, preflight
    from .protocol import Client

    socket_path = args.socket or SOCKET_PATH
    status = None
    client = None
    try:
        client = Client(socket_path, connect_timeout=1.5)
        client.connect()
        status = client.call("status", timeout=3.0)
    except Exception:
        status = None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    errors = 0
    warnings = 0
    print("Throtl doctor")

    if status is None:
        print("❌ Daemon not reachable — check 'systemctl status throtl'.")
        errors += 1
        interface = _resolve_interface(None)
        issues = preflight(_resolve_tt_command(None), interface)
        socket_info = _socket_permissions_local(socket_path)
    else:
        print(f"✅ Daemon reachable (PID {status.get('pid')}, "
              f"interface {status.get('interface')})")
        issues = status.get("preflight") or []
        socket_info = status.get("socket") or {"path": socket_path, "exists": False}
        if status.get("config_warning"):
            print(f"⚠️  Config: {status['config_warning']}")
            warnings += 1
        if status.get("monitor_error"):
            print(f"⚠️  Monitor: {status['monitor_error']}")
            warnings += 1
        if status.get("engine_error"):
            print(f"⚠️  Engine: {status['engine_error']}")
            warnings += 1
        slow = _slow_apply_message(status.get("engine"))
        if slow:
            print(f"⚠️  {slow}")
            warnings += 1

    for issue in issues:
        if _is_fatal_issue(issue):
            print(f"❌ {issue}")
            errors += 1
        else:
            print(f"⚠️  {issue}")
            warnings += 1

    if socket_info.get("exists"):
        line = (f"Socket {socket_info.get('path')} "
                f"mode={socket_info.get('mode')} "
                f"group={socket_info.get('group')}")
        if socket_info.get("restricted"):
            print(f"✅ {line}")
        else:
            print(f"⚠️  {line} (accessible to all local users)")
            warnings += 1
    else:
        print(f"❌ Socket {socket_info.get('path')} does not exist")
        errors += 1

    # The classic case: socket present, but the session does not know the group.
    hint = socket_access_hint(socket_path)
    if hint:
        print(f"❌ {hint}")
        errors += 1

    if errors == 0 and warnings == 0:
        print("✅ No problems found.")

    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="throtl-cli", description="Throtl-Daemon CLI"
    )
    parser.add_argument("--socket", default=None, help="Unix socket path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show the daemon status")

    sub.add_parser("list-processes", help="show the live process list")

    g = sub.add_parser("set-global", help="set global limits/priorities")
    g.add_argument("--download-limit", "-dl", default=None,
                   help="e.g. 2mbps, 512kbps, 1000000")
    g.add_argument("--upload-limit", "-ul", default=None)
    g.add_argument("--download-minimum", default=None)
    g.add_argument("--upload-minimum", default=None)
    g.add_argument("--download-priority", default=None,
                   choices=["kritisch", "hoch", "normal", "niedrig"])
    g.add_argument("--upload-priority", default=None,
                   choices=["kritisch", "hoch", "normal", "niedrig"])
    g.add_argument("--clear-download-limit", action="store_true")
    g.add_argument("--clear-upload-limit", action="store_true")

    g2 = sub.add_parser("set-process",
                        help="set/update a process rule")
    g2.add_argument("--name", default=None, help="process/rule name")
    g2.add_argument("--appname", default=None,
                    help="display name, if different from --exe")
    g2.add_argument("--exe", default=None, help="exe path (regex-escaped)")
    g2.add_argument("--match", default=None, help="cmdline regex")
    g2.add_argument("--download-limit", default=None)
    g2.add_argument("--upload-limit", default=None)
    g2.add_argument("--priority", default="normal",
                    choices=["kritisch", "hoch", "normal", "niedrig"])
    g2.add_argument("--recursive", action="store_true")
    g2.add_argument("--window-days", default=None,
                    help="time-window weekdays, e.g. 'mo-fr' or 'sa,so'")
    g2.add_argument("--window-start", default=None,
                    help="time-window start as HH:MM")
    g2.add_argument("--window-end", default=None,
                    help="time-window end as HH:MM (before start = across midnight)")
    g2.add_argument("--clear-window", action="store_true",
                    help="remove the rule's time window")

    r = sub.add_parser("remove-process", help="delete a rule")
    r.add_argument("--key", required=True)

    t = sub.add_parser("toggle", help="toggle shaping globally on/off")
    t.add_argument("--enabled", choices=["true", "false"], default="true")

    sub.add_parser("monitor", help="live bandwidth per second")

    tp = sub.add_parser("top", help="full-screen live ranking (htop-style)")
    tp.add_argument("--interval", default="1.0",
                    help="refresh interval in seconds (default: 1.0)")
    tp.add_argument("--sort", choices=["download", "upload", "name"],
                    default="download", help="sort column (default: download)")

    w = sub.add_parser("watch",
                       help="watch processes for N seconds and print a report")
    w.add_argument("--duration", "-d", default=10.0,
                   help="watch duration in seconds (default: 10)")
    w.add_argument("--interval", "-i", default=1.0,
                   help="sampling interval in seconds (default: 1)")
    w.add_argument("--app", default=None,
                   help="only apps with this name (substring, optional)")
    w.add_argument("--alert", default=None,
                   help="threshold (e.g. 5mbps); exit code 4 on breach")
    w.add_argument("--json", action="store_true",
                   help="print the report as JSON")

    sft = sub.add_parser("selftest",
                         help="end-to-end check whether limits really apply")
    sft.add_argument("--limit", default="2mbps",
                     help="test limit for the curl download (default: 2mbps)")
    sft.add_argument("--url", default="https://speed.cloudflare.com/__down?bytes=100000000",
                     help="download URL for the test")
    sft.add_argument("--time", type=float, default=6.0,
                     help="seconds for the baseline measurement (default: 6)")
    sft.add_argument("--warmup", type=float, default=4.0,
                     help="seconds warmup before measuring (default: 4)")
    sft.add_argument("--measure", type=float, default=5.0,
                     help="seconds measurement window (default: 5)")

    sub.add_parser("profiles", help="list profiles")

    pu = sub.add_parser("profile-use", help="activate a profile")
    pu.add_argument("name")

    ps = sub.add_parser("profile-save",
                        help="save the current settings as a profile")
    ps.add_argument("name")
    ps.add_argument("--no-activate", action="store_true",
                    help="save the profile but do not activate it")

    pd = sub.add_parser("profile-delete", help="delete a profile")
    pd.add_argument("name")

    sp = sub.add_parser("start-profile",
                        help="activate a profile at daemon start (show/set/remove)")
    sp.add_argument("name", nargs="?", default=None, help="profile name")
    sp.add_argument("--clear", action="store_true", help="remove the startup profile")

    st = sub.add_parser("stats", help="show bandwidth statistics")
    st.add_argument("--window", choices=["minute", "hour", "day"],
                    default="minute", help="time window (default: minute)")

    sub.add_parser("budgets", help="show consumption budgets and usage")

    bs = sub.add_parser("budget-set",
                        help="set a budget (global or per app)")
    bs.add_argument("--app", default=None, help="app name (default: global)")
    bs.add_argument("--day", default=None,
                    help="last-24h budget, e.g. 20gb")
    bs.add_argument("--week", default=None,
                    help="last-7-days budget, e.g. 100gb")
    bs.add_argument("--enable", action="store_true", help="enable budgets")
    bs.add_argument("--disable", action="store_true", help="disable budgets")

    br = sub.add_parser("budget-remove", help="delete an app budget")
    br.add_argument("--app", required=True)

    ex = sub.add_parser("export", help="print the config as TOML")
    ex.add_argument("--output", "-o", default=None,
                    help="target file (default: stdout)")

    im = sub.add_parser("import", help="apply a config from a TOML file")
    im.add_argument("file")

    sub.add_parser("doctor", help="check environment, preflight and socket permissions")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # doctor must work even without a running daemon (otherwise a missing
    # installation could never be diagnosed).
    if args.command == "doctor":
        return cmd_doctor(args)
    client = _client(args)
    try:
        handlers = {
            "status": cmd_status,
            "list-processes": cmd_list,
            "set-global": cmd_set_global,
            "set-process": cmd_set_process,
            "remove-process": cmd_remove,
            "toggle": cmd_toggle,
            "monitor": cmd_monitor,
            "top": cmd_top,
            "watch": cmd_watch,
            "selftest": cmd_selftest,
            "profiles": cmd_profiles,
            "profile-use": cmd_profile_use,
            "profile-save": cmd_profile_save,
            "profile-delete": cmd_profile_delete,
            "start-profile": cmd_start_profile,
            "stats": cmd_stats,
            "budgets": cmd_budgets,
            "budget-set": cmd_budget_set,
            "budget-remove": cmd_budget_remove,
            "export": cmd_export,
            "import": cmd_import,
        }
        return handlers[args.command](client, args)
    except RpcError as error:
        sys.stderr.write(f"RPC error ({error.code}): {error.message}\n")
        return 1
    except TimeoutError_:
        sys.stderr.write("Timeout: the daemon is not responding.\n")
        return 1
    except ConnectionError as error:
        # The daemon dropped away during a long command (monitor/top/watch):
        # report cleanly instead of a traceback.
        sys.stderr.write(f"connection to the daemon lost: {error}\n")
        return 1
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
