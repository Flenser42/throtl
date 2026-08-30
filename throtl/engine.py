"""TrafficToll-Engine: YAML-Konfiguration rendern und tt-Prozess steuern.

Die TrafficToll-Config wird bei jeder Aenderung (Limit setzen, Prioritaet,
global an/aus, globale Limits) neu gerendert und der `tt`-Subprozess mit der
neuen YAML **neu gestartet**. TrafficToll hat keinen dynamischen Reload
(Config wird einmal beim Start gelesen; er richtet die tc-Auflagen nur fuer die
gefundenem Ports/Verbindungen nach), daher ist Neustart der zuverlaessige Weg.

Semantik des YAML-Formats (vgl. example.yaml in cryzed/TrafficToll):
- download/upload: Interface-Obergrenzen (global). Ohne sie funktioniert
  Priorisierung nur eingeschraenkt. Fuer "kein globales Limit" lassen wir sie
  weg (unbegrenzt); fuer die Priorisierung setzen wir ein hohes Kap (z.B. die
  gemessene Leitung, falls der User es konfiguriert).
- download-minimum/upload-minimum: garantierte Mindestrate fuer nicht
  gematchten Traffic.
- processes: Regeln mit download/upload (kbps) und -priority (int).

Da eine Prioritaet in TrafficToll pro Anwendung getrennt Download/UPLOAD haben
kann, wir aber einen einzelnen GUI-Wert haben, werden beide gleich gesetzt.
"""

import errno
import os
import signal
import subprocess
import threading
import time

from .config import PRIORITY_TO_INT, priority_to_int
from . import units

# Standardwerte (aus traffictoll/cli.py) in kbit/s
GLOBAL_MINIMUM_DOWNLOAD = 100
GLOBAL_MINIMUM_UPLOAD = 10
PROCESS_MINIMUM_DOWNLOAD = 10
PROCESS_MINIMUM_UPLOAD = 1


def format_rate_kbps(kbit_per_s) -> str:
    """Rate (kbit/s) als TrafficToll-/tc-tauglichen String (z.B. '512kbps')."""
    if kbit_per_s is None:
        return None
    return f"{int(round(kbit_per_s))}kbps"


def yaml_quote(value: str) -> str:
    """Minimaler Double-Quoted-YAML-Escaper fuer die Keys/Werte."""
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def render_tt_config(config: dict) -> str:
    """Die Throtl-Config in TrafficToll-YAML rendern (exaktes tt-Format)."""
    g = config["global"]
    lines = []

    download_limit = g.get("download_limit")
    upload_limit = g.get("upload_limit")
    enabled = g.get("enabled", True)

    # Abgeschaltet: leere Config ohne aktive Regeln/Interface-Limits.
    # tt deaktiviert das Shaping dann faktisch (keine processes, keine Limits).
    if not enabled:
        lines.append("# traffic shaping deaktiviert")
        return "\n".join(lines) + "\n"

    if download_limit is not None:
        lines.append(f"download: {format_rate_kbps(download_limit)}")
    if upload_limit is not None:
        lines.append(f"upload: {format_rate_kbps(upload_limit)}")

    lines.append(
        f"download-minimum: {format_rate_kbps(g.get('download_minimum', GLOBAL_MINIMUM_DOWNLOAD))}"
    )
    lines.append(
        f"upload-minimum: {format_rate_kbps(g.get('upload_minimum', GLOBAL_MINIMUM_UPLOAD))}"
    )
    lines.append(
        f"download-priority: {priority_to_int(g.get('download_priority', 'normal'))}"
    )
    lines.append(
        f"upload-priority: {priority_to_int(g.get('upload_priority', 'normal'))}"
    )

    processes = config.get("processes", [])
    if processes:
        lines.append("processes:")
        for rule in processes:
            name = rule.get("name") or rule.get("key") or "regel"
            lines.append(f"  {yaml_quote(name)}:")
            dl = rule.get("download_limit")
            ul = rule.get("upload_limit")
            if dl is not None:
                lines.append(f"    download: {format_rate_kbps(dl)}")
            if ul is not None:
                lines.append(f"    upload: {format_rate_kbps(ul)}")
            priority_name = rule.get("priority", "normal")
            prio = priority_to_int(priority_name)
            lines.append(f"    download-priority: {prio}")
            lines.append(f"    upload-priority: {prio}")
            if rule.get("recursive"):
                lines.append("    recursive: true")
            # match: einzelnes Predicate wie in example.yaml
            mt = rule.get("match_type", "exe")
            mv = rule.get("match_value", "")
            # TrafficToll matched regex gegen den echten Wert. Fuer exe/name
            # ist das Pattern bereits re.escape()-t -> Literal-Match.
            lines.append("    match:")
            lines.append(f"      - {mt}: {yaml_quote(mv)}")

    return "\n".join(lines) + "\n"


