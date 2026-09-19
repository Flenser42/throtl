"""Throtl — Bandbreiten-Limits und QoS pro Anwendung fuer Linux.

Architektur: privilegierter Daemon (root, systemd) mit TrafficToll-Backend,
GTK4/libadwaita-Frontend, Unix-Socket-IPC. Konfiguration unter
~/.config/throtl/ (TOML).
"""

__version__ = "0.1.0"

# Konfigurationsverzeichnis (~/.config/throtl)
CONFIG_DIR_NAME = "throtl"

# Laufzeit-Verzeichnis (Socket, generierte YAML, tt-Log)
RUN_DIR = "/run/throtl"
SOCKET_PATH = f"{RUN_DIR}/daemon.sock"
