"""Throtl-Daemon: Unix-Socket-Server, TrafficToll-Engine-Steuerung, Monitoring.

Architektur:
    [GTK4-Frontend / CLI] --Unix-Socket (JSON)-- [Daemon]
        ├── TrafficTollEngine  -> tt-Subprozess (tc + cgroups, root)
        ├── NethogsMonitor     -> Live-Bandbreiten pro Prozess
        └── ConfigStore        -> ~/.config/throtl/config.toml

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
import copy
import os
import signal
import socket
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
    pretty_app_name,
)
from .protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    iter_messages,
    make_error,
    send_message,
)
from .stats import VALID_WINDOWS, StatsStore
from .units import parse_rate  # noqa: F401  (re-export, CLI/Protokoll-Kompatibilitaet)

# Sentinel: "Argument nicht uebergeben" -> Default-Monitor verwenden.
# Explizit ``monitor_factory=None`` bedeutet dagegen "Monitoring aus"
# (Tests/Simulation), damit kein nethogs-Subprozess gestartet wird.
_MONITOR_DEFAULT = object()


class ConfigStore:
    """Lädt/hält/persistiert die Config in TOML."""

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        self.path = config_path_for(config_dir)
        self._config = load_config(self.path)
        # Warnung aus dem Laden festhalten (kaputte TOML -> Defaults). Der
        # Daemon startet dann trotzdem, aber status() macht das Problem sichtbar.
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
        from .units import DISPLAY_UNITS

        if unit not in DISPLAY_UNITS:
            raise ValueError(f"unit muss eines von {DISPLAY_UNITS} sein")
        self._config["unit"] = unit
        self._persist()
        return unit

    def set_budget(self, app: str | None = None, **fields) -> dict:
        """Budget setzen/aktualisieren. ``app=None`` = globales Budget.

        Nur uebergebene Felder (``day``/``week``/``enabled``) werden geaendert.
        """
        budgets = self._config.setdefault(
            "budgets", {"enabled": True, "day": None, "week": None, "rules": []}
        )
        allowed = {"day", "week", "enabled"}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unbekanntes Budget-Feld {key!r}")
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
        """Komplette Config ersetzen (Import/Profile) und persistieren."""
        self._config = config
        self._persist()
        return self._config


def parse_limit_param(value):
    """Param fuer ein Limit akzeptieren: None->unbegrenzt, Zahl, oder Rate-String."""
    if value is None or value in ("", "null", "none", "unbegrenzt", "unlimited"):
        return None
    from .units import parse_rate as pr

    return pr(value)


def _resolve_interface(value) -> str:
    """Konfig-Wert 'auto'/None/'' in das echte Routing-Interface aufloesen."""
    if value in (None, "", "auto", "automatic"):
        return detect_default_interface() or "lo"
    return value


def _resolve_tt_command(value) -> str:
    """tt-Binary ermitteln, wenn keines explizit angegeben wurde.

    Reihenfolge: Argument -> $THROTL_TT -> venv-Pfad aus install.sh -> PATH.
    Vorher war der Default der nackte Name 'tt', der ausserhalb des venv-PATH
    nicht existiert ("TrafficToll (tt) wurde nicht gefunden").
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
    """Root-/Tool-Vorauspruefung: gibt Liste von Warnungen/Fehlern zurueck."""
    import shutil

    issues = []
    if os.geteuid() != 0:
        issues.append("Daemon laeuft nicht als root (tc/cgroups/nethogs brauchen root)")
    if not os.path.exists(tt_command):
        issues.append(f"tt nicht gefunden: {tt_command}")
    for tool in ("tc", "ip", "iptables"):
        if shutil.which(tool) is None:
            issues.append(f"Kommando fehlt: {tool}")
    # ifb-Modul pruefen (fuer TrafficTolls Download-Shaping noetig)
    try:
        if not os.path.exists("/sys/module/ifb"):
            with open("/proc/modules", "r", encoding="utf-8") as handle:
                if "ifb" not in handle.read():
                    issues.append("Kernelmodul 'ifb' nicht geladen (sh -c 'modprobe ifb')")
    except OSError:
        pass
    if interface in (None, "", "auto", "lo"):
        issues.append(f"Nicht-lokales Interface fehlt (aktuell: {interface!r}). "
                      "Shaping braucht ein echtes Routing-Interface (z.B. enp34s0).")
    return issues


