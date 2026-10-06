"""TrafficToll engine: render the YAML config and steer the tt process.

The TrafficToll config is re-rendered on every change (set a limit, priority,
global on/off, global limits) and the ``tt`` subprocess is **restarted** with the
new YAML. TrafficToll has no dynamic reload (the config is read once at startup;
it installs the tc rules only for the ports/connections found then), so a restart
is the reliable path.

Semantics of the YAML format (cf. example.yaml in cryzed/TrafficToll):
- download/upload: interface caps (global). Without them prioritisation only
  works in a limited way. For "no global limit" we leave them out (unlimited);
  for prioritisation we set a high cap (e.g. the measured line, if the user
  configured it).
- download-minimum/upload-minimum: guaranteed minimum rate for unmatched traffic.
- processes: rules with download/upload (kbps) and -priority (int).

Since a TrafficToll priority can differ for download/upload per application, but
we have a single GUI value, both are set equal.
"""

import collections
import os
import signal
import subprocess
import threading
import time

from .config import priority_to_int, rule_active

# Defaults (from traffictoll/cli.py) in kbit/s
GLOBAL_MINIMUM_DOWNLOAD = 100
GLOBAL_MINIMUM_UPLOAD = 10


def format_rate_kbps(kbit_per_s) -> str:
    """Rate (kbit/s) as a TrafficToll-/tc-compatible string.

    Use only the bit forms of iproute2 (``kbit``/``mbit``). ``kbps`` is read by
    ``tc`` as ``KBps`` regardless of spelling — i.e. kilobytes per second. A
    limit of 8000 kbit/s landed as 8000 KB/s = 64 Mbit/s in the class: every
    limit was 8x too high.
    """
    if kbit_per_s is None:
        return None
    value = round(kbit_per_s)
    if value >= 1_000_000:
        # 1 Gbit/s = 1_000_000 kbit/s, so the divisor must match the unit.
        text = f"{value / 1_000_000:.3f}".rstrip("0").rstrip(".")
        return f"{text}gbit"
    return f"{value}kbit"


def yaml_quote(value: str) -> str:
    """Minimal double-quoted YAML escaper for the keys/values.

    Escapes ``"``/``\\`` plus all C0 control characters (including ``\r``) and
    U+2028/U+2029 — a raw ``\r`` in a rule name made the rendered YAML
    unparseable (tt then failed to start).
    """
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F or code in (0x2028, 0x2029):
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def render_tt_config(config: dict, when=None) -> str:
    """Render the Throtl config as TrafficToll YAML (exact tt format).

    ``when`` (``datetime``) is evaluated for time-window rules: rules whose
    window is not currently active are omitted.
    """
    g = config["global"]
    lines = []

    download_limit = g.get("download_limit")
    upload_limit = g.get("upload_limit")
    enabled = g.get("enabled", True)

    # Disabled: empty config without active rules/interface limits.
    # tt then effectively disables shaping (no processes, no limits).
    if not enabled:
        lines.append("# traffic shaping disabled")
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

    processes = [rule for rule in config.get("processes", [])
                 if rule_active(rule, when)]
    if processes:
        lines.append("processes:")
        for rule in processes:
            name = rule.get("name") or rule.get("key") or "rule"
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
            # match: a single predicate like in example.yaml
            mt = rule.get("match_type", "exe")
            mv = rule.get("match_value", "")
            # TrafficToll matches a regex against the real value. For exe/name
            # the pattern is already re.escape()-d -> literal match.
            lines.append("    match:")
            lines.append(f"      - {mt}: {yaml_quote(mv)}")

    return "\n".join(lines) + "\n"


