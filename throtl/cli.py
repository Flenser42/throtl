"""Throtl-CLI: Daemon ohne GUI steuern und testen.

Beispiele:
    throtl-cli status
    throtl-cli list-processes
    throtl-cli set-global --download-limit 2mbps --upload-limit 1mbps
    throtl-cli set-global --download-priority hoch
    throtl-cli set-process --name Firefox --exe /usr/lib/firefox/firefox \\
        --download-limit 200kbps --priority normal
    throtl-cli remove-process --key 'exe:/usr/lib/firefox/firefox'
    throtl-cli toggle --enabled false
    throtl-cli monitor           # Live-Ausgabe pro Sekunde
    throtl-cli top               # Vollbild-Ranking (htop-Stil)
    throtl-cli selftest          # prueft end-to-end, ob Limits greifen
    throtl-cli profiles          # Profile verwalten
    throtl-cli stats --window day
    throtl-cli export --output throtl.toml
    throtl-cli doctor
"""

import argparse
import sys
import time

from . import SOCKET_PATH
from .protocol import Client, RpcError, TimeoutError_


def _client(args) -> Client:
    client = Client(args.socket or SOCKET_PATH)
    try:
        client.connect()
    except ConnectionError as error:
        sys.stderr.write(f"Fehler: {error}\n")
        sys.stderr.write("Laeuft der Daemon? (bin/throtl-daemon --foreground)\n")
        sys.exit(2)
    return client


def _fmt_rate(value, unit="auto"):
    from .units import format_rate

    return format_rate(value, unit)


def _fmt_bytes(value) -> str:
    """Byte-Volumen menschenlesbar formatieren (SI, 1000er-Schritte)."""
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
              f"(latest tt crash; siehe stderr/unten)")
    stderr = engine.get("stderr_tail") or []
    if stderr:
        print("  Engine stderr (letzte Zeilen):")
        for line in stderr[-3:]:
            print(f"    | {line}")
    issues = status.get("preflight") or []
    if issues:
        print("  Preflight-Warnungen:")
        for issue in issues:
            print(f"    ! {issue}")
    if status.get("simulated"):
        print("  (Simulationsmodus: keine echten tc-Auflagen)")
    return 0


def _proc_display(name: str, limit: int = 32) -> str:
    """nethogs nennt die ganze Kommandozeile -> fuer die Anzeige kuerzen."""
    first = (name or "?").split()
    base = (first[0] if first else name).strip().strip('"').strip("'")
    base = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base if len(base) <= limit else base[: limit - 1] + "…"


def cmd_list(client, args):
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
        sys.stderr.write("Keine Aenderung angegeben.\n")
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
    if not args.exe and not args.name and not args.match:
        sys.stderr.write("Bitte --exe, --name oder --match angeben.\n")
        return 1
    if args.exe:
        match_type, match_value = "exe", args.exe
    elif args.name:
        match_type, match_value = "name", args.name
    else:
        match_type, match_value = "cmdline", args.match

    params = {
        "name": args.name or args.appname or None,
        "match_type": match_type,
        "match_value": match_value,
        "download_limit": args.download_limit,
        "upload_limit": args.upload_limit,
        "priority": args.priority,
        "recursive": args.recursive,
    }
    result = client.call("set_process", params)
    print("Rule saved/updated:")
    print(f"  key:   {result.get('key')}")
    print(f"  name:  {result.get('name')}")
    print(f"  match: {result.get('match_type')}:{result.get('match_value')}")
    print(f"  dl:    {_fmt_rate(result.get('download_limit'))}")
    print(f"  ul:    {_fmt_rate(result.get('upload_limit'))}")
    print(f"  prio:  {result.get('priority')}")
    return 0


def cmd_remove(client, args):
    result = client.call("remove_process", {"key": args.key})
    print("Deleted." if result.get("removed") else "Rule not found.")
    return 0 if result.get("removed") else 3


def cmd_toggle(client, args):
    enabled = str(args.enabled).lower() == "true"
    result = client.call("toggle_enabled", {"enabled": enabled})
    print(f"Shaping {'AN' if result.get('enabled') else 'AUS'}")
    return 0


