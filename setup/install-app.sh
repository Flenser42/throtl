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
  echo "Error: throtl-app/ not found ($APP_DIR)"
  exit 1
fi

# The version to install (single source: the Tauri config).
VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$APP_DIR/src-tauri/tauri.conf.json" | head -1)"
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read version from tauri.conf.json."
  exit 1
fi

# Only accept a bundle of THIS version: otherwise a leftover AppImage of an old
# version would be installed (exactly what happened with 0.1.0).
find_appimage() {
  local candidate
  for candidate in "$BUNDLE_DIR/appimage/"*_"$VERSION"_*.AppImage; do
    [[ -f "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 0
}

# Reuse the cache only when every source file is older than the AppImage.
# A pull that adds features without a version bump would otherwise install a
# stale UI — the version string alone is not a sufficient freshness signal.
needs_rebuild() {
  local appimage="$1"
  [[ -n "$(find "$APP_DIR" \
    \( -name node_modules -o -name target -o -name dist \) -prune -o \
    -type f -newer "$appimage" -print -quit 2>/dev/null)" ]]
}

# Build the AppImage with a live spinner and elapsed time. The Rust
# compilation can take several minutes and must never look frozen — a user
# staring at a silent terminal otherwise assumes it hung and kills it.
# `--bundles appimage` skips the deb/rpm bundlers (which need dpkg-deb and
# rpmbuild, not present on Arch/Omarchy) and only produces what is installed.
build_appimage() {
  local log pid start elapsed phase pct total_crates compiled_crates width filled j
  log="$(mktemp)"
  start=$SECONDS

  # Cargo.lock's package list is the denominator for the Rust-phase percentage.
  total_crates="$(grep -c '^\[\[package\]\]' "$APP_DIR/src-tauri/Cargo.lock" 2>/dev/null || true)"
  if (( total_crates < 1 )); then total_crates=1; fi

  (
    cd "$APP_DIR" || exit 1
    npm ci --no-audit --no-fund
    npm run tauri build -- --bundles appimage
  ) > "$log" 2>&1 &
  pid=$!

  width=28
  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$(( SECONDS - start ))
    if grep -q 'Bundling ' "$log" 2>/dev/null; then
      phase="bundling AppImage"; pct=96
    elif grep -q 'Compiling ' "$log" 2>/dev/null; then
      compiled_crates="$(grep -cE '^(Compiling|Fresh) ' "$log" 2>/dev/null || true)"
      compiled_crates="${compiled_crates:-0}"
      pct=$(( 10 + compiled_crates * 85 / total_crates ))
      if (( pct > 92 )); then pct=92; fi
      phase="compiling Rust ($compiled_crates/$total_crates)"
    elif grep -qE 'added [0-9]+|up to date|building for production' "$log" 2>/dev/null; then
      phase="building frontend"; pct=8
    else
      phase="installing deps"; pct=2
    fi
    filled=$(( pct * width / 100 ))
    printf '\r   ['
    for (( j=0; j<width; j++ )); do
      if (( j < filled )); then printf '='; else printf ' '; fi
    done
    printf '] %3d%%  %-26s  (%dm %02ds)   ' \
      "$pct" "$phase" "$(( elapsed / 60 ))" "$(( elapsed % 60 ))"
    sleep 0.2
  done

  if wait "$pid"; then
    printf '\r   GUI build done (%dm %02ds).\n' \
      "$(((SECONDS - start) / 60))" "$(((SECONDS - start) % 60))"
    rm -f "$log"
    return 0
  fi
  printf '\r   GUI build FAILED — last output:\n'
  tail -n 30 "$log" >&2
  rm -f "$log"
  return 1
}

APPIMAGE="$(find_appimage)"
rebuild_reason=""
if [[ -n "$APPIMAGE" ]] && needs_rebuild "$APPIMAGE"; then
  rebuild_reason=" (source changed since it was built)"
  APPIMAGE=""
fi
if [[ -z "$APPIMAGE" ]]; then
  if [[ "${1:-}" == "--no-build" ]]; then
    echo "No up-to-date AppImage for version $VERSION present and --no-build set."
    echo "Present: $(ls -1 "$BUNDLE_DIR"/appimage/*.AppImage 2>/dev/null | tr '\n' ' ')"
    exit 1
  fi
  echo "Building the GUI for version $VERSION${rebuild_reason} (first time: 5–15 min) …"
  command -v npm   >/dev/null || { echo "npm missing (install Node)."; exit 1; }
  command -v cargo >/dev/null || { echo "cargo missing (install rustup, see README)."; exit 1; }
  build_appimage
  APPIMAGE="$(find_appimage)"
fi
[[ -n "$APPIMAGE" ]] || { echo "Build failed (no AppImage for $VERSION)."; exit 1; }

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
  *) echo "Note: '$BIN' is not on the PATH — e.g. add it in your shell config." ;;
esac

echo "GUI installed:"
echo "   Start:  throtl-app   (or 'Throtl' in the app menu)"
echo "   File:   $OPT/Throtl.AppImage"
