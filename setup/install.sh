#!/usr/bin/env bash
set -euo pipefail

# Throtl-Installation fuer Omarchy (Arch Linux + Hyprland, Wayland).
# - Installiert Systempakete: nethogs, gtk4, libadwaita, python-gobject, python-cairo
# - Installiert TrafficToll (pip) in ein venv unter /opt/netlimiter-clone
# - Legt systemd-Service, .desktop-Datei, Icon an
# - Richtet Autostart ein
#
# AUSFUEHREN NUR ALS ROOT/sudo:  sudo ./install.sh

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SELF_DIR")"
OPT="/opt/netlimiter-clone"
ETC="/etc/netlimiter-clone"
RUN="/run/netlimiter-clone"

# AUR-Helper auswaehlen
AUR_HELPER=""
for helper in paru yay; do
  if command -v "$helper" >/dev/null 2>&1; then
    AUR_HELPER="$helper"
    break
  fi
done

echo "=== [1/6] Systempakete installieren ==="
# pacman-Pakete (alle in [extra])
sudo pacman -S --needed --noconfirm \
  nethogs gtk4 libadwaita python-gobject python-cairo

echo "=== [2/6] TrafficToll in venv installieren ==="
sudo mkdir -p "$OPT"
if [ ! -x "$OPT"/venv/bin/python ]; then
  sudo /usr/bin/python3 -m venv "$OPT/venv"
fi
# pip fuer das venv bereitstellen (falls fehlt)
if ! "$OPT/venv/bin/python" -c "import pip" >/dev/null 2>&1; then
  sudo "$OPT/venv/bin/python" -m ensurepip --upgrade || true
fi
sudo "$OPT/venv/bin/pip" install --upgrade pip
sudo "$OPT/venv/bin/pip" install traffictoll

echo "=== [3/6] Projektdateien kopieren ==="
sudo cp -r "$PROJECT_DIR"/throtl "$OPT/"
sudo mkdir -p "$OPT/bin"
sudo cp "$PROJECT_DIR"/bin/throtl-gui "$PROJECT_DIR"/bin/throtl-cli \
  "$PROJECT_DIR"/bin/throtl-daemon "$OPT/bin/"
sudo chmod +x "$OPT/bin"/throtl-*

echo "=== [4/6] Konfiguration + Runtime-Verzeichnisse ==="
sudo mkdir -p "$ETC" "$RUN"
if [ ! -f "$ETC/config.toml" ]; then
  sudo install -o root -g root -m 0644 "$SELF_DIR"/default-config.toml "$ETC/config.toml"
fi

echo "=== [5/6] systemd-Service installieren ==="
sudo install -m 0644 "$SELF_DIR"/netlimiter-clone.service /etc/systemd/system/
sudo systemctl daemon-reload
# Restart-Zaehler leeren (falls die Unit zuvor in einer Start-Loop steckte)
sudo systemctl reset-failed netlimiter-clone 2>/dev/null || true
sudo systemctl enable netlimiter-clone
sudo systemctl restart netlimiter-clone
echo "   Daemon-Service: netlimiter-clone  (Status: systemctl status netlimiter-clone)"

echo "=== [6/6] Desktop-Datei + Icon + Autostart ==="
sudo install -Dm 0755 -d /usr/share/applications
sudo install -m 0644 "$SELF_DIR"/throtl.desktop /usr/share/applications/throtl.desktop

sudo install -Dm 0644 "$PROJECT_DIR"/data/hicolor/scalable/apps/throtl.svg \
  /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

# AUR-Helper notfalls installieren (falls keiner vorhanden): nicht erzwungen
if [ -z "$AUR_HELPER" ]; then
  echo "   Hinweis: Kein AUR-Helper (paru/yay) gefunden. TrafficToll wurde ueber pip "
  echo "   installiert, kein AUR-Paket noetig."
fi

echo
echo " FERTIG. Throtl ist installiert."
echo "   CLI:     /opt/netlimiter-clone/bin/throtl-cli status"
echo "   GUI:     Starte 'Throtl' im App-Menue (Quickshell/wofi/rofi)"
echo "   Uninstall: sudo ./setup/uninstall.sh"
