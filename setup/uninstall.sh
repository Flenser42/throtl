#!/usr/bin/env bash
set -euo pipefail

# Throtl-Deinstallation (Services stoppen/entfernen, Desktop-Datei, Icon, /etc).
# Die gesetzten Limits-Config in /etc/netlimiter-clone UND den venv-Code
# in /opt/netlimiter-clone entfernt nur auf Wunsch (flag --purge).
#
#   sudo ./setup/uninstall.sh            # entfernt Service, Desktop, Icon, aber NICHT Code/Config
#   sudo ./setup/uninstall.sh --purge    # entfernt auch /opt/netlimiter-clone und /etc/netlimiter-clone

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Stoppe + entferne systemd-Service ==="
sudo systemctl disable --now netlimiter-clone 2>/dev/null || true
sudo rm -f /etc/systemd/system/netlimiter-clone.service
sudo systemctl daemon-reload

echo "=== Entferne Desktop-Datei + Icon ==="
sudo rm -f /usr/share/applications/throtl.desktop
sudo rm -f /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

if [ "${1:-}" == "--purge" ]; then
  echo "=== Purge: entferne Code + Config ==="
  sudo rm -rf /opt/netlimiter-clone /etc/netlimiter-clone /run/netlimiter-clone
fi

echo "Throtl deinstalliert."
