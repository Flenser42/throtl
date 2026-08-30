"""Throtl-Daemon: Unix-Socket-Server, TrafficToll-Engine-Steuerung, Monitoring.

Architektur:
    [GTK4-Frontend / CLI] --Unix-Socket (JSON)-- [Daemon]
        ├── TrafficTollEngine  -> tt-Subprozess (tc + cgroups, root)
        ├── NethogsMonitor     -> Live-Bandbreiten pro Prozess
        └── ConfigStore        -> ~/.config/netlimiter-clone/config.toml

IPC-Nachrichten: siehe throtl.protocol (Request/Response/Event).

Methoden des Daemons:
    status                      -> Daemon-/Engine-/Monitor-Status
    get_config / get_state      -> volle bzw. zusammengefasste Config
    set_global {key:value}      -> globale Limits/Prioritaeten anpassen
    set_process {...}           -> Regel erstellen/aktualisieren (per key)
    remove_process {key}        -> Regel loeschen
    toggle_enabled {enabled}    -> globales Shaping an/aus
    set_unit {unit}             -> Anzeige-Einheit der GUI (kbps|kBs)
    list_processes []           -> Live-Stats-Schnappschuss

Events:
    stats  (nach jedem Monitoring-Tick; enthaelt Prozess-Stats + Regeln)
"""

import argparse
import atexit
import os
import socket
import threading
import time

from . import SOCKET_PATH, __version__
from .config import (
    config_dir_default,
    config_path_for,
    default_config,
    detect_default_interface,
    load_config,
    make_rule,
    priority_to_name,
    save_config,
)
from .engine import SimEngine, TrafficTollEngine
from .monitor import NethogsMonitor
from .protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    iter_messages,
    make_error,
    send_message,
)
from .units import parse_rate  # noqa: F401  (re-export, CLI/Protokoll-Kompatibilitaet)


class ConfigStore:
    """Lädt/hält/persistiert die Config in TOML."""

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        self.path = config_path_for(config_dir)
        self._config = load_config(self.path)

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
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"unbekannter globaler Schluessel {key!r}")
        if "enabled" in changes:
            g["enabled"] = bool(changes["enabled"])
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
        if unit not in ("kbps", "kBs"):
            raise ValueError("unit muss 'kbps' oder 'kBs' sein")
        self._config["unit"] = unit
        self._persist()
        return unit


def parse_limit_param(value):
    """Param fuer ein Limit akzeptieren: None->unbegrenzt, Zahl, oder Rate-String."""
    if value is None or value in ("", "null", "none", "unbegrenzt", "unlimited"):
        return None
    from .units import parse_rate as pr

    return pr(value)


