#!/usr/bin/env bash
set -euo pipefail

# Install the new Tauri GUI (throtl-app) for the CURRENT USER — no root needed.
# If no AppImage has been built yet it is built once (needs Node + Rust);
# afterwards it is installed per-user under ~/.local and shows up in the
# application menu as "Throtl".
#
#   ./setup/install-app.sh          # normal (builds if needed)
#   ./setup/install-app.sh --no-build   # fail instead of building

# ---------------------------------------------------------------------------
# Terminal styling. Colours are used only when stdout is an interactive TTY
# that supports them (and NO_COLOR is unset). Piped/redirected output — CI,
# `| tee install.log` — stays byte-clean plain text (no escape codes).
# ---------------------------------------------------------------------------
if [[ -t 1 && -n "${TERM:-}" && "$TERM" != "dumb" && -z "${NO_COLOR:-}" ]]; then
  C_CYAN=$'\033[1;36m';  C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'
  C_RED=$'\033[1;31m';   C_DIM=$'\033[2m';      C_RESET=$'\033[0m'
  C_EL=$'\033[K'
else
  C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_RESET=''; C_EL=''
fi

step() { printf '\n%s%s%s\n' "$C_CYAN" "$1" "$C_RESET" || true; }
ok()   { printf '   %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1" || true; }
warn() { printf '   %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1" || true; }
err()  { printf '\n%s✗%s %s\n' "$C_RED" "$C_RESET" "$1" || true; }
dim()  { printf '   %s%s%s\n' "$C_DIM" "$1" "$C_RESET" || true; }

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SELF_DIR")"
APP_DIR="$PROJECT_DIR/throtl-app"
BUNDLE_DIR="$APP_DIR/src-tauri/target/release/bundle"
# rustup/cargo, pnpm and mise (Node) live in user dirs; install.sh may run this
# through `sudo -u` with a minimal secure_path, so add them explicitly.
export PATH="$HOME/.cargo/bin:$HOME/.local/share/pnpm:$HOME/.local/share/mise/shims:$HOME/.local/bin:$PATH"

if [[ ! -d "$APP_DIR" ]]; then
  err "throtl-app/ not found ($APP_DIR)"
  exit 1
fi

# The version to install (single source: the Tauri config).
VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$APP_DIR/src-tauri/tauri.conf.json" | head -1)"
if [[ -z "$VERSION" ]]; then
  err "could not read version from tauri.conf.json"
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

# Build the AppImage with a live progress bar and elapsed time. The Rust
# compilation can take several minutes and must never look frozen — a user
# staring at a silent terminal otherwise assumes it hung and kills it.
# `--bundles appimage` skips the deb/rpm bundlers (which need dpkg-deb and
# rpmbuild, not present on Arch/Omarchy) and only produces what is installed.
build_appimage() {
  local log pid start elapsed phase pct total_crates compiled_crates width filled j
  local bar filled_str empty_str last_phase
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
  # Pre-render the filled/empty block strings once so the bar stays a fixed width.
  filled_str=""; empty_str=""
  for (( j=0; j<width; j++ )); do filled_str+="█"; empty_str+="░"; done

  last_phase=""
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
    if [[ -t 1 ]]; then
      filled=$(( pct * width / 100 ))
      bar="${filled_str:0:$filled}${empty_str:0:$(( width - filled ))}"
      printf '\r   [%s] %3d%%  %-28s  (%dm %02ds)%s' \
        "$bar" "$pct" "$phase" "$(( elapsed / 60 ))" "$(( elapsed % 60 ))" "$C_EL"
    elif [[ "$phase" != "$last_phase" ]]; then
      printf '   %s\n' "$phase"
      last_phase="$phase"
    fi
    sleep 0.2
  done
  [[ -t 1 ]] && printf '\n'

  if wait "$pid"; then
    ok "GUI build done ($(( (SECONDS - start) / 60 ))m $(( (SECONDS - start) % 60 ))s)"
    rm -f "$log"
    return 0
  fi
  err "GUI build failed — last output:"
  tail -n 30 "$log" >&2
  rm -f "$log"
  return 1
}

APPIMAGE="$(find_appimage)"
USE_BINARY=0
rebuild_reason=""
if [[ -n "$APPIMAGE" ]] && needs_rebuild "$APPIMAGE"; then
  rebuild_reason=" (source changed since it was built)"
  APPIMAGE=""
fi
build_binary_only() {
  # No AppImage bundling: just the executable. Works on platforms where the
  # bundler is unavailable (some aarch64 setups, missing linuxdeploy download).
  ( cd "$APP_DIR" && npm run tauri build -- --no-bundle ) || return 1
  [[ -x "$APP_DIR/src-tauri/target/release/throtl-app" ]]
}
if [[ -z "$APPIMAGE" ]]; then
  if [[ "${1:-}" == "--no-build" ]]; then
    err "no up-to-date AppImage for version $VERSION and --no-build is set"
    dim "present: $(ls -1 "$BUNDLE_DIR"/appimage/*.AppImage 2>/dev/null | tr '\n' ' ')"
    exit 1
  fi
  step "==> Build the GUI (version $VERSION$rebuild_reason)"
  dim "first build can take 5–15 min"
  command -v npm   >/dev/null || { err "npm missing (install Node)"; exit 1; }
  command -v cargo >/dev/null || { err "cargo missing (install rustup, see README)"; exit 1; }
  if ! build_appimage; then
    warn "AppImage bundling failed — building the plain binary instead"
    build_binary_only || { err "the GUI build failed"; exit 1; }
    USE_BINARY=1
  fi
  APPIMAGE="$(find_appimage)"
fi
if [[ "$USE_BINARY" -eq 0 && -z "$APPIMAGE" ]]; then
  warn "no AppImage produced — building the plain binary instead"
  build_binary_only || { err "the GUI build failed"; exit 1; }
  USE_BINARY=1
fi

dim "→ installing the dashboard …"
PREFIX="$HOME/.local"
OPT="$PREFIX/opt/throtl"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
mkdir -p "$OPT" "$BIN" "$APPS" "$ICONS"

if [[ "$USE_BINARY" -eq 1 ]]; then
  install -m 0755 "$APP_DIR/src-tauri/target/release/throtl-app" "$OPT/throtl-app-bin"
  EXEC_TARGET="$OPT/throtl-app-bin"
  rm -f "$OPT/Throtl.AppImage"
else
  install -m 0755 "$APPIMAGE" "$OPT/Throtl.AppImage"
  EXEC_TARGET="$OPT/Throtl.AppImage"
fi

cat > "$BIN/throtl-app" <<EOF
#!/usr/bin/env sh
exec "$EXEC_TARGET" "\$@"
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
  *) dim "note: '$BIN' is not on the PATH — add it to your shell config" ;;
esac

ok "GUI installed"
dim "Start:  throtl-app   (or 'Throtl' in the app menu)"
dim "File:   $EXEC_TARGET"