class Daemon:
    def __init__(self, socket_path: str = SOCKET_PATH, config_dir: str | None = None,
                 engine=None, monitor_factory=_MONITOR_DEFAULT, interval: float = 1.0,
                 tt_command: str = "tt"):
        self.socket_path = socket_path
        self.config_dir = config_dir or config_dir_default()
        self.store = ConfigStore(self.config_dir)
        self._state_lock = threading.RLock()
        self.interval = interval
        # Persistente Bandbreiten-Statistik (Punkt 5): Ringpuffer neben der
        # config.toml, wird im Monitor-Tick gefuettert.
        self.stats = StatsStore(self.config_dir, interval=interval)

        cfg = self.store.get()
        self.interface = _resolve_interface(cfg.get("interface"))

        # Engine waehlen: explizit (Tests) oder TrafficTollEngine (tt via venv)
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
        # Signatur der aktuell aktiven Zeitfenster-Regeln (Engine-Reapply).
        self._window_signature = None
        # Engine-Neustarts laufen in einem eigenen Thread (ein tt-Apply dauert
        # ~2 s und darf weder die RPC-Antworten noch die GUI blockieren).
        self._apply_event = threading.Event()
        self._apply_thread = None
        self._engine_applying = False

        atexit.register(self.shutdown)

    # --- Lifecycle ---

    def start(self) -> None:
        # Ein konfiguriertes Start-Profil vor dem ersten Apply aktivieren.
        self._apply_start_profile()
        # Initiale Config synchron anwenden, danach uebernimmt der Worker.
        self._apply_engine()
        self._start_apply_worker()
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
        self._secure_socket()
        # Monitoring-Ticker starten
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="throtl-monitor-ticker"
        )
        self._monitor_thread.start()
        print(f"Throtl-Daemon {__version__} läuft auf {self.socket_path}")

    def _secure_socket(self) -> None:
        """Zugriff auf den Daemon-Socket auf die Gruppe ``throtl`` begrenzen.

        Als root erzeugt der Socket-Pfad rw-------; ohne Schreibrecht schlaegt
        der Connect des User-Prozesses (GUI/CLI) mit "Permission denied" fehl.
        Der frueher gesetzte Modus 0666 loeste das, gab aber JEDEM lokalen
        Konto vollen Zugriff: ein unprivilegierter Nutzer konnte damit globale
        Limits setzen, die Netzgeschwindigkeit drosseln oder Regeln loeschen.

        Jetzt: ``chown root:throtl`` + Modus 0660. Nur Mitglieder der Gruppe
        (per ``usermod -aG throtl $USER``) erreichen den Daemon.

        Fehlt die Gruppe (z. B. manueller Start ohne install.sh), bleiben wir
        funktionsfaehig und weichen auf 0666 aus — aber mit deutlicher Warnung,
        damit die Abschwaechung nicht unbemerkt bleibt.
        """
        import grp

        gid = None
        if os.geteuid() == 0:
            try:
                gid = grp.getgrnam(SOCKET_GROUP).gr_gid
            except KeyError:
                print(
                    f"Warnung: Gruppe '{SOCKET_GROUP}' existiert nicht — der "
                    "Daemon-Socket ist fuer alle lokalen Nutzer zugaenglich. "
                    f"Abhilfe: 'groupadd {SOCKET_GROUP}' und "
                    f"'usermod -aG {SOCKET_GROUP} $USER' (install.sh macht das).",
                    flush=True,
                )
        try:
            if gid is not None:
                os.chown(self.socket_path, 0, gid)
                os.chmod(self.socket_path, SOCKET_MODE)
            else:
                # Kein root oder keine Gruppe: Zugriff ermoeglichen, aber
                # sichtbar machen, dass das nicht der sichere Modus ist.
                os.chmod(self.socket_path, 0o666)
        except OSError as error:
            print(f"Warnung: Socket-Rechte nicht setzbar: {error}", flush=True)

    def _monitor_loop(self) -> None:
        while self._running:
            self._tick_monitor()
            time.sleep(self.interval)

    def _tick_monitor(self) -> None:
        # Toten Monitor erkennen (nethogs beendet/abgestuerzt): Fehler merken,
        # aufraeumen; die Retry-Logik unten startet ihn dann neu. Monitor-Stubs
        # ohne is_alive() werden konservativ als lebendig behandelt.
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
                print(f"Monitor-Fehler: {error}", flush=True)
                self._stop_monitor()
        # Monitor nachziehen, falls (noch) keiner laeuft. Alle ~3 Ticks erneut.
        if self.monitor is None:
            self._monitor_retry_tick += 1
            if self._monitor_retry_tick >= 3:
                self._monitor_retry_tick = 0
                self._start_monitor()
        # Automatische Profilumschaltung (nur wenn ein Zeitplan existiert).
        self._apply_schedule()
        # Zeitfenster-Regeln: Engine neu anwenden, wenn ein Fenster kippt.
        self._apply_time_windows()
        # Echter Monitoring-Tick: Statistik fortschreiben. RPC-Snapshots
        # (list_processes) duerfen NICHT zusaetzlich zaehlen, sonst wuerde der
        # GUI-Poll die Raten doppelt verbuchen.
        self._collect_snapshot(record_stats=True)

    def _apply_schedule(self) -> None:
        """Passendes Zeitplan-Profil aktivieren (im Monitor-Tick).

        Nur wenn ueberhaupt Regeln vorhanden sind UND eine Regel JETZT passt.
        Ausserhalb aller Fenster wird bewusst NICHT zurueckgeschaltet, sondern
        die letzte Wahl beibehalten (konservativ: ein manuell gewaehltes Profil
        soll nicht mitten am Tag ueberschrieben werden).
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
                print(f"Warnung: Zeitplan-Profil {target!r}: {error}", flush=True)
                return
            self.store._persist()
        self._schedule_engine_apply()

    def _apply_start_profile(self) -> None:
        """Ein konfiguriertes ``start_profile`` beim Daemon-Start aktivieren.

        Ein Zeitplan hat Vorrang: er wird beim naechsten Monitor-Tick angewandt
        und ueberschreibt das Start-Profil, falls gerade ein Fenster passt.
        """
        with self._state_lock:
            cfg = self.store.get()
            name = cfg.get("start_profile")
            if not name or name == cfg.get("active_profile"):
                return
            try:
                apply_profile(cfg, name)
            except Exception as error:
                print(f"Warnung: Start-Profil {name!r}: {error}", flush=True)
                return
            self.store._persist()

    def _apply_time_windows(self) -> None:
        """Engine neu anwenden, wenn sich die aktiven Zeitfenster-Regeln aendern.

        TrafficToll hat keinen dynamischen Reload; damit „Firefox 20-24 Uhr"
        wirkt, wird beim Uebergang in/aus einem Fenster ein Apply angestossen.
        Die Signatur ist die Menge der gerade aktiven Regel-Keys.
        """
        cfg = self.store.get()
        rules = cfg.get("processes") or []
        if not any(rule.get("window") for rule in rules):
            self._window_signature = None
            return
        signature = tuple(sorted(
            rule.get("key", "") for rule in rules if rule_active(rule)
        ))
        if signature != self._window_signature:
            self._window_signature = signature
            self._schedule_engine_apply()

    def _iface_throughput(self):
        """Echte Interface-Rate (kbit/s) aus /proc/net/dev-Deltas.

        Das ist die verlaessliche "globale" Zahl: sie enthaelt ALLES, was ueber
        das Interface geht (auch Traffic, den nethogs keinem Prozess zuordnen
        kann, z. B. VPN/UDP/anderer Nutzer). Ohne Vergleichswert -> (None, None).
        """
        import time as _time

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
        # Sample nur aktualisieren, wenn genug Zeit vergangen ist. Mehrere
        # Aufrufer (Monitor-Tick + GUI-Poll) teilen sich dieses Sample; frueher
        # setzte jeder Aufruf das Sample zurueck und kurze Abstaende lieferten
        # None -> die Global-Zeile flackerte auf "measuring...".
        if elapsed >= 0.5:
            down = max(0, rx - previous[1]) * 8.0 / 1000.0 / elapsed
            up = max(0, tx - previous[2]) * 8.0 / 1000.0 / elapsed
            self._iface_rate = (round(down, 1), round(up, 1))
            self._iface_sample = (now, rx, tx)
        return self._iface_rate

    def _collect_snapshot(self, record_stats: bool = False) -> dict:
        """Prozess-Stats + echte Interface-Rate + angewendete Regeln.

        ``record_stats=True`` (nur aus :meth:`_tick_monitor`) schreibt die
        pro-App-Summen zusaetzlich in den persistenten :class:`StatsStore`.
        """
        raw = {}
        if self.monitor is not None:
            try:
                raw = self.monitor.snapshot()
            except Exception:
                raw = {}
        cfg = self.store.get()
        rules = cfg.get("processes", [])
        processes = []
        attributed_down = attributed_up = 0.0
        for pid, info in raw.items():
            matches = _match_rules(rules, info.get("name"), info.get("pid", pid))
            download = round(info.get("download", 0.0), 3)
            upload = round(info.get("upload", 0.0), 3)
            if pid != UNATTRIBUTED_PID:
                attributed_down += download
                attributed_up += upload
            processes.append({
                "pid": pid,
                "name": info.get("name", "?"),
                "download": download,
                "upload": upload,
                "unattributed": pid == UNATTRIBUTED_PID,
                # via Regeln: limits/prioritaet fuer die Anzeige
                "rule_name": matches.get("name"),
            })
        # Nach ANWENDUNG gruppieren: eine App laeuft oft in vielen Prozessen
        # (z. B. ein Downloader mit 8 Workern). Die App soll als EINE Zeile mit
        # der Summe erscheinen — sonst sieht man 8x
        # "python3" mit je ~0,2 MB/s statt einmal "legendary" mit ~2 MB/s.
        apps = {}
        for pid, info in raw.items():
            if pid == UNATTRIBUTED_PID:
                app, exe = UNATTRIBUTED_NAME, ""
            else:
                cmdline = info.get("name", "")
                app = pretty_app_name(cmdline)
                exe = (cmdline.split() or [""])[0]
            entry = apps.get(app)
            if entry is None:
                entry = apps[app] = {
                    "name": app,
                    "exe": exe,
                    "download": 0.0,
                    "upload": 0.0,
                    "pids": [],
                    "unattributed": pid == UNATTRIBUTED_PID,
                }
            entry["download"] += info.get("download", 0.0)
            entry["upload"] += info.get("upload", 0.0)
            if len(entry["pids"]) < 16:
                entry["pids"].append(pid)
        app_list = []
        for entry in apps.values():
            entry["download"] = round(entry["download"], 3)
            entry["upload"] = round(entry["upload"], 3)
            entry["pid_count"] = len(entry["pids"])
            if entry["unattributed"]:
                entry["rule_name"] = None
            else:
                matches = _match_rules(rules, entry["exe"] or entry["name"], None)
                entry["rule_name"] = matches.get("name")
            app_list.append(entry)

        if record_stats:
            self._record_stats(app_list)

        global_down, global_up = self._iface_throughput()
        return {
            "interface": self.interface,
            "enabled": cfg["global"].get("enabled", True),
            "processes": processes,
            "apps": app_list,  # pro Anwendung gruppiert (Summe aller PIDs)
            "rules": rules,  # fuer GUI: union von Regel + Live-Stats
            "monitored": self.monitor is not None,
            # Echte Interface-Rate (alles) vs. nur zugeordneter Traffic
            "global": {"download": global_down, "upload": global_up},
            "attributed": {"download": round(attributed_down, 1),
                           "upload": round(attributed_up, 1)},
        }

    def _record_stats(self, app_list: list) -> None:
        """Pro-App-Raten (kbit/s) in den Statistik-Speicher verbuchen."""
        for entry in app_list:
            self.stats.record(
                entry.get("name", "?"),
                entry.get("download", 0.0),
                entry.get("upload", 0.0),
            )

    # --- Engine ---

    def _apply_engine(self, config: dict | None = None) -> None:
        """Engine (tt) mit der aktuellen/uebergebenen Config synchronisieren.

        Wird beim Start einmal synchron aufgerufen; danach uebernimmt der
        Background-Worker (:meth:`_schedule_engine_apply`), damit ein ~2 s
        dauernder tt-Neustart weder RPC-Antworten noch die GUI blockiert.
        """
        if config is None:
            config = self._snapshot_config()
        try:
            self.engine.apply(config)
        except Exception as error:
            self.engine_error = f"{type(error).__name__}: {error}"
            print(f"Engine-Fehler: {self.engine_error}", flush=True)
        else:
            self.engine_error = None

    def _snapshot_config(self) -> dict:
        """Tiefe Kopie der Config unter State-Lock (nicht auf lebenden Daten rendern)."""
        with self._state_lock:
            return copy.deepcopy(self.store.get())

    def _start_apply_worker(self) -> None:
        self._apply_thread = threading.Thread(
            target=self._apply_loop, daemon=True, name="throtl-engine-apply"
        )
        self._apply_thread.start()

    def _apply_loop(self) -> None:
        """Serialisiert Engine-Neustarts und fasst schnelle Aenderungen zusammen."""
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
        """Engine-Apply anfordern: nicht blockierend und coalesced."""
        self._apply_event.set()

    def _start_monitor(self) -> None:
        if self._monitor_factory is None:
            return  # Monitoring bewusst deaktiviert (Tests/Simulation)
        if self.monitor is None:
            self.monitor = self._monitor_factory(self.interface, 1.0)
            try:
                self.monitor.start()
            except Exception as error:
                self.monitor_error = f"{type(error).__name__}: {error}"
                print(f"Monitor-Fehler: {self.monitor_error}", flush=True)
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
            "import_config": self._h_import_config,
        }

    # --- Handler ---

    def _h_status(self, params):
        engine_status = None
        try:
            engine_status = self.engine.status() if self.engine else None
        except Exception:
            engine_status = None
        cfg = self.store.get()
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
            # Kaputte/ungueltige config.toml: Daemon laeuft mit Defaults weiter,
            # der Grund ist aber abfragbar statt nur im Journal zu stehen.
            "config_warning": self.store.load_warning,
            "socket": self._socket_permissions(),
        }

    def _socket_permissions(self) -> dict:
        """Effektive Rechte des Daemon-Sockets (fuer status()/doctor)."""
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
            # 0666 = jeder lokale Nutzer darf Limits setzen -> unsicher.
            "restricted": mode != 0o666,
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
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return store.get()["global"]

    def _h_set_process(self, params):
        key = params.get("key")
        existing = None
        if key:
            existing = next(
                (r for r in self.store.get().get("processes", []) if r.get("key") == key),
                None,
            )
        if existing is not None:
            # Update einer bestehenden Regel: match_type/match_value NICHT neu
            # ableiten. Die GUI schickt die gespeicherte Regel zurueck; ein
            # erneutes make_rule() wuerde das bereits re.escape()-te Pattern
            # nochmals escapen und die Regel matchte nicht mehr.
            rule = dict(existing)
            if params.get("name"):
                rule["name"] = str(params["name"])
            if "download_limit" in params:
                rule["download_limit"] = parse_limit_param(params.get("download_limit"))
            if "upload_limit" in params:
                rule["upload_limit"] = parse_limit_param(params.get("upload_limit"))
            if params.get("priority"):
                rule["priority"] = priority_to_name(
                    priority_to_int(str(params["priority"]))
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
            raise ValueError("key fehlt")
        with self._state_lock:
            removed = self.store.remove_process(key)
        self._schedule_engine_apply()
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
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return {"enabled": enabled}

    def _h_set_unit(self, params):
        unit = str(params.get("unit", "kbps"))
        return {"unit": self.store.set_unit(unit)}

    def _h_list_processes(self, params):
        return self._collect_snapshot()

    def _h_get_stats(self, params):
        window = str(params.get("window") or "minute")
        if window not in VALID_WINDOWS:
            raise ValueError(
                f"window muss eines von {', '.join(VALID_WINDOWS)} sein"
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
                f"window muss eines von {', '.join(VALID_WINDOWS)} sein"
            )
        return {"window": window, "series": self.stats.series(window)}

    def _h_get_budgets(self, params):
        from .budgets import budget_status

        cfg = self.store.get()
        return {
            "enabled": (cfg.get("budgets") or {}).get("enabled", True),
            "entries": budget_status(cfg, self.stats),
        }

    def _h_set_budget(self, params):
        from .units import parse_size

        app = str(params.get("app") or "").strip() or None
        fields = {}
        for key in ("day", "week"):
            if key in params:
                fields[key] = parse_size(params.get(key))
        if "enabled" in params:
            fields["enabled"] = bool(params.get("enabled"))
        budgets = self.store.set_budget(app=app, **fields)
        return {"ok": True, "budgets": budgets}

    def _h_remove_budget(self, params):
        app = str(params.get("app") or "").strip()
        if not app:
            raise ValueError("app fehlt")
        return {"removed": self.store.remove_budget(app)}

    # --- Profile / Zeitplaene ---------------------------------------------

    def _h_list_profiles(self, params):
        cfg = self.store.get()
        return {
            "profiles": profile_names(cfg),
            "active": cfg.get("active_profile"),
        }

    def _h_set_profile(self, params):
        """Aktuellen Zustand als benanntes Profil sichern (optional aktivieren)."""
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
        """Start-Profil setzen/loeschen (``name`` fehlt/leer = deaktivieren)."""
        raw = params.get("name")
        name = validate_profile_name(raw) if raw and str(raw).strip() else None
        with self._state_lock:
            self.store.get()["start_profile"] = name
            self.store._persist()
        return {"start_profile": name}

    def _h_import_config(self, params):
        """Rohe (TOML-)Config validieren und komplett uebernehmen."""
        data = params.get("config")
        if not isinstance(data, dict):
            raise ValueError("config fehlt oder ist keine Tabelle")
        config = normalize(data)
        with self._state_lock:
            self.store.replace(config)
        self._schedule_engine_apply()
        self._emit_rules_changed()
        return config

    def _emit_rules_changed(self) -> None:
        # Nach einer Aenderung sofort einen frischen Snapshot ziehen, damit die
        # naechste Abfrage aktuelle Raten liefert und der /proc-Sample-Delta
        # nicht veraltet.
        self._tick_monitor()

    # --- Shutdown ---

    def shutdown(self) -> None:
        self._running = False
        self._apply_event.set()          # Apply-Worker aufwecken
        if self._apply_thread is not None:
            self._apply_thread.join(timeout=5.0)
            self._apply_thread = None
        self._stop_monitor()
        # Statistik beim Herunterfahren sichern (sonst gingen die letzten
        # <save_every Ticks verloren).
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
    """Erste passende Regel fuer einen nethogs-Prozessnamen/PID finden.

    nethogs liefert als "name" entweder den vollen exe-Pfad oder den
    Prozessnamen. Gespeicherte ``match_value``-Muster sind fuer TrafficToll
    regex-escaped — fuer den Vergleich hier muessen sie zurueckgewandelt
    werden, sonst passt keine einzige Regel.
    """
    if not name:
        return {}
    for rule in rules:
        if rule.get("name") and rule["name"] == name:
            return rule
        mv = unescape_pattern(rule.get("match_value"))
        if mv and mv == name:
            return rule
        if mv and (name.startswith(mv) or name.endswith(mv.rstrip("/"))):
            return rule
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Throtl-Daemon")
    parser.add_argument("--foreground", action="store_true",
                        help="im Vordergrund laufen (fuer systemd/Testing)")
    parser.add_argument("--socket", default=SOCKET_PATH, help="Unix-Socket-Pfad")
    parser.add_argument("--config-dir", default=None,
                        help="Config-Verzeichnis (Default: ~/.config/throtl)")
    parser.add_argument("--interface", default=None,
                        help="Netzwerk-Interface (Default: auto)")
    parser.add_argument("--simulate", action="store_true",
                        help="Tooling-Demo: verwende SimEngine (kein Root/noch kein tt)")
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
        # Interface ueberschreiben: in Config persistieren
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

    # SIGTERM/SIGINT sauber behandeln: sonst laeuft das Cleanup (Monitor/Engine
    # stoppen, Socket entfernen) bei 'systemctl stop' bzw. 'kill' nicht, und
    # nethogs/tt bleiben als Waisen zurueck.
    def _request_stop(_signum, _frame):
        daemon._running = False

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    daemon.start()
    try:
        daemon.serve_forever()
    finally:
        daemon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
