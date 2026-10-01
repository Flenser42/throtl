#!/usr/bin/env bash
set -euo pipefail

# Throtl-Installation fuer Omarchy (Arch Linux + Hyprland, Wayland).
# - Installiert Systempakete: nethogs, webkit2gtk-4.1 (fuer das Dashboard)
# - Installiert TrafficToll (pip) in ein venv unter /opt/throtl
# - Legt systemd-Service an, raeumt alte GTK-Eintraege auf
# - Installiert optional das Tauri-Dashboard (Schritt 7)
#
# AUSFUEHREN NUR ALS ROOT/sudo:  sudo ./install.sh

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SELF_DIR")"
OPT="/opt/throtl"
ETC="/etc/throtl"
RUN="/run/throtl"

echo "=== [1/7] Systempakete installieren ==="
# pacman-Pakete (alle in [extra]): nethogs liefert die Live-Messung,
# webkit2gtk-4.1 ist die Laufzeit des Tauri-Dashboards.
sudo pacman -S --needed --noconfirm nethogs webkit2gtk-4.1

echo "=== [2/7] TrafficToll in venv installieren ==="
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

echo "=== [3/7] Projektdateien kopieren ==="
# Alte Kopie entfernen, damit keine veralteten Module/__pycache__ liegen bleiben.
sudo rm -rf "$OPT/throtl"
sudo cp -r "$PROJECT_DIR/throtl" "$OPT/"
sudo find "$OPT/throtl" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
sudo install -m 0644 "$PROJECT_DIR/README.md" "$PROJECT_DIR/CHANGELOG.md" \
  "$PROJECT_DIR/LICENSE" "$OPT/"
sudo mkdir -p "$OPT/bin"
sudo cp "$PROJECT_DIR"/bin/throtl-cli "$PROJECT_DIR"/bin/throtl-daemon "$OPT/bin/"
sudo chmod +x "$OPT/bin"/throtl-*
echo "   Launcher als /usr/local/bin/throtl-* (PATH) verlinken"
sudo ln -sf "$OPT/bin/throtl-cli"    /usr/local/bin/throtl-cli
sudo ln -sf "$OPT/bin/throtl-daemon" /usr/local/bin/throtl-daemon

echo "=== [4/7] Gruppe, Konfiguration + Runtime-Verzeichnisse ==="
# Gruppe 'throtl': nur ihre Mitglieder duerfen den Daemon-Socket ansprechen
# (0660, siehe Daemon._secure_socket). Idempotent: existiert die Gruppe schon,
# bleiben bestehende Mitglieder erhalten.
if ! getent group throtl >/dev/null 2>&1; then
  sudo groupadd --system throtl
fi
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  if ! id -nG "$SUDO_USER" 2>/dev/null | tr ' ' '\n' | grep -qx throtl; then
    sudo usermod -aG throtl "$SUDO_USER"
    echo "   $SUDO_USER zur Gruppe 'throtl' hinzugefuegt."
  fi
  echo "   Wichtig: Gruppen gelten erst in einer NEUEN Session. Meldet"
  echo "   'throtl-cli' 'Permission denied', einmal neu einloggen oder im"
  echo "   Terminal 'newgrp throtl' ausfuehren."
fi
sudo mkdir -p "$ETC" "$RUN"
if [ ! -f "$ETC/config.toml" ]; then
  sudo install -o root -g root -m 0644 "$SELF_DIR"/default-config.toml "$ETC/config.toml"
fi

echo "=== [5/7] systemd-Service installieren ==="
sudo install -m 0644 "$SELF_DIR"/throtl.service /etc/systemd/system/
sudo systemctl daemon-reload
# Restart-Zaehler leeren (falls die Unit zuvor in einer Start-Loop steckte)
sudo systemctl reset-failed throtl 2>/dev/null || true
sudo systemctl enable throtl
# Nicht mit set -e abbrechen, wenn der Daemon nicht startet: sonst laufen
# Desktop-Datei/Icon/Autostart (Schritt 6) nie durch und die Installation
# bleibt halbfertig.
if ! sudo systemctl restart throtl; then
  echo "   Warnung: Daemon startet nicht. Ursache pruefen:"
  echo "            journalctl -u throtl -n 50 --no-pager"
fi
echo "   Daemon-Service: throtl  (Status: systemctl status throtl)"

echo "=== [6/7] Alte GTK-Eintraege aufraeumen ==="
# Aeltere Installationen hinterliessen die GTK-GUI: Menueeintrag, Launcher und
# Icon entfernen, damit das Menue nur noch das Dashboard zeigt.
sudo rm -f /usr/share/applications/throtl.desktop \
           /usr/share/applications/throtl-classic.desktop \
           /usr/local/bin/throtl-gui \
           /usr/share/icons/hicolor/scalable/apps/throtl.svg
sudo rm -rf "$OPT/throtl/gui" "$OPT/bin/throtl-gui"
# Desktop-Datenbank aktualisieren, damit Launcher den Wegfall sofort sehen.
if command -v update-desktop-database >/dev/null 2>&1; then
  sudo update-desktop-database /usr/share/applications 2>/dev/null || true
fi

if ! command -v paru >/dev/null 2>&1 && ! command -v yay >/dev/null 2>&1; then
  echo "   Hinweis: Kein AUR-Helper (paru/yay) gefunden. TrafficToll wurde ueber pip "
  echo "   installiert, kein AUR-Paket noetig."
fi

echo "=== [7/7] Neue GUI (Tauri) installieren ==="
APP_USER="${SUDO_USER:-$USER}"
if [[ "${1:-}" == "--no-app" ]]; then
  echo "   uebersprungen (--no-app)"
elif [[ "$APP_USER" == "root" ]]; then
  echo "   Als root aufgerufen - GUI uebersprungen."
  echo "   Ohne sudo nachholen: ./setup/install-app.sh"
elif sudo -u "$APP_USER" -H bash "$SELF_DIR/install-app.sh"; then
  echo "   GUI installiert: 'throtl-app' bzw. 'Throtl' im App-Menue."
else
  echo "   Warnung: GUI-Installation fehlgeschlagen (Node/Rust vorhanden?)."
  echo "   Spaeter erneut: ./setup/install-app.sh"
fi

echo
echo " FERTIG. Throtl ist installiert."
echo "   CLI:     /opt/throtl/bin/throtl-cli status"
echo "   GUI:     'throtl-app' bzw. 'Throtl' im App-Menue (Dashboard)"
echo "   Uninstall: sudo ./setup/uninstall.sh"
echo "   Nur GUI neu installieren: ./setup/install-app.sh"
