#!/usr/bin/env bash
set -euo pipefail

# Throtl installation for Omarchy (Arch Linux + Hyprland, Wayland).
# - Installs system packages: nethogs, webkit2gtk-4.1 (for the dashboard)
# - Installs TrafficToll (pip) into a venv under /opt/throtl
# - Creates the systemd service, cleans up old GTK entries
# - Optionally installs the Tauri dashboard (step 7)
#
# RUN ONLY AS ROOT/sudo:  sudo ./install.sh

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SELF_DIR")"
OPT="/opt/throtl"
ETC="/etc/throtl"
RUN="/run/throtl"

# Options: --no-app skips the GUI, --with-waybar installs the Waybar status module.
WITH_WAYBAR=0
NO_APP=0
for arg in "$@"; do
  case "$arg" in
    --with-waybar) WITH_WAYBAR=1 ;;
    --no-app)      NO_APP=1 ;;
  esac
done

echo "=== [1/7] Install system packages ==="
# pacman packages (all in [extra]): nethogs provides the live measurement,
# iproute2 provides 'tc', webkit2gtk-4.1 is the dashboard's runtime.
if command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed --noconfirm nethogs webkit2gtk-4.1 iproute2
else
  echo "   No pacman found (not Arch/Omarchy). Please install first:"
  echo "     nethogs, iproute2 (tc), webkit2gtk-4.1 (dashboard runtime)"
fi

echo "=== [2/7] Install TrafficToll into a venv ==="
sudo mkdir -p "$OPT"
if [ ! -x "$OPT"/venv/bin/python ]; then
  sudo /usr/bin/python3 -m venv "$OPT/venv"
fi
# Provide pip for the venv (if missing)
if ! "$OPT/venv/bin/python" -c "import pip" >/dev/null 2>&1; then
  sudo "$OPT/venv/bin/python" -m ensurepip --upgrade || true
fi
sudo "$OPT/venv/bin/pip" install --upgrade pip
sudo "$OPT/venv/bin/pip" install traffictoll==1.5.0

echo "=== [3/7] Copy project files ==="
# Remove the old copy so no stale modules/__pycache__ linger.
sudo rm -rf "$OPT/throtl"
sudo cp -r "$PROJECT_DIR/throtl" "$OPT/"
sudo find "$OPT/throtl" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
sudo install -m 0644 "$PROJECT_DIR/README.md" "$PROJECT_DIR/CHANGELOG.md" \
  "$PROJECT_DIR/LICENSE" "$OPT/"
sudo mkdir -p "$OPT/bin"
sudo cp "$PROJECT_DIR"/bin/throtl-cli "$PROJECT_DIR"/bin/throtl-daemon "$OPT/bin/"
sudo chmod +x "$OPT/bin"/throtl-*
echo "   Link launchers to /usr/local/bin/throtl-* (PATH)"
sudo ln -sf "$OPT/bin/throtl-cli"    /usr/local/bin/throtl-cli
sudo ln -sf "$OPT/bin/throtl-daemon" /usr/local/bin/throtl-daemon

echo "=== [4/7] Group, configuration + runtime directories ==="
# Group 'throtl': only its members may access the daemon socket
# (0660, see Daemon._secure_socket). Idempotent: if the group already exists,
# existing members are kept.
if ! getent group throtl >/dev/null 2>&1; then
  sudo groupadd --system throtl
fi
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  if ! id -nG "$SUDO_USER" 2>/dev/null | tr ' ' '\n' | grep -qx throtl; then
    sudo usermod -aG throtl "$SUDO_USER"
    echo "   Added $SUDO_USER to group 'throtl'."
  fi
  echo "   Important: groups only apply in a NEW session. If 'throtl-cli'"
  echo "   reports 'Permission denied', log out and back in once, or run"
  echo "   'newgrp throtl' in a terminal."
fi
sudo mkdir -p "$ETC" "$RUN"
if [ ! -f "$ETC/config.toml" ]; then
  sudo install -o root -g root -m 0644 "$SELF_DIR"/default-config.toml "$ETC/config.toml"
fi

echo "=== [5/7] Install systemd service ==="
sudo install -m 0644 "$SELF_DIR"/throtl.service /etc/systemd/system/
sudo systemctl daemon-reload
# Clear the restart counter (in case the unit was stuck in a start loop before)
sudo systemctl reset-failed throtl 2>/dev/null || true
sudo systemctl enable throtl
# Do not abort via set -e if the daemon fails to start: otherwise the desktop
# file/icon/autostart (step 6) would never run and the installation would
# remain half-finished.
if ! sudo systemctl restart throtl; then
  echo "   Warning: daemon does not start. Check the cause:"
  echo "            journalctl -u throtl -n 50 --no-pager"
