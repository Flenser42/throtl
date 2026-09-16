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
    throtl-cli autocap            # Demo/Test: curl + dd in die Clouds
"""

import argparse
import sys
import time

from . import SOCKET_PATH, __version__
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


def _priority_int(name):
    from .config import priority_to_int

    return priority_to_int(name)


def cmd_status(client, args):
    status = client.call("status")
    print(f"Throtl-Daemon {status.get('daemon')} (PID {status.get('pid')})")
    print(f"  Interface:   {status.get('interface')}")
    print(f"  Shaping:     {'ON' if status.get('enabled') else 'OFF'}")
    print(f"  Monitoring:  {'yes' if status.get('monitoring') else 'no'}")
    engine = status.get("engine") or {}
    print(f"  Engine:      running={engine.get('running')} "
          f"generation={engine.get('generation')}")
    if status.get("engine_error"):
        print(f"  Engine error: {status['engine_error']}")
    if engine.get("last_error"):
        print(f"  Engine last error: {engine['last_error']}")
    if status.get("monitor_error"):
        print(f"  Monitor error: {status['monitor_error']}")
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


def cmd_autocap(client, args):
    """Demo-Workflow (Test, ob Limits greifen):

    Erstellt eine Regel fuer 'iperf3' oder ruft sie ab, startet einen Uebertrag
    und zeigt die gemessene Bandbreite. In echtem Setup mit root.
    """
    print("Demo: autocap is only a smoke test for now.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="throtl-cli", description="Throtl-Daemon CLI"
    )
    parser.add_argument("--socket", default=None, help="Unix-Socket-Pfad")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Daemon-Status anzeigen")

    sp = sub.add_parser("list-processes", help="Live-Prozess-Liste anzeigen")

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

    sub.add_parser("autocap", help="Demo: Limits greifen testen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
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
            "autocap": cmd_autocap,
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
