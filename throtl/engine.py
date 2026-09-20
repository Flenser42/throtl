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

import collections
import os
import signal
import subprocess
import threading
import time

from .config import priority_to_int

# Standardwerte (aus traffictoll/cli.py) in kbit/s
GLOBAL_MINIMUM_DOWNLOAD = 100
GLOBAL_MINIMUM_UPLOAD = 10


def format_rate_kbps(kbit_per_s) -> str:
    """Rate (kbit/s) als TrafficToll-/tc-tauglichen String (z.B. '512kbps')."""
    if kbit_per_s is None:
        return None
    return f"{round(kbit_per_s)}kbps"


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


class TrafficTollEngine:
    """Startet/überwacht den tt-Subprozess.

    ``command``: Basis-Kommando (Standard `tt` aus dem venv, per install.sh).
    ``monitor_callback``: optional; wird nach einem Reload aufgerufen.
    ``tt_argv_builder``: injizierbar (Tests) fuer die argliste.
    """

    def __init__(self, device: str, command="tt", delay=1.0, on_restart=None,
                 log_path: str | None = None):
        self.device = device
        self.command = command
        self.delay = delay
        self.on_restart = on_restart
        self.dry_run = False
        self._proc = None
        # RLock ist zwingend: status() haelt den Lock und wertet intern den
        # Prozesszustand aus. Mit einem nicht-reentranten threading.Lock()
        # blockierte der Aufruf sich selbst -> Daemon antwortete nie (Deadlock).
        self._lock = threading.RLock()
        # Getrennter Lock fuer den stderr-Puffer: der Reader-Thread darf nie
        # auf dem Lifecycle-Lock warten muessen.
        self._stderr_lock = threading.Lock()
        self._active_config = None
        self._generation = 0
        self._exit_code = None
        self._stderr_tail = collections.deque(maxlen=50)
        self._stderr_path = log_path
        self._reader_thread = None
        self._watchdog = None
        self._last_error = None
        # Metriken (fuer status/doctor): Apply-Haeufigkeit, Neustarts,
        # Fehlschlaege und Dauer. Zeigt, ob das Apply-Coalescing greift.
        self._applies = 0
        self._restarts = 0
        self._apply_failures = 0
        self._last_apply_seconds = None
        self._total_apply_seconds = 0.0

    def apply(self, config: dict) -> None:
        """Neue Config schreiben und tt nur bei echter Aenderung neu starten.

        Ein Neustart kostet ~2 s (tt beenden + neu aufsetzen). Deshalb wird
        bei identischer Config (z. B. das GUI schickt beim Editieren die ganze
        Regel zurueck) nichts getan.
        """
        yaml = render_tt_config(config)
        enabled = config["global"].get("enabled", True)
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            if self._active_config == yaml and (not enabled or running):
                return
            self._generation += 1
            self._active_config = yaml
            self._applies += 1
            started = time.monotonic()
            self._stop_locked()
            if not enabled:
                # Shaping deaktiviert: kein tt-Prozess
                self._record_apply(started)
                if self.on_restart is not None:
                    self.on_restart(disabled=True, error=None)
                return
            try:
                self._start_locked(yaml)
            except Exception as error:
                self._apply_failures += 1
                self._record_apply(started)
                self._last_error = f"{type(error).__name__}: {error}"
                if self.on_restart is not None:
                    self.on_restart(disabled=False, error=str(error))
                raise
            else:
                self._restarts += 1
                self._record_apply(started)
                self._last_error = None
        if self.on_restart is not None:
            self.on_restart(disabled=False, error=None)

    def _record_apply(self, started: float) -> None:
        elapsed = time.monotonic() - started
        self._last_apply_seconds = round(elapsed, 3)
        self._total_apply_seconds += elapsed

    def _tc_cleanup(self) -> None:
        """tc-Reste entfernen, damit tt seine QDiscs frisch aufbauen kann.

        TrafficToll legt beim Start ein neues root-qdisc an. Existiert noch
        eines aus einem vorherigen Lauf (z. B. weil tt per SIGTERM beendet
        wurde und sein atexit-Cleanup nicht lief), scheitert der Aufbau mit
        "Exclusivity flag on, cannot modify" / "Parent Qdisc doesn't exists"
        und die Limits greifen nicht mehr.
        """
        if self.dry_run:
            return
        devices = [self.device]
        for name in ("ifb0", "ifb1"):
            if os.path.exists(f"/sys/class/net/{name}"):
                devices.append(name)
        for device in devices:
            for args in (("qdisc", "del", "dev", device, "root"),
                         ("qdisc", "del", "dev", device, "ingress")):
                try:
                    subprocess.run(["tc", *args], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass

    def _start_locked(self, yaml: str) -> None:
        cfg_path = self._write_yaml(yaml)
        self._tc_cleanup()
        argv = self._build_argv(cfg_path)
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"TrafficToll ({self.command}) wurde nicht gefunden. "
                "Bitte install.sh ausfuehren."
            ) from None
        self._exit_code = None
        self._stderr_tail.clear()
        self._reader_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name="throtl-tt-stderr"
        )
        self._reader_thread.start()
        self._watchdog = threading.Thread(
            target=self._watch, daemon=True, name="throtl-tt-watch"
        )
        self._watchdog.start()

    def _drain_stderr(self) -> None:
        """tt-stderr zeilenweise sammeln (fuer Diagnose, z.B. tc-Fehler)."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line:
                continue
            with self._stderr_lock:
                self._stderr_tail.append(line)
                if self._stderr_path:
                    try:
                        with open(self._stderr_path, "a", encoding="utf-8") as handle:
                            handle.write(line + "\n")
                    except OSError:
                        pass

    def _watch(self) -> None:
        """Exit-Code des tt-Prozesses festhalten (frueher Absturz sichtbar)."""
        proc = self._proc
        if proc is None:
            return
        code = proc.wait()
        with self._lock:
            self._exit_code = code

    def _stop_locked(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # SIGINT (nicht SIGTERM): TrafficToll faengt KeyboardInterrupt und
            # sein atexit-Cleanup entfernt danach die QDiscs. Mit SIGTERM
            # blieben sie liegen -> naechster Start scheitert (siehe _tc_cleanup).
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.terminate()
                    proc.wait(timeout=1.5)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
        # stderr-Reader-/Watchdog-Threads beenden (fds schliessen damit Reader endet)
        if proc is not None:
            if proc.stderr is not None:
                try:
                    proc.stderr.close()
                except OSError:
                    pass
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=2.0)
            if self._watchdog is not None:
                self._watchdog.join(timeout=2.0)
            self._reader_thread = None
            self._watchdog = None
        self._proc = None

    def _build_argv(self, cfg_path: str) -> list:
        return [self.command, self.device, cfg_path, "--delay", str(self.delay)]

    def _write_yaml(self, yaml: str) -> str:
        """YAML atomar im Laufzeitverzeichnis ablegen (NICHT /tmp).

        In /tmp kollidieren die Rechte verschiedener Nutzer (beobachtet:
        EACCES auf /tmp/throtl-tt-config.yaml). Reihenfolge:
        $THROTL_RUN_DIR -> /run/throtl -> Temp-Verzeichnis.

        Geschrieben wird atomar: ``tt`` liest die Datei beim Start komplett ein
        und wuerde eine halb geschriebene YAML als kaputte Konfiguration
        interpretieren (Rendern + Schreiben passiert bei jeder Aenderung).
        """
        import tempfile

        from . import write_text_atomic

        candidates = []
        env_dir = os.environ.get("THROTL_RUN_DIR")
        if env_dir:
            candidates.append(env_dir)
        candidates.append("/run/throtl")
        candidates.append(tempfile.gettempdir())

        last_error = None
        for directory in candidates:
            try:
                os.makedirs(directory, exist_ok=True)
                path = os.path.join(directory, "throtl-tt-config.yaml")
                # 0600: die YAML beschreibt die Regeln des Nutzers und muss
                # nicht fuer andere lokale Konten lesbar sein.
                write_text_atomic(path, yaml, mode=0o600)
                return path
            except OSError as error:
                last_error = error
                continue
        raise RuntimeError(
            "Konnte die TrafficToll-Config nicht schreiben (versucht: "
            f"{candidates}): {last_error}"
        )

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def status(self) -> dict:
        # Kein verschachteltes Locking: der Prozesszustand wird inline gelesen
        # statt is_running() aufzurufen (das war der Deadlock mit Lock()).
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            exit_code = self._exit_code
            generation = self._generation
        with self._stderr_lock:
            tail = list(self._stderr_tail)[-5:]
        return {
            "running": running,
            "device": self.device,
            "generation": generation,
            "exit_code": exit_code,
            "stderr_tail": tail,
            "stderr_path": self._stderr_path,
            "last_error": self._last_error,
            "applies": self._applies,
            "restarts": self._restarts,
            "apply_failures": self._apply_failures,
            "last_apply_seconds": self._last_apply_seconds,
            "avg_apply_seconds": (
                round(self._total_apply_seconds / self._applies, 3)
                if self._applies else None
            ),
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
        self._applies = 0
        self._restarts = 0
        self._apply_failures = 0

    def apply(self, config: dict) -> None:
        self._generation += 1
        self._applies += 1
        self._enabled = bool(config["global"].get("enabled", True))
        if self._enabled:
            self._restarts += 1
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
            "applies": self._applies,
            "restarts": self._restarts,
            "apply_failures": self._apply_failures,
            "last_apply_seconds": None,
            "avg_apply_seconds": None,
        }