def build_req(config: dict) -> dict:
    """Die von einem externen Tool (tt) benoetigten Daten als Request-Payload."""
    return render_tt_config(config)


class TrafficTollEngine:
    """Startet/überwacht den tt-Subprozess.

    ``command``: Basis-Kommando (Standard `tt` aus dem venv, per install.sh).
    ``monitor_callback``: optional; wird nach einem Reload aufgerufen.
    ``tt_argv_builder``: injizierbar (Tests) fuer die argliste.
    """

    def __init__(self, device: str, command="tt", delay=1.0, on_restart=None):
        self.device = device
        self.command = command
        self.delay = delay
        self.on_restart = on_restart
        self._proc = None
        self._lock = threading.Lock()
        self._active_config = None
        self._generation = 0

    def apply(self, config: dict) -> None:
        """Neue Config schreiben und tt neu starten."""
        yaml = render_tt_config(config)
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._active_config = yaml
            self._stop_locked()
            if not config["global"].get("enabled", True):
                # Shaping deaktiviert: kein tt-Prozess
                if self.on_restart is not None:
                    self.on_restart(disabled=True, error=None)
                return
            try:
                self._start_locked(yaml)
            except (OSError, subprocess.SubprocessError) as error:
                if self.on_restart is not None:
                    self.on_restart(disabled=False, error=str(error))
                raise
        if self.on_restart is not None:
            self.on_restart(disabled=False, error=None)

    def _start_locked(self, yaml: str) -> None:
        cfg_path = self._write_yaml(yaml)
        argv = self._build_argv(cfg_path)
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"TrafficToll ({self.command}) wurde nicht gefunden. "
                "Bitte install.sh ausfuehren."
            ) from None

    def _stop_locked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
        self._proc = None

    def _build_argv(self, cfg_path: str) -> list:
        return [self.command, self.device, cfg_path, "--delay", str(self.delay)]

    def _write_yaml(self, yaml: str) -> str:
        """YAML unter dem state-Dir ablegen; Pfad zurueckgeben."""
        import tempfile

        directory = os.environ.get("THROTL_RUN_DIR", tempfile.gettempdir())
        path = os.path.join(directory, "netlimiter-tt-config.yaml")
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(yaml)
        return path

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "device": self.device,
            "generation": self._generation,
        }


class SimEngine:
    """Simulations-Engine (kein Root/noch kein tt installiert).

    Werden von Tests/CLI-Demo verwendet, um Limits konzeptionell anzuwenden,
    ohne wirklich tc-Auflagen zu setzen. Interface ist eine Teilmenge von
    TrafficTollEngine, damit der Daemon beides bedienen kann.
    """

    def __init__(self, device="auto-interface"):
        self.device = device
        self.simulated = True
        self._enabled = False
        self._rules = []
        self._generation = 0

    def apply(self, config: dict) -> None:
        self._generation += 1
        self._enabled = bool(config["global"].get("enabled", True))
        self._rules = list(config.get("processes", []))

    def is_running(self) -> bool:
        # SimEngine "laeuft" nur, wenn Shaping aktiv ist
        return self._enabled

    def stop(self) -> None:
        self._enabled = False

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "device": self.device,
            "generation": self._generation,
            "simulated": True,
        }
