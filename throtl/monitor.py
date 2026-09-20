"""Live per-process bandwidth monitoring via nethogs (trace mode).

nethogs `-t` prints one "Refreshing:" block per interval, each containing lines
like::

    Program[ <cmdline>]/<pid>/<uid>\t<sent_kBs>\t<recv_kBs>

Values are throughput in kB/s (view mode 0). Per the nethogs source
(cui.cpp, ``Line::log()``) the first number is ``sent_value`` (upload) and the
second is ``recv_value`` (download); we map them accordingly and convert to
kbit/s.

Traffic that cannot be attributed to a process is reported by nethogs as
``unknown TCP/0/0``. We keep it under a synthetic entry (pid ``-``,
name ``(unattributed)``) instead of discarding it, so nothing silently
disappears and the sum reconciles with the interface counters.

We monitor exactly ONE device (the shaped interface): nethogs emits a line per
(device, process) and the trace format carries no device name, so monitoring
several devices would add the same flow multiple times.
"""

import collections
import os
import shutil
import subprocess
import threading

# nethogs `-t`: identifier (name/pid/uid) + 2 values
TRACE_FIELD_COUNT = 3

# Synthetic pid/name for traffic nethogs cannot attribute to a process
UNATTRIBUTED_PID = "-"
UNATTRIBUTED_NAME = "(unattributed)"


def resolve_nethogs_binary(cmd: str | None = None) -> str:
    """Locate nethogs as an ABSOLUTE path.

    Matters for systemd services with a minimal/odd PATH: otherwise the daemon
    cannot find "nethogs" and monitoring stays off.
    Order: argument -> $THROTL_NETHOGS -> /usr/bin/nethogs -> PATH.
    """
    if cmd:
        return cmd
    env = os.environ.get("THROTL_NETHOGS")
    if env:
        return env
    for candidate in ("/usr/bin/nethogs", "/usr/local/bin/nethogs"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("nethogs") or "nethogs"


# Interpreter, bei denen argv[0] nicht der App-Name ist
_INTERPRETERS = {
    "python", "python2", "python3", "pypy", "pypy3", "node", "nodejs", "deno",
    "sh", "bash", "zsh", "dash", "fish", "perl", "ruby", "php", "env",
    "wine", "wine64", "java", "mono", "dotnet",
}
_SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".sh", ".rb", ".pl", ".php")


def _basename(path: str) -> str:
    return (path or "").strip().strip('"').strip("'").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def pretty_app_name(cmdline: str) -> str:
    """Lesbaren App-Namen aus der nethogs-Kommandozeile ableiten.

    nethogs liefert argv[0] (oft "python3") plus, mit -l, die Argumente:
      "python3 ./legendary install CrabEA …"  -> "legendary"
      "python3 /opt/app/main.py --serve"      -> "main"
      "python3 -m http.server"                -> "http.server"
      "java -jar JDownloader.jar"             -> "JDownloader"
      "/usr/lib/electron43/electron --type=…" -> "electron"
    """
    tokens = (cmdline or "").split()
    if not tokens:
        return "?"
    base = _basename(tokens[0]) or tokens[0]
    looks_like_interpreter = (
        base in _INTERPRETERS
        or base.startswith(("python", "node"))
    )
    if not looks_like_interpreter:
        return base
    rest = tokens[1:]
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "-m" and index + 1 < len(rest):
            return _basename(rest[index + 1]) or base
        if token == "-jar" and index + 1 < len(rest):
            name = _basename(rest[index + 1])
            return name[:-4] if name.lower().endswith(".jar") else (name or base)
        if token.startswith("-"):
            index += 1
            continue
        name = _basename(token)
        lowered = name.lower()
        for suffix in _SCRIPT_SUFFIXES:
            if lowered.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name or base
    return base


def parse_trace(line: str):
    """Parse one nethogs trace line.

    Returns ``(name, pid, uid, sent_kBs, recv_kBs)`` or None if unparsable.
    Unattributable traffic (pid 0 / "unknown …") is returned with
    ``pid == UNATTRIBUTED_PID`` so the caller can keep it.
    """
    line = line.rstrip("\n")
    if not line or line == "Refreshing:" or line.startswith("Unknown connection"):
        return None
    fields = line.split("\t")
    if len(fields) != TRACE_FIELD_COUNT:
        return None
    ident, sent, recv = fields
    parts = ident.split("/")
    if len(parts) < 3:
        return None
    pid_str = parts[-2]
    uid_str = parts[-1]
    name = "/".join(parts[:-2]) or "?"
    try:
        sent_kBs = float(sent)
        recv_kBs = float(recv)
    except ValueError:
        return None
    if pid_str in ("0", "?") or name.lower().startswith("unknown"):
        return UNATTRIBUTED_NAME, UNATTRIBUTED_PID, uid_str, sent_kBs, recv_kBs
    return name, pid_str, uid_str, sent_kBs, recv_kBs


def _kBs_to_kbit(kBs: float) -> float:
    """nethogs rechnet in 1024er-Schritten (``#define KB (1UL << 10)``), also
    KiB/s. Intern nutzen wir kbit/s (1000er): 1 KiB/s = 1024*8/1000 kbit/s.

    Vorher wurde KiB/s wie kB/s behandelt -> Raten ~2,4 % zu niedrig.
    """
    return kBs * 1024.0 * 8.0 / 1000.0