fi
echo "   Daemon service: throtl  (status: systemctl status throtl)"

echo "=== [6/7] Clean up old GTK entries ==="
# Older installations left behind the GTK GUI: remove the menu entry, launcher
# and icon so the menu only shows the dashboard.
sudo rm -f /usr/share/applications/throtl.desktop \
           /usr/share/applications/throtl-classic.desktop \
           /usr/local/bin/throtl-gui \
           /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo rm -rf "$OPT/throtl/gui" "$OPT/bin/throtl-gui"
# Update the desktop database so launchers see the removal immediately.
if command -v update-desktop-database >/dev/null 2>&1; then
  sudo update-desktop-database /usr/share/applications 2>/dev/null || true
fi

if ! command -v paru >/dev/null 2>&1 && ! command -v yay >/dev/null 2>&1; then
  echo "   Note: no AUR helper (paru/yay) found. TrafficToll was installed via pip;"
  echo "   no AUR package needed."
fi

echo "=== [7/7] Install the new GUI (Tauri) ==="
APP_USER="${SUDO_USER:-$USER}"
if [[ "$NO_APP" -eq 1 ]]; then
  echo "   skipped (--no-app)"
elif [[ "$APP_USER" == "root" ]]; then
  echo "   Called as root - GUI skipped."
  echo "   Run without sudo later: ./setup/install-app.sh"
elif sudo -u "$APP_USER" -H bash "$SELF_DIR/install-app.sh"; then
  echo "   GUI installed: 'throtl-app' or 'Throtl' in the app menu."
else
  echo "   Warning: GUI installation failed (are Node/Rust present?)."
  echo "   Try again later: ./setup/install-app.sh"
fi

echo "=== [8/8] Install polkit action for GUI setup ==="
# Lets the dashboard trigger the one-time setup (the 'throtl' group and, from
# a checkout, the reinstall) via `pkexec` without a terminal. The policy file
# references the helper under its installed path, so pkexec uses the
# org.throtl.setup action (auth_admin) instead of the generic fallback action
# org.freedesktop.policykit.exec.
sudo install -m 0755 "$SELF_DIR"/throtl-setup /usr/local/bin/throtl-setup
sudo mkdir -p /usr/share/polkit-1/actions
sudo install -m 0644 "$SELF_DIR"/polkit/org.throtl.setup.policy \
  /usr/share/polkit-1/actions/org.throtl.setup.policy
echo "   The GUI can trigger the setup via 'pkexec /usr/local/bin/throtl-setup'."

if [[ "$WITH_WAYBAR" -eq 1 ]]; then
  echo
  echo "=== Waybar status module ==="
  WB_USER="${SUDO_USER:-$USER}"
  if [[ "$WB_USER" == "root" ]]; then
    WB_HOME="${HOME:-/root}"
  else
    WB_HOME="$(getent passwd "$WB_USER" | cut -d: -f6)"
  fi
  if [[ -n "$WB_HOME" ]] && mkdir -p "$WB_HOME/.local/bin"; then
    install -m 0755 "$SELF_DIR/waybar/throtl.sh" "$WB_HOME/.local/bin/throtl-waybar"
    if [[ "$WB_USER" != "root" ]]; then
      chown "$WB_USER" "$WB_HOME/.local/bin" "$WB_HOME/.local/bin/throtl-waybar" 2>/dev/null || true
    fi
    echo "   Installed: $WB_HOME/.local/bin/throtl-waybar"
    echo "   Add this module to your Waybar config (modules-right etc.):"
    echo
    cat <<'EOF'
    "custom/throtl": {
        "exec": "~/.local/bin/throtl-waybar",
        "interval": 2,
        "return-type": "json",
        "on-click": "throtl-cli toggle"
    },
EOF
    echo
  else
    echo "   Warning: could not create ~/.local/bin — skipping the Waybar module."
  fi
fi

echo
echo " DONE. Throtl is installed."
echo "   CLI:     /opt/throtl/bin/throtl-cli status"
echo "   GUI:     'throtl-app' or 'Throtl' in the app menu (dashboard)"
echo "   Uninstall: sudo ./setup/uninstall.sh"
echo "   Reinstall only the GUI: ./setup/install-app.sh"
