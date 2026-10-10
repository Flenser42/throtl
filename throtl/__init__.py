"""Throtl — per-application bandwidth limits and QoS for Linux.

Architecture: a privileged daemon (root, systemd) with a TrafficToll backend,
the Tauri dashboard (throtl-app/) and the CLI as frontends over Unix-socket IPC.
Configuration lives in ~/.config/throtl/ (TOML).
"""

import grp
import os
import tempfile

__version__ = "0.14.1"

# Configuration directory (~/.config/throtl)
CONFIG_DIR_NAME = "throtl"

# Runtime directory (socket, generated YAML, tt log)
RUN_DIR = "/run/throtl"
SOCKET_PATH = f"{RUN_DIR}/daemon.sock"

# The group that owns the daemon socket. Only members of this group may talk to
# the root daemon (socket mode 0660). Without this restriction ANY local user
# could set limits and throttle the network.
SOCKET_GROUP = "throtl"
SOCKET_MODE = 0o660


def socket_access_hint(path: str | None = None) -> str | None:
    """Explain why the socket is unreachable — when we know the reason.

    Most common case: ``install.sh`` added the user to the ``throtl`` group, but
    the *running* session does not know the group yet (groups are assigned at
    login). Returns ``None`` when there is nothing to explain (socket missing,
    we are root, or access is fine).
    """
    path = path or SOCKET_PATH
    if os.geteuid() == 0:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    # connect() needs write permission on the socket file.
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
    """Write text atomically: temp file -> fsync -> os.replace().

    A direct ``open(path, "w")`` is not atomic: a power loss or crash midway
    through the write leaves a truncated file behind. For the TrafficToll YAML
    that makes ``tt`` render a broken config; for ``config.toml`` it used to
    even prevent the daemon from starting.

    ``os.replace`` is atomic on POSIX (same filesystem assumed), so a reader sees
    either the old or the new file — never an intermediate state. ``mode`` sets
    the permissions explicitly (the caller's umask must not silently push the
    config down to 0600 when the daemon rewrites it as root and the GUI must
    still read it as the user — or vice versa).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # Create the temp file in the TARGET directory, otherwise os.replace is not
    # atomic (EXDEV across filesystem boundaries).
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
        # Never leave a half-written temp file behind.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Make the directory entry durable (best effort; some filesystems cannot,
    # e.g. certain overlay/FUSE setups — then it is not an error).
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