class TrafficTollEngine:
    """Start/monitor the tt subprocess.

    ``command``: base command (default ``tt`` from the venv, via install.sh).
    ``monitor_callback``: optional; called after a reload.
    ``tt_argv_builder``: injectable (tests) for the arg list.
    """

    def __init__(self, device: str, command="tt", delay=1.0, on_restart=None,
                 log_path: str | None = None):
        self.device = device
        self.command = command
        self.delay = delay
        self.on_restart = on_restart
        self.dry_run = False
        self._proc = None
        # RLock is mandatory: status() holds the lock and internally evaluates
        # the process state. With a non-reentrant threading.Lock() the call
        # blocked itself -> daemon never answered (deadlock).
        self._lock = threading.RLock()
        # Separate lock for the stderr buffer: the reader thread must never
        # have to wait on the lifecycle lock.
        self._stderr_lock = threading.Lock()
        self._active_config = None
        self._generation = 0
        self._exit_code = None
        self._stderr_tail = collections.deque(maxlen=50)
        self._stderr_path = log_path
        self._LOG_MAX_BYTES = 1 << 20  # 1 MiB, then it is truncated
        self._reader_thread = None
        self._watchdog = None
        self._last_error = None
        # Metrics (for status/doctor): apply frequency, restarts, failures and
        # duration. Shows whether the apply coalescing is working.
        self._applies = 0
        self._restarts = 0
        self._apply_failures = 0
        self._last_apply_seconds = None
        self._total_apply_seconds = 0.0

    def apply(self, config: dict) -> None:
        """Write the new config and restart tt only on a real change.

        A restart costs ~2 s (stop tt + set it up again). So for an identical
        config (e.g. the GUI sends the whole rule back while editing) nothing
        is done.
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
                # Shaping disabled: no tt process
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
        """Remove tc leftovers so tt can build its qdiscs fresh.

        TrafficToll creates a new root qdisc at startup. If one still exists
        from a previous run (e.g. tt was stopped via SIGTERM and its atexit
        cleanup did not run), the setup fails with "Exclusivity flag on, cannot
        modify" / "Parent Qdisc doesn't exists" and the limits no longer apply.
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
                f"TrafficToll ({self.command}) was not found. "
                "Please run install.sh."
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

    def _append_stderr_log(self, line: str) -> None:
        """Append a diag line and truncate the file when needed.

        ``/run/throtl`` is tmpfs: an unboundedly growing tt.log would eat
        memory, so only the last chunk is kept. Truncation and appending happen
        under ``_stderr_lock`` so two lines do not race each other.
        """
        path = self._stderr_path
        if not path:
            return
        with self._stderr_lock:
            try:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    # File does not exist yet (first call) — do not treat it as
                    # an error, otherwise nothing is ever written.
                    size = 0
                if size >= self._LOG_MAX_BYTES:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        tail = handle.read()[-(self._LOG_MAX_BYTES // 4):]
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(tail)
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    def _drain_stderr(self) -> None:
        """Collect tt's stderr line by line (for diagnostics, e.g. tc errors)."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line:
                continue
            with self._stderr_lock:
                self._stderr_tail.append(line)
            self._append_stderr_log(line)

    def _watch(self) -> None:
        """Record the tt process's exit code (an early crash stays visible)."""
        proc = self._proc
        if proc is None:
            return
        code = proc.wait()
        with self._lock:
            self._exit_code = code

    def _stop_locked(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # SIGINT (not SIGTERM): TrafficToll catches KeyboardInterrupt and
            # its atexit cleanup then removes the qdiscs. With SIGTERM they
            # would remain -> next start fails (see _tc_cleanup).
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
        # Stop the stderr reader/watchdog threads (close fds so the reader ends)
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
        """Place the YAML atomically in the runtime directory (NOT /tmp).

        In /tmp the permissions of different users collide (observed: EACCES on
        /tmp/throtl-tt-config.yaml). Order: $THROTL_RUN_DIR -> /run/throtl ->
        temp directory.

        Written atomically: ``tt`` reads the file completely at startup and
        would interpret a half-written YAML as a broken config (rendering +
        writing happens on every change).
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
                # 0600: the YAML describes the user's rules and does not need to
                # be readable by other local accounts.
                write_text_atomic(path, yaml, mode=0o600)
                return path
            except OSError as error:
                last_error = error
                continue
        raise RuntimeError(
            "Could not write the TrafficToll config (tried: "
            f"{candidates}): {last_error}"
        )

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def status(self) -> dict:
        # No nested locking: the process state is read inline instead of calling
        # is_running() (that was the deadlock with Lock()).
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
    """Simulation engine (no root / no tt installed yet).

    Used by tests/CLI demo to apply limits conceptually without really setting
    tc rules. The interface is a subset of TrafficTollEngine, so the daemon can
    drive both.
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
        # SimEngine "runs" only when shaping is active
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
