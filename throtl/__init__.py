"""Throtl — Bandbreiten-Limits und QoS pro Anwendung fuer Linux.

Architektur: privilegierter Daemon (root, systemd) mit TrafficToll-Backend,
GTK4/libadwaita-Frontend, Unix-Socket-IPC. Konfiguration unter
~/.config/throtl/ (TOML).
"""

import grp
import os
import tempfile

__version__ = "0.10.2"

# Konfigurationsverzeichnis (~/.config/throtl)
CONFIG_DIR_NAME = "throtl"

# Laufzeit-Verzeichnis (Socket, generierte YAML, tt-Log)
RUN_DIR = "/run/throtl"
SOCKET_PATH = f"{RUN_DIR}/daemon.sock"

# Gruppe, der der Daemon-Socket gehoert. Nur Mitglieder dieser Gruppe duerfen
# den root-Daemon ansprechen (Socket-Modus 0660). Ohne diese Einschraenkung
# konnte JEDER lokale Nutzer Limits setzen und das Netz drosseln.
SOCKET_GROUP = "throtl"
SOCKET_MODE = 0o660


def socket_access_hint(path: str | None = None) -> str | None:
    """Erklaeren, warum der Socket nicht erreichbar ist — wenn wir es wissen.

    Haeufigster Fall: ``install.sh`` hat den Nutzer zur Gruppe ``throtl``
    hinzugefuegt, die *laufende* Session kennt die Gruppe aber noch nicht
    (Gruppen werden beim Login zugewiesen). Gibt ``None`` zurueck, wenn es
    nichts zu erklaeren gibt (Socket fehlt, wir sind root, oder Zugriff ok).
    """
    path = path or SOCKET_PATH
    if os.geteuid() == 0:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    # connect() braucht Schreibrecht auf der Socket-Datei.
    if os.access(path, os.W_OK):
        return None
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    if info.st_mode & 0o060 and info.st_gid not in set(os.getgroups()):
        return (
            f"Access denied: {path} belongs to group '{group}', which this "
            f"session is not a member of. install.sh added you to the group, "
            f"but a running session keeps its old groups — run 'newgrp {group}' "
            f"here, or log out and back in.")
    return None


def write_text_atomic(path: str, text: str, mode: int | None = None) -> None:
    """Text atomar schreiben: temp-Datei -> fsync -> os.replace().

    Ein direktes ``open(path, "w")`` ist nicht atomar: bei Stromausfall oder
    Absturz mitten im Schreiben bleibt eine abgeschnittene Datei zurueck. Bei
    der TrafficToll-YAML rendert ``tt`` daraus eine kaputte Konfiguration, bei
    ``config.toml`` scheiterte frueher sogar der Daemon-Start.

    ``os.replace`` ist auf POSIX atomar (gleiches Dateisystem vorausgesetzt),
    ein Leser sieht also entweder die alte oder die neue Datei — nie ein
    Zwischenstadium. ``mode`` setzt explizit die Rechte (der Umask des
    Aufrufers soll die Config nicht unbemerkt auf 0600 druecken, wenn der
    Daemon sie als root neu schreibt und die GUI sie als User lesen koennen
    soll — bzw. umgekehrt).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # temp-Datei im ZIEL-Verzeichnis anlegen, sonst ist os.replace nicht
    # atomar (EXDEV ueber Dateisystemgrenzen).
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory,
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", delete=False,
    )
    tmp = handle.name
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # Nie eine halbe temp-Datei liegen lassen.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Verzeichniseintrag dauerhaft machen (best effort; manche FS koennen das
    # nicht, z. B. einige Overlay-/FUSE-Setups — dann ist das kein Fehler).
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
