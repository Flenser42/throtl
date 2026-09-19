"""Throtl — Bandbreiten-Limits und QoS pro Anwendung fuer Linux.

Architektur: privilegierter Daemon (root, systemd) mit TrafficToll-Backend,
GTK4/libadwaita-Frontend, Unix-Socket-IPC. Konfiguration unter
~/.config/throtl/ (TOML).
"""

__version__ = "0.1.0"

APP_NAME = "Throtl"
APP_ID = "throtl"

# Konfigurationsverzeichnis (wie spezifiziert: ~/.config/throtl/)
CONFIG_DIR_NAME = "throtl"

# Laufzeit-Verzeichnisse
RUN_DIR = "/run/throtl"
SOCKET_PATH = f"{RUN_DIR}/daemon.sock"
STATE_DIR = RUN_DIR

# Installationsziel des Setup-Skripts (venv fuer TrafficToll)
OPT_DIR = "/opt/throtl"