class TraceParser:
    """Incremental parser for the nethogs `-t` stream.

    Every "Refreshing:" line completes the previous tick and is returned by
    ``feed``. A process that no longer appears simply disappears in the next
    tick.
    """

    def __init__(self):
        self._current = {}

    def feed(self, chunk: str) -> list:
        ticks = []
        for raw_line in chunk.splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            if line == "Refreshing:":
                if self._current:
                    ticks.append(self._current)
                    self._current = {}
                continue
            parsed = parse_trace(line)
            if parsed is None:
                continue
            name, pid, uid, sent, recv = parsed
            entry = self._current.get(pid)
            download = _kBs_to_kbit(recv)
            upload = _kBs_to_kbit(sent)
            if entry is None:
                self._current[pid] = {
                    "name": name,
                    "uid": uid,
                    "download": download,
                    "upload": upload,
                }
            else:
                # same process on several lines -> accumulate
                entry["download"] += download
                entry["upload"] += upload
        return ticks

    def finish(self) -> dict:
        """Return the open window (after the last mark) as the final tick."""
        if self._current:
            result = self._current
            self._current = {}
            return result
        return {}


class NethogsMonitor:
    """Runs nethogs -t as a subprocess and parses the trace stream.

    ``inject`` accepts a file-like object for tests.
    """

    def __init__(self, device: str, interval: float = 1.0, cmd: str | None = None,
                 inject=None, capture_udp: bool = True):
        self.device = device
        self.interval = interval
        self.cmd = resolve_nethogs_binary(cmd)
        self.capture_udp = capture_udp
        self._inject = inject
        self._proc = None
        self._parser = TraceParser()
        self._reader_thread = None
        self._running = False
        self._lock = threading.Lock()
        self._latest = {}
        self._stderr_tail = collections.deque(maxlen=30)
        self._stderr_thread = None
        self.last_error = None

    def _build_argv(self) -> list:
        # -t trace mode, -d interval. No -v: nethogs' default 0 = kB/s (rate);
        # -v 1 would be cumulative "total kB".
        # -C captures TCP *and* UDP (QUIC/VPN/DNS live on UDP and would
        # otherwise be missing entirely).
        argv = [self.cmd, "-t", "-d", str(self.interval)]
        if self.capture_udp:
            argv.append("-C")
        # -l: vollstaendige Kommandozeile mit ausgeben. Ohne -l meldet nethogs
        # nur argv[0] ("python3"), damit sind Skript-Apps nicht unterscheidbar.
        argv.append("-l")
        if self.device not in (None, "", "auto", "automatic"):
            argv.append(self.device)
        return argv

    def start(self) -> None:
        if self._running:
            return
        source = self._inject
        if source is None:
            try:
                self._proc = subprocess.Popen(
                    self._build_argv(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError as error:
                raise RuntimeError(
                    "nethogs ist nicht installiert (pacman -S nethogs). "
                    "Live-Monitoring deaktiviert."
                ) from error
            source = self._proc.stdout
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_stream, args=(source,), daemon=True
        )
        self._reader_thread.start()
        if self._proc is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(self._proc,), daemon=True
            )
            self._stderr_thread.start()

    def _drain_stderr(self, proc) -> None:
        """nethogs-stderr sammeln (Diagnose; geht sonst verloren)."""
        stream = proc.stderr
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            line = line.rstrip()
            if line:
                self._stderr_tail.append(line)

    def is_alive(self) -> bool:
        """Laeuft der nethogs-Prozess noch? (Injektionen: solange running.)"""
        if self._inject is not None:
            return self._running
        return self._proc is not None and self._proc.poll() is None

    def stderr_tail(self) -> list:
        return list(self._stderr_tail)

    def _read_stream(self, stream) -> None:
        for line in iter(stream.readline, ""):
            if not self._running:
                break
            try:
                for tick in self._parser.feed(line):
                    with self._lock:
                        self._latest = tick
            except Exception:
                continue
        with self._lock:
            self._latest = self._parser.finish()
        # Stream zu Ende: wenn wir nicht selbst gestoppt haben, ist nethogs
        # gestorben. Prozess reapen (kein Zombie) und Grund merken.
        proc = self._proc
        if self._running and proc is not None:
            try:
                code = proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                code = None
            if code is not None:
                tail = " | ".join(list(self._stderr_tail)[-3:])
                self.last_error = f"nethogs exited (code {code})"
                if tail:
                    self.last_error += f": {tail}"

    def snapshot(self) -> dict:
        """Latest tick: pid -> {name, uid, download (kbit/s), upload (kbit/s)}."""
        with self._lock:
            return dict(self._latest)

    def stop(self) -> None:
        self._running = False
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            # Auf das Ende warten — sonst bleibt der nethogs-Subprozess als
            # Zombie zurueck (ResourceWarning). Der Prozess-Tod schliesst das
            # Schreibende der Pipe, der Reader-Thread laeuft dadurch aus.
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2.0)
            self._stderr_thread = None
        # Pipes erst nach den Readern schliessen (sonst ValueError im Reader).
        if proc is not None:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self._proc = None
