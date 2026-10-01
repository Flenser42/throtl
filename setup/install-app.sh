#!/usr/bin/env bash
set -euo pipefail

# Install the new Tauri GUI (throtl-app) for the CURRENT USER — no root needed.
# If no AppImage has been built yet it is built once (needs Node + Rust);
# afterwards it is installed per-user under ~/.local and shows up in the
# application menu as "Throtl".
#
#   ./setup/install-app.sh          # normal (builds if needed)
#   ./setup/install-app.sh --no-build   # fail instead of building

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SELF_DIR")"
APP_DIR="$PROJECT_DIR/throtl-app"
BUNDLE_DIR="$APP_DIR/src-tauri/target/release/bundle"
# rustup/cargo and pnpm live in user dirs.
export PATH="$HOME/.cargo/bin:$HOME/.local/share/pnpm:$PATH"

if [[ ! -d "$APP_DIR" ]]; then
  echo "Fehler: throtl-app/ nicht gefunden ($APP_DIR)"
  exit 1
fi

find_appimage() {
  ls -1 "$BUNDLE_DIR"/appimage/*.AppImage 2>/dev/null | head -1 || true
}

APPIMAGE="$(find_appimage)"
if [[ -z "$APPIMAGE" ]]; then
  if [[ "${1:-}" == "--no-build" ]]; then
    echo "Kein AppImage vorhanden und --no-build gesetzt."
    exit 1
  fi
  echo "Baue die GUI (einmalig, ~2–3 Min) …"
  command -v npm   >/dev/null || { echo "npm fehlt (Node installieren)."; exit 1; }
  command -v cargo >/dev/null || { echo "cargo fehlt (rustup installieren, siehe README)."; exit 1; }
  ( cd "$APP_DIR" && npm ci --no-audit --no-fund && npm run tauri build )
  APPIMAGE="$(find_appimage)"
fi
[[ -n "$APPIMAGE" ]] || { echo "Build fehlgeschlagen."; exit 1; }

PREFIX="$HOME/.local"
OPT="$PREFIX/opt/throtl"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
mkdir -p "$OPT" "$BIN" "$APPS" "$ICONS"

install -m 0755 "$APPIMAGE" "$OPT/Throtl.AppImage"

cat > "$BIN/throtl-app" <<EOF
#!/usr/bin/env sh
exec "$OPT/Throtl.AppImage" "\$@"
EOF
chmod 0755 "$BIN/throtl-app"

if [[ -f "$PROJECT_DIR/data/hicolor/scalable/apps/throtl.svg" ]]; then
  install -m 0644 "$PROJECT_DIR/data/hicolor/scalable/apps/throtl.svg" \
    "$ICONS/throtl-app.svg"
fi

cat > "$APPS/throtl-app.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Throtl
GenericName=Bandwidth control
Comment=Per-application bandwidth limits and traffic prioritisation
Exec=$BIN/throtl-app
Icon=throtl-app
Terminal=false
Categories=Network;System;Monitor;
StartupNotify=true
EOF

# Older installs shipped a second "Throtl" entry for the GTK GUI; remove it so
# the menu shows a single "Throtl".
rm -f "$APPS/throtl.desktop"

command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Hinweis: '$BIN' liegt nicht im PATH — z. B. in der Shell-Config ergänzen." ;;
esac

echo "GUI installiert:"
echo "   Start:  throtl-app   (oder 'Throtl' im App-Menü)"
echo "   Datei:  $OPT/Throtl.AppImage"
