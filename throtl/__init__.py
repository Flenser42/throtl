"""Throtl — NetLimiter-artige Bandbreiten-Limits & QoS fuer Linux.

Architektur: privilegierter Daemon (root, systemd) mit TrafficToll-Backend,
GTK4/libadwaita-Frontend, Unix-Socket-IPC. Konfiguration unter
~/.config/netlimiter-clone/ (TOML).
"""

__version__ = "0.1.0"

APP_NAME = "Throtl"
APP_ID = "throtl"

# Konfigurationsverzeichnis (wie spezifiziert: ~/.config/netlimiter-clone/)
CONFIG_DIR_NAME = "netlimiter-clone"

# Laufzeit-Verzeichnisse
RUN_DIR = "/run/netlimiter-clone"
SOCKET_PATH = f"{RUN_DIR}/daemon.sock"
STATE_DIR = RUN_DIR

# Installationsziel des Setup-Skripts (venv fuer TrafficToll)
OPT_DIR = "/opt/netlimiter-clone"
