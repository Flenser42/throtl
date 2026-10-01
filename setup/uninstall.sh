#!/usr/bin/env bash
set -euo pipefail

# Throtl-Deinstallation (Services stoppen/entfernen, Desktop-Datei, Icon, /etc).
# Die gesetzten Limits-Config in /etc/throtl UND den venv-Code
# in /opt/throtl entfernt nur auf Wunsch (flag --purge).
#
#   sudo ./setup/uninstall.sh            # entfernt Service, Desktop, Icon, aber NICHT Code/Config
#   sudo ./setup/uninstall.sh --purge    # entfernt auch /opt/throtl und /etc/throtl

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Stoppe + entferne systemd-Service ==="
sudo systemctl disable --now throtl 2>/dev/null || true
sudo rm -f /etc/systemd/system/throtl.service
sudo systemctl daemon-reload

echo "=== Entferne Desktop-Datei + Icon ==="
sudo rm -f /usr/share/applications/throtl.desktop
sudo rm -f /usr/share/applications/throtl-classic.desktop
sudo rm -f /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

# Die Dashboard-Installation liegt pro Nutzer unter ~/.local (install-app.sh).
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  if [ -n "$USER_HOME" ] && [ -d "$USER_HOME/.local" ]; then
    echo "=== Entferne Dashboard-Installation (Nutzer $SUDO_USER) ==="
    sudo rm -rf "$USER_HOME/.local/opt/throtl"
    sudo rm -f "$USER_HOME/.local/bin/throtl-app"
    sudo rm -f "$USER_HOME/.local/share/applications/throtl-app.desktop"
    sudo rm -f "$USER_HOME/.local/share/applications/throtl.desktop"
    sudo rm -f "$USER_HOME/.local/share/icons/hicolor/scalable/apps/throtl-app.svg"
  fi
fi

echo "=== Entferne PATH-Launcher ==="
sudo rm -f /usr/local/bin/throtl-cli /usr/local/bin/throtl-gui /usr/local/bin/throtl-daemon

if [ "${1:-}" == "--purge" ]; then
  echo "=== Purge: entferne Code + Config ==="
  sudo rm -rf /opt/throtl /etc/throtl /run/throtl
fi

echo "Throtl deinstalliert."