def cmd_profiles(client, args):
    result = client.call("list_profiles")
    active = result.get("active")
    names = result.get("profiles") or []
    if not names:
        print("(keine Profile)")
        return 0
    for name in names:
        marker = "*" if name == active else " "
        print(f"{marker} {name}")
    return 0


def cmd_profile_use(client, args):
    client.call("activate_profile", {"name": args.name})
    print(f"Profil aktiv: {args.name}")
    return 0


def cmd_profile_save(client, args):
    result = client.call("set_profile", {
        "name": args.name,
        "activate": not args.no_activate,
    })
    print(f"Profil gespeichert: {result.get('name')} "
          f"(aktiv: {result.get('active')})")
    return 0


def cmd_profile_delete(client, args):
    result = client.call("delete_profile", {"name": args.name})
    if result.get("deleted"):
        print("Profil geloescht.")
        return 0
    print("Profil nicht gefunden.")
    return 3


def cmd_stats(client, args):
    result = client.call("get_stats", {"window": args.window})
    apps = result.get("apps") or []
    totals = result.get("totals") or {}
    print(f"Statistik ({result.get('window')}):  "
          f"runter={_fmt_bytes(totals.get('download'))}  "
          f"rauf={_fmt_bytes(totals.get('upload'))}")
    if not apps:
        print("  (noch keine Daten aufgezeichnet)")
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
        print(f"Config exportiert: {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_import(client, args):
    import tomllib

    try:
        with open(args.file, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        sys.stderr.write(f"Fehler beim Lesen von {args.file}: {error}\n")
        return 1
    client.call("import_config", {"config": data})
    print(f"Config importiert: {args.file}")
    return 0


def cmd_monitor(client, args):
    """Live-Ausgabe der Bandbreiten pro Sekunde (aus dem Daemon)."""
    try:
        while True:
            state = client.call("list_processes")
            print(f"--- Interface {state.get('interface')} "
                  f"({'AN' if state.get('enabled') else 'AUS'}) "
                  f"[{(time.strftime('%H:%M:%S'))}] ---", end="\r")
            rows = []
            for proc in state.get("processes", []):
                rows.append(
                    f"{proc.get('name','?'):<30} "
                    f"runter={_fmt_rate(proc.get('download')):<14} "
                    f"rauf={_fmt_rate(proc.get('upload'))}"
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
    """Vollbild-Live-Ranking der Apps (htop-Stil). Ctrl-C beendet."""
    import os

    if not sys.stdout.isatty():
        sys.stderr.write("top braucht ein Terminal (TTY).\n")
        return 2
    colors = not os.environ.get("NO_COLOR")
    green = "\033[32m" if colors else ""
    orange = "\033[33m" if colors else ""
    dim = "\033[2m" if colors else ""
    bold = "\033[1m" if colors else ""
    reset = "\033[0m" if colors else ""
    sort_key = args.sort
    interval = max(0.3, float(args.interval))

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


def _wait_engine(client, timeout: float = 15.0) -> None:
    """Warten, bis ein laufender Engine-Apply fertig ist."""
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
    """End-to-End-Beweis: ein echter curl-Download muss durch das Limit gehen.

    Misst bewusst die von nethogs gemeldete Rate *nach* einem Warmup, statt den
    curl-Durchschnitt: TrafficToll setzt die tc-Filter erst ein paar Sekunden
    nach Verbindungsaufbau, der ungedrosselte Start wuerde den Schnitt sonst
    verfaelschen.
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
        return fail("Simulationsmodus — der Selftest braucht den echten Daemon "
                    "(root + TrafficToll).", 2)
    curl = shutil.which("curl")
    if not curl:
        return fail("curl ist nicht installiert.", 2)
    if not status.get("enabled"):
        return fail("Shaping ist AUS. Erst 'throtl-cli toggle --enabled true'.", 2)
    if not status.get("monitoring"):
        return fail("Monitoring ist aus — der Selftest braucht nethogs-Raten.", 2)

    limit_kbit = parse_rate(args.limit)
    if not limit_kbit:
        return fail(f"Ungueltiges Limit: {args.limit!r}", 2)
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

    print(f"Throtl-Selftest — Limit {_fmt_rate(limit_kbit)} auf {curl}")
    print(f"  URL: {args.url}")
    print("  1) Basiswert ohne Limit …")
    base_bps = measure_curl(args.time)
    if base_bps is None:
        return fail("Basismessung fehlgeschlagen (Netzwerk/URL?).", 2)

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
        print(f"  2) Download mit Limit ({args.warmup}s Warmup, dann {args.measure}s messen) …")
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
        if rule is not None:
            if existing is not None:
                client.call("set_process", existing)
            else:
                client.call("remove_process", {"key": rule.get("key")})
        _wait_engine(client)

    limit_kbit_measured = statistics.median(samples) if samples else None
    if limit_kbit_measured is None:
        return fail("Keine curl-Rate vom Daemon erhalten (nethogs/Attribution?). "
                    "Bitte 'throtl-cli status' pruefen.", 2)

    ratio = (limit_kbit_measured / limit_kbit) if limit_kbit else 0.0
    print(f"  Basis:     {_fmt_rate(base_bps * 8 / 1000)}  ({base_bps / 1000:.0f} KB/s)")
    print(f"  Limitiert: {_fmt_rate(limit_kbit_measured)}  "
          f"(median)  = {ratio:.2f}× Limit")

    if base_bps < limit_bps * 1.5:
        print("⚠️  Die ungedrosselte Rate liegt nah am Limit — die Leitung ist zu "
              "langsam fuer einen aussagekraeftigen Test.")
        return 2
    if limit_kbit_measured <= limit_kbit * 1.8:
        print("✅ Bestanden: der Download wurde tatsaechlich gedrosselt.")
        return 0
    print("❌ Fehlgeschlagen: gemessene Rate liegt ueber dem Limit "
          "(greift die Regel? richtiges Interface?).")
    return 1


def _is_fatal_issue(issue: str) -> bool:
    """Grobe Einordnung: fehlende Rechte/Tools sind echtes Problem, Rest Warnung."""
    text = (issue or "").lower()
    return ("nicht als root" in text or "nicht gefunden" in text
            or "kommando fehlt" in text)


def _socket_permissions_local(path: str) -> dict:
    """Socket-Rechte ohne laufenden Daemon aus dem Dateisystem lesen."""
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
    """Umgebung/Preflight + effektive Socket-Rechte pruefen.

    Laeuft bewusst AUCH ohne erreichbaren Daemon (lokaler Preflight-Fallback),
    damit eine kaputte Installation diagnostizierbar bleibt. Exit-Code 1,
    sobald mindestens ein echtes Problem gefunden wurde.
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
        print("❌ Daemon nicht erreichbar — 'systemctl status throtl' pruefen.")
        errors += 1
        interface = _resolve_interface(None)
        issues = preflight(_resolve_tt_command(None), interface)
        socket_info = _socket_permissions_local(socket_path)
    else:
        print(f"✅ Daemon erreichbar (PID {status.get('pid')}, "
              f"Interface {status.get('interface')})")
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
            print(f"⚠️  {line} (fuer alle lokalen Nutzer zugaenglich)")
            warnings += 1
    else:
        print(f"❌ Socket {socket_info.get('path')} existiert nicht")
        errors += 1

    if errors == 0 and warnings == 0:
        print("✅ Keine Probleme gefunden.")

    print(f"\n{errors} Fehler, {warnings} Warnungen")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="throtl-cli", description="Throtl-Daemon CLI"
    )
    parser.add_argument("--socket", default=None, help="Unix-Socket-Pfad")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Daemon-Status anzeigen")

    sub.add_parser("list-processes", help="Live-Prozess-Liste anzeigen")

    g = sub.add_parser("set-global", help="Globale Limits/Prioritaeten setzen")
    g.add_argument("--download-limit", "-dl", default=None,
                   help="z.B. 2mbps, 512kbps, 1000000")
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
                        help="Prozess-Regel setzen/aktualisieren")
    g2.add_argument("--name", default=None, help="Prozessname/Regelname")
    g2.add_argument("--appname", default=None,
                    help="Anzeigename, falls von --exe abweichend")
    g2.add_argument("--exe", default=None, help="exe-Pfad (regex-escaped)")
    g2.add_argument("--match", default=None, help="cmdline-Regex")
    g2.add_argument("--download-limit", default=None)
    g2.add_argument("--upload-limit", default=None)
    g2.add_argument("--priority", default="normal",
                    choices=["kritisch", "hoch", "normal", "niedrig"])
    g2.add_argument("--recursive", action="store_true")

    r = sub.add_parser("remove-process", help="Regel loeschen")
    r.add_argument("--key", required=True)

    t = sub.add_parser("toggle", help="Shaping global an/aus")
    t.add_argument("--enabled", choices=["true", "false"], default="true")

    sub.add_parser("monitor", help="Live-Bandbreiten pro Sekunde")

    tp = sub.add_parser("top", help="Vollbild-Live-Ranking (htop-Stil)")
    tp.add_argument("--interval", default="1.0",
                    help="Aktualisierungsintervall in Sekunden (Default: 1.0)")
    tp.add_argument("--sort", choices=["download", "upload", "name"],
                    default="download", help="Sortierspalte (Default: download)")

    sft = sub.add_parser("selftest",
                         help="End-to-End pruefen, ob Limits wirklich greifen")
    sft.add_argument("--limit", default="2mbps",
                     help="Testlimit fuer den curl-Download (Default: 2mbps)")
    sft.add_argument("--url", default="https://speed.cloudflare.com/__down?bytes=100000000",
                     help="Download-URL fuer den Test")
    sft.add_argument("--time", type=float, default=6.0,
                     help="Sekunden fuer die Basismessung (Default: 6)")
    sft.add_argument("--warmup", type=float, default=4.0,
                     help="Sekunden Warmup vor der Messung (Default: 4)")
    sft.add_argument("--measure", type=float, default=5.0,
                     help="Sekunden Messfenster (Default: 5)")

    sub.add_parser("profiles", help="Profile auflisten")

    pu = sub.add_parser("profile-use", help="Profil aktivieren")
    pu.add_argument("name")

    ps = sub.add_parser("profile-save",
                        help="Aktuelle Einstellungen als Profil speichern")
    ps.add_argument("name")
    ps.add_argument("--no-activate", action="store_true",
                    help="Profil speichern, aber nicht aktivieren")

    pd = sub.add_parser("profile-delete", help="Profil loeschen")
    pd.add_argument("name")

    st = sub.add_parser("stats", help="Bandbreiten-Statistik anzeigen")
    st.add_argument("--window", choices=["minute", "hour", "day"],
                    default="minute", help="Zeitfenster (Default: minute)")

    ex = sub.add_parser("export", help="Config als TOML ausgeben")
    ex.add_argument("--output", "-o", default=None,
                    help="Zieldatei (Default: stdout)")

    im = sub.add_parser("import", help="Config aus TOML-Datei anwenden")
    im.add_argument("file")

    sub.add_parser("doctor", help="Umgebung, Preflight und Socket-Rechte pruefen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # doctor muss auch ohne laufenden Daemon funktionieren (sonst koennte man
    # eine fehlende Installation nie diagnostizieren).
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
            "selftest": cmd_selftest,
            "profiles": cmd_profiles,
            "profile-use": cmd_profile_use,
            "profile-save": cmd_profile_save,
            "profile-delete": cmd_profile_delete,
            "stats": cmd_stats,
            "export": cmd_export,
            "import": cmd_import,
        }
        return handlers[args.command](client, args)
    except RpcError as error:
        sys.stderr.write(f"RPC-Fehler ({error.code}): {error.message}\n")
        return 1
    except TimeoutError_:
        sys.stderr.write("Timeout: Daemon reagiert nicht.\n")
        return 1
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