class Daemon:
    def __init__(self, socket_path: str = SOCKET_PATH, config_dir: str = None,
                 engine=None, monitor_factory=None, interval: float = 1.0,
                 tt_command: str = "tt"):
        self.socket_path = socket_path
        self.config_dir = config_dir or config_dir_default()
        self.store = ConfigStore(self.config_dir)
        self._state_lock = threading.RLock()
        self.interval = interval

        cfg = self.store.get()
        interface = cfg.get("interface") or detect_default_interface() or "lo"
        self.interface = interface

        # Engine waehlen: explizit (Tests) oder TrafficTollEngine (tt via venv)
        self.engine = engine
        if self.engine is None:
            self.engine = TrafficTollEngine(interface, command=tt_command)

        self.monitor = None
        self._monitor_factory = monitor_factory or (lambda dev, i: NethogsMonitor(dev, interval=i))

        self._clients = set()
        self._server = None
        self._running = True
        self._monitor_thread = None
        self._last_snapshot = {}
        self._ruleset_generation = 0
        self._monitor_enabled = True

        atexit.register(self.shutdown)

    # --- Lifecycle ---

    def start(self) -> None:
        self._apply_engine()
        self._start_monitor()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError as error:
            print(f"Warnung: Socket-Vorbereitung: {error}")
        self._server.bind(self.socket_path)
        self._server.listen(8)
        self._server.settimeout(0.25)
        # Als root erzeugt der Socket-Pfad rw-------. Ohne Schreibrecht auf der
        # Socket-Datei schlaegt der Unix-Connect des User-Prozesses (GUI/CLI)
        # mit "Permission denied" fehl. Wir setzen 0666; Zugriff bleibt trotzdem
        # rein lokal (kein Netzwerk-Port).
        try:
            os.chmod(self.socket_path, 0o666)
        except OSError as error:
            print(f"Warnung: Socket-Rechte nicht setzbar: {error}")
        # Monitoring-Ticker starten
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="throtl-monitor-ticker"
        )
        self._monitor_thread.start()
        print(f"Throtl-Daemon {__version__} läuft auf {self.socket_path}")

    def _monitor_loop(self) -> None:
        while self._running:
            self._tick_monitor()
            time.sleep(self.interval)

    def _tick_monitor(self) -> None:
        self._last_snapshot = self._collect_snapshot()

    def _collect_snapshot(self) -> dict:
        """Prozess-Stats + angewendete Regeln zusammenfassen."""
        raw = {}
        if self.monitor is not None:
            try:
                raw = self.monitor.snapshot()
            except Exception:
                raw = {}
        cfg = self.store.get()
        rules = cfg.get("processes", [])
        processes = []
        for pid, info in raw.items():
            matches = _match_rules(rules, info.get("name"), info.get("pid", pid))
            processes.append({
                "pid": pid,
                "name": info.get("name", "?"),
                "download": round(info.get("download", 0.0), 3),
                "upload": round(info.get("upload", 0.0), 3),
                # via Regeln: limits/prioritaet fuer die Anzeige
                "rule_name": matches.get("name"),
            })
        # Regel-Informationen separat liefern, damit die GUI sie zuordnen kann
        return {
            "interface": self.interface,
            "enabled": cfg["global"].get("enabled", True),
            "processes": processes,
            "rules": rules,  # fuer GUI: union von Regel + Live-Stats
            "monitored": self.monitor is not None,
        }

    # --- Engine ---

    def _apply_engine(self) -> None:
        cfg = self.store.get()
        try:
            self.engine.apply(cfg)
        except Exception as error:
            print(f"Engine-Fehler: {error}")

    def _start_monitor(self) -> None:
        if self.monitor is None:
            self.monitor = self._monitor_factory(self.interface, 1.0)
            try:
                self.monitor.start()
            except Exception as error:
                print(f"Monitor-Fehler: {error}")
                self.monitor = None

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
            for message in iter_messages(conn):
                if message is None:
                    break
                response = self._dispatch(message)
                if response is not None:
                    try:
                        send_message(conn, response)
                    except OSError:
                        break

    def _dispatch(self, message: dict) -> dict:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None or not method:
            return {"id": message_id, "error": make_error(INVALID_PARAMS, "id/method fehlt")}
        handler = self._handlers().get(method)
        if handler is None:
            return {"id": message_id, "error": make_error(METHOD_NOT_FOUND, f"unbekannte Methode: {method}")}
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
        }

    # --- Handler ---

    def _h_status(self, params):
        engine_status = None
        try:
            engine_status = self.engine.status() if self.engine else None
        except Exception:
            engine_status = None
        cfg = self.store.get()
        return {
            "daemon": __version__,
            "pid": os.getpid(),
            "interface": self.interface,
            "enabled": cfg["global"].get("enabled", True),
            "monitoring": self.monitor is not None,
            "engine": engine_status,
            "simulated": getattr(self.engine, "simulated", False),
        }

    def _h_get_config(self, params):
        return self.store.get()

    def _h_get_state(self, params):
        return self._collect_snapshot()

    def _h_set_global(self, params):
        store = self.store
        changes = dict(params)
        with self._state_lock:
            store.update_global(**changes)
            self._apply_engine()
        self._emit_rules_changed()
        return store.get()["global"]

    def _h_set_process(self, params):
        name = str(params.get("name", ""))
        match_type = str(params.get("match_type", "exe"))
        match_value = str(params.get("match_value", ""))
        if not match_value:
            raise ValueError("match_value fehlt")
        rule = make_rule(
            name=name,
            match_type=match_type,
            match_value=match_value,
            download_limit=parse_limit_param(params.get("download_limit")),
            upload_limit=parse_limit_param(params.get("upload_limit")),
            priority=str(params.get("priority") or "normal"),
            recursive=bool(params.get("recursive", False)),
            key=params.get("key"),
        )
        with self._state_lock:
            self.store.upsert_process(rule)
            self._apply_engine()
        self._emit_rules_changed()
        return rule

    def _h_remove_process(self, params):
        key = str(params.get("key", ""))
        if not key:
            raise ValueError("key fehlt")
        with self._state_lock:
            removed = self.store.remove_process(key)
            self._apply_engine()
        self._emit_rules_changed()
        return {"removed": removed}

    def _h_toggle_enabled(self, params):
        raw = params.get("enabled")
        if isinstance(raw, str):
            enabled = raw.strip().lower() in ("1", "true", "on", "ja", "an")
        else:
            enabled = bool(raw)
        with self._state_lock:
            self.store.update_global(enabled=enabled)
            self._apply_engine()
        self._emit_rules_changed()
        return {"enabled": enabled}

    def _h_set_unit(self, params):
        unit = str(params.get("unit", "kbps"))
        return {"unit": self.store.set_unit(unit)}

    def _h_list_processes(self, params):
        return self._collect_snapshot()

    def _emit_rules_changed(self) -> None:
        state = self._collect_snapshot()
        self._tick_monitor()

    # --- Shutdown ---

    def shutdown(self) -> None:
        self._running = False
        self._stop_monitor()
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
    """Erste passende Regel fuer einen nethogs-Prozessnamen/PID finden.

    nethogs liefert als "name" entweder den vollen exe-Pfad oder den
    Prozessnamen. Wir treffen eine Anzeige-Zuordnung: die Regel passt, wenn
    ihr ``name`` oder ihr literal aufbereiteter ``match_value`` mit dem
    nethogs-Namen uebereinstimmt oder in ihm endet.
    """
    if not name:
        return {}
    for rule in rules:
        if rule.get("name") and rule["name"] == name:
            return rule
        mv = rule.get("match_value")
        if mv and mv == name:
            return rule
        if mv and (name.startswith(mv) or name.endswith(mv.rstrip("/"))):
            return rule
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Throtl-Daemon")
    parser.add_argument("--foreground", action="store_true",
                        help="im Vordergrund laufen (fpr systemd/Testing)")
    parser.add_argument("--socket", default=SOCKET_PATH, help="Unix-Socket-Pfad")
    parser.add_argument("--config-dir", default=None,
                        help="Config-Verzeichnis (Default: ~/.config/netlimiter-clone)")
    parser.add_argument("--interface", default=None,
                        help="Netzwerk-Interface (Default: auto)")
    parser.add_argument("--simulate", action="store_true",
                        help="Tooling-Demo: verwende SimEngine (kein Root/noch kein tt)")
    parser.add_argument("--tt-command", default="tt",
                        help="Pfad zum tt-Binary (Default: tt aus venv)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.config_dir is None:
        cfg_dir = config_dir_default()
    else:
        cfg_dir = args.config_dir
    if args.interface:
        # Interface ueberschreiben: in Config persistieren
        store = ConfigStore(cfg_dir)
        if store.get().get("interface") != args.interface:
            store.get()["interface"] = args.interface
            store._persist()

    daemon = Daemon(
        socket_path=args.socket,
        config_dir=cfg_dir,
        interval=1.0,
        tt_command=args.tt_command,
    )
    if args.interface:
        daemon.interface = args.interface
    if args.simulate:
        interface = args.interface or daemon.engine.device
        daemon.engine = SimEngine(device=interface)
    daemon.start()
    try:
        daemon.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
