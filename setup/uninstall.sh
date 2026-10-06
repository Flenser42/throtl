#!/usr/bin/env bash
set -euo pipefail

# Throtl uninstall (stop/remove services, desktop file, icon, /etc).
# The configured limits in /etc/throtl AND the venv code in /opt/throtl
# are only removed on request (flag --purge).
#
#   sudo ./setup/uninstall.sh            # removes service, desktop, icon, but NOT code/config
#   sudo ./setup/uninstall.sh --purge    # also removes /opt/throtl and /etc/throtl

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Stop + remove systemd service ==="
sudo systemctl disable --now throtl 2>/dev/null || true
sudo rm -f /etc/systemd/system/throtl.service
sudo systemctl daemon-reload

echo "=== Remove desktop file + icon ==="
sudo rm -f /usr/share/applications/throtl.desktop
sudo rm -f /usr/share/applications/throtl-classic.desktop
sudo rm -f /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

# The dashboard install lives per user under ~/.local (install-app.sh).
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  if [ -n "$USER_HOME" ] && [ -d "$USER_HOME/.local" ]; then
    echo "=== Remove dashboard installation (user $SUDO_USER) ==="
    sudo rm -rf "$USER_HOME/.local/opt/throtl"
    sudo rm -f "$USER_HOME/.local/bin/throtl-app"
    sudo rm -f "$USER_HOME/.local/share/applications/throtl-app.desktop"
    sudo rm -f "$USER_HOME/.local/share/applications/throtl.desktop"
    sudo rm -f "$USER_HOME/.local/share/icons/hicolor/scalable/apps/throtl-app.svg"
  fi
fi

echo "=== Remove PATH launchers ==="
sudo rm -f /usr/local/bin/throtl-cli /usr/local/bin/throtl-gui /usr/local/bin/throtl-daemon

echo "=== Remove polkit setup (helper + policy) ==="
sudo rm -f /usr/local/bin/throtl-setup
sudo rm -f /usr/share/polkit-1/actions/org.throtl.setup.policy

if [ "${1:-}" == "--purge" ]; then
  echo "=== Purge: remove code + config ==="
  sudo rm -rf /opt/throtl /etc/throtl /run/throtl
fi

echo "Throtl uninstalled."
