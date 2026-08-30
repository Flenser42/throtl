"""Live-Bandbreiten-Monitoring pro Prozess via nethogs im Trace-Modus.

nethogs `-t` gibt pro Refresh-Block einen "Refreshing:"-Header und dann pro
Prozess eine Zeile im Format::

    Program[ <cmdline>]/<pid>/<uid>\t<sent_kBs>\t<recv_kBs>

Werte sind in **Kilobyte pro Sekunde** (Anzeige-Modus). Der Parser liest diesen
Stream inkrementell und liefert pro Tick ein Dict von Prozessen.

Hinweis zur Semantik (aus nethogs-Quelle cui.cpp `Line::log()`): die erste Zahl
nach dem Identifier ist `sent_value`, die zweite `recv_value`. Die ncurses-
Kopfzeilen bezeichnen sie als "SENT"/"RECVD"; "recv" entspricht technisch dem
DOWNLOAD (eingehend), "sent" dem UPLOAD (ausgehend). Der Parser mappt daher
recv -> download und sent -> upload.
"""

import subprocess
import threading

# nethogs `-t`: Identifier (name/pid/uid) + 2 Werte
TRACE_FIELD_COUNT = 3


def parse_trace(line: str):
    """Eine nethogs-Trace-Zeile parsen.

    Returns: (name, pid, uid, sent_kBs, recv_kBs) oder None bei unparsbar.
    """
    line = line.rstrip("\n")
    if not line or line == "Refreshing:" or line.startswith("Unknown connection"):
        return None
    fields = line.split("\t")
    if len(fields) != TRACE_FIELD_COUNT:
        return None
    ident, sent, recv = fields
    # ident ist "<name>[/<cmdline>]/<pid>/<uid>"
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
    return name, pid_str, uid_str, sent_kBs, recv_kBs


def _kBs_to_kbit(kBs: float) -> float:
    """nethogs liefert kB/s; intern rechnen wir in kbit/s."""
    from .units import kBs_to_kbit

    return kBs_to_kbit(kBs)


class TraceParser:
    """Inkrementeller Parser fuer den nethogs-`-t`-Stream.

    Jede "Refreshing:"-Zeile beendet den vorherigen Tick und liefert ihn als
    Ergebnis von ``feed``. Ein Prozess, der nicht mehr auftaucht, faellt im
    naechsten Tick einfach weg (der Aufrufer kann Alt-Eintraege auf 0 setzen).
    """

    def __init__(self):
        self._current = {}
        self._seen_mark = False

    def feed(self, chunk: str) -> list:
        """Daten zufuehren; liefert eine Liste fertiggestellter Ticks.

        Ein Tick wird an seiner Abschluss-``Refreshing:``-Marke ausgeliefert
        (Daten des vorherigen Intervalls). Leere erste Marken werden
        uebersprungen; das Fenster nach der letzten Marke liefert ``finish()``.
        """
        ticks = []
        for raw_line in chunk.splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            if line == "Refreshing:":
                if self._current:
                    ticks.append(self._current)
                    self._current = {}
                self._seen_mark = True
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
                # derselbe Prozess in mehreren Zeilen -> akkumulieren
                entry["download"] += download
                entry["upload"] += upload
        return ticks

    def finish(self) -> dict:
        """Offenes Fenster (nach der letzten Marke) als finalen Tick liefern."""
        if self._current:
            result = self._current
            self._current = {}
            return result
        return {}


class NethogsMonitor:
    """Startet nethogs -t als Subprozess und parst den Trace-Stream.

    Über ``inject`` kann ein faike File-Like-Objekt (Tests) uebergeben werden.
    """

    def __init__(self, device: str, interval: float = 1.0, cmd: str = "nethogs",
                 inject=None):
        self.device = device
        self.interval = interval
        self.cmd = cmd
        self._inject = inject
        self._proc = None
        self._parser = TraceParser()
        self._reader_thread = None
        self._running = False
        self._lock = threading.Lock()
        self._latest = {}

    def _build_argv(self) -> list:
        # -t Trace-Modus; -d Interval in Sekunden; -v 1 (kB/s-Anzeige)
        return [self.cmd, "-t", "-d", str(self.interval), "-v", "1", self.device]

    def start(self) -> None:
        if self._running:
            return
        source = self._inject
        if source is None:
            try:
                self._proc = subprocess.Popen(
                    self._build_argv(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
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

    def snapshot(self) -> dict:
        """Neuester Tick: pid -> {name, uid, download (kbit/s), upload (kbit/s)}."""
        with self._lock:
            return dict(self._latest)

    def stop(self) -> None:
        self._running = False
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)


def parse_trace_stream(stream) -> list:
    """Gesamten Stream parsen (Tests/Diagnose): liefert Liste aller Ticks."""
    parser = TraceParser()
    ticks = []
    for line in stream:
        ticks.extend(parser.feed(line))
    ticks.append(parser.finish())
    return ticks
