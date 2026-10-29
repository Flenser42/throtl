#!/usr/bin/env bash
set -euo pipefail

# Throtl installation for Omarchy (Arch Linux + Hyprland, Wayland).
# - Installs system packages: nethogs, iproute2/tc (the shaper),
#   webkit2gtk-4.1 (for the dashboard)
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

# ---------------------------------------------------------------------------
# Terminal styling. Colours are used only when stdout is an interactive TTY
# that supports them (and NO_COLOR is unset). Piped/redirected output — CI,
# `| tee install.log` — stays byte-clean plain text (no escape codes).
# ---------------------------------------------------------------------------
if [[ -t 1 && -n "${TERM:-}" && "$TERM" != "dumb" && -z "${NO_COLOR:-}" ]]; then
  C_CYAN=$'\033[1;36m';  C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'
  C_RED=$'\033[1;31m';   C_DIM=$'\033[2m';      C_RESET=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_RESET=''
fi

# step:  bold cyan section header (prefixed by a blank line).
# ok:    green ✓ detail line.   warn: yellow ! detail line.
# err:   red ✗ banner to stderr (fatal/hard error).  dim: muted secondary detail.
step() { printf '\n%s%s%s\n' "$C_CYAN" "$1" "$C_RESET" || true; }
ok()   { printf '   %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1" || true; }
warn() { printf '   %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1" || true; }
err()  { printf '\n%s✗%s %s\n' "$C_RED" "$C_RESET" "$1" >&2 || true; }
dim()  { printf '   %s%s%s\n' "$C_DIM" "$1" "$C_RESET" || true; }

# has_cmd <bin>: true if the binary is on PATH or in a standard sbin/bin dir
# (needed because `tc` may only live under /usr/sbin, which root's PATH may miss).
has_cmd() {
  command -v "$1" >/dev/null 2>&1 \
    || [ -x "/usr/bin/$1" ] || [ -x "/usr/sbin/$1" ] || [ -x "/sbin/$1" ] || [ -x "/bin/$1" ]
}

# Options:
#   --no-app        skip the GUI
#   --with-waybar   install the Waybar status module (generic Arch)
#   --with-omarchy  install the Omarchy bar widget (Omarchy)
#   --with-widget   install whichever widget matches the detected shell
WITH_WAYBAR=0
WITH_OMARCHY=0
WITH_WIDGET=0
NO_APP=0
for arg in "$@"; do
  case "$arg" in
    --with-waybar)   WITH_WAYBAR=1 ;;
    --with-omarchy)  WITH_OMARCHY=1 ;;
    --with-widget)   WITH_WIDGET=1 ;;
    --no-app)        NO_APP=1 ;;
  esac
done

# Detect the shell/bar: Omarchy ships `omarchy-shell`; generic Arch uses Waybar.
IS_OMARCHY=0
if command -v omarchy-shell >/dev/null 2>&1 || [ -d /usr/share/omarchy ]; then
  IS_OMARCHY=1
fi

# --with-widget: install the widget that matches the detected shell.
if [[ "$WITH_WIDGET" -eq 1 ]]; then
  if [[ "$IS_OMARCHY" -eq 1 ]]; then WITH_OMARCHY=1; else WITH_WAYBAR=1; fi
fi

# Interactive (tty) with no explicit widget choice: ask about the relevant one.
if [[ -t 0 && "$WITH_WAYBAR" -eq 0 && "$WITH_OMARCHY" -eq 0 ]]; then
  if [[ "$IS_OMARCHY" -eq 1 ]]; then
    read -r -p "Install the Omarchy bar widget for Throtl? [Y/n] " _answer || true
    case "${_answer:-y}" in [Yy]*) WITH_OMARCHY=1 ;; esac
  else
    read -r -p "Install the Waybar status module for Throtl? [Y/n] " _answer || true
    case "${_answer:-y}" in [Yy]*) WITH_WAYBAR=1 ;; esac
  fi
fi

step "==> [1/8] Install system packages"
# Three system deps, named differently per distro:
#   nethogs             live per-process measurement
#   iproute2 / iproute  'ip' + the 'tc' shaper (Fedora splits tc into iproute-tc)
#   webkit2gtk-4.1      runtime for the dashboard (skip with --no-app)
# The GUI is built from source on the first install (step 7), so the matching
# -devel packages are needed as well: WebKitGTK/GTK headers, librsvg, OpenSSL,
# the tray (appindicator) library and patchelf for the AppImage. Without them
# `cargo build` stops at pkg-config (e.g. "webkit2gtk-4.1 not found").
WANT_DEV=0
if [[ "$NO_APP" -eq 0 ]]; then WANT_DEV=1; fi
dev_warn() { warn "could not install the GUI build dependencies"; \
             dim  "  later 'npm run tauri build' may fail (see docs/INSTALL.md)"; }

dim "→ installing system packages …"
PKG_OK=1
TC_PKG="iproute2"   # package that provides `tc` on Arch/Debian/openSUSE
if command -v pacman >/dev/null 2>&1; then
  # Arch, Manjaro, Omarchy
  sudo pacman -S --needed --noconfirm nethogs webkit2gtk-4.1 iproute2
  if [[ "$WANT_DEV" -eq 1 ]]; then
    sudo pacman -S --needed --noconfirm base-devel librsvg libappindicator-gtk3 \
      openssl file patchelf || dev_warn
  fi
elif command -v apt-get >/dev/null 2>&1; then
  # Debian, Ubuntu (and derivatives)
  sudo apt-get update
  sudo apt-get install -y nethogs libwebkit2gtk-4.1-0 iproute2
  if [[ "$WANT_DEV" -eq 1 ]]; then
    sudo apt-get install -y build-essential libwebkit2gtk-4.1-dev libgtk-3-dev \
      librsvg2-dev libayatana-appindicator3-dev libssl-dev file patchelf || dev_warn
  fi
elif command -v dnf >/dev/null 2>&1; then
  # Fedora (and derivatives, incl. Asahi): `ip` is in iproute, `tc` in iproute-tc.
  TC_PKG="iproute-tc"
  sudo dnf install -y nethogs webkit2gtk4.1 iproute iproute-tc
  if [[ "$WANT_DEV" -eq 1 ]]; then
    sudo dnf install -y webkit2gtk4.1-devel gtk3-devel librsvg2-devel \
      libayatana-appindicator-gtk3-devel openssl-devel file patchelf || dev_warn
  fi
elif command -v zypper >/dev/null 2>&1; then
  # openSUSE
  sudo zypper --non-interactive install nethogs libwebkit2gtk-4_1-0 iproute2
  if [[ "$WANT_DEV" -eq 1 ]]; then
    sudo zypper --non-interactive install webkit2gtk4.1-devel gtk3-devel \
      librsvg-devel libayatana-appindicator3-devel libopenssl-devel file patchelf || dev_warn
  fi
else
  PKG_OK=0
  warn "unrecognised package manager — install these first and re-run:"
  dim  "  nethogs · tc (iproute2 / iproute-tc) · webkit2gtk-4.1 (dashboard runtime)"
fi
if [[ "$PKG_OK" -eq 1 ]]; then
  ok "system packages"
  # Verify the tools actually landed. Distros split the shaper into its own
  # package (Fedora ships `tc` as iproute-tc), so a missing binary would
  # otherwise fail silently at shaping/measurement time — fail loudly with the
  # exact package name instead.
  if ! has_cmd tc; then
    err "the 'tc' shaper is missing — install '$TC_PKG' and re-run"
    exit 1
  fi
  if ! has_cmd nethogs; then
    err "the 'nethogs' monitor is missing — install 'nethogs' and re-run"
    exit 1
  fi
fi

step "==> [2/8] Install TrafficToll into a venv"
dim "→ creating the Python venv and installing TrafficToll …"
sudo mkdir -p "$OPT"
if [ ! -x "$OPT"/venv/bin/python ]; then
  sudo /usr/bin/python3 -m venv "$OPT/venv"
fi
# Provide pip for the venv (if missing)
if ! "$OPT/venv/bin/python" -c "import pip" >/dev/null 2>&1; then
  sudo "$OPT/venv/bin/python" -m ensurepip --upgrade || true
fi
# Quiet the pip steps; on failure re-run without -q so the real error is shown.
if ! sudo "$OPT/venv/bin/pip" install -q --upgrade pip; then
  sudo "$OPT/venv/bin/pip" install --upgrade pip >&2
fi
# TrafficToll 1.5.0 is from 2020 and pins psutil < 6, loguru < 0.5 and
# ruamel.yaml < 0.17. psutil 5.9.x only ships cp36-abi3 wheels for x86_64/i686,
# so on ARM (Asahi, Raspberry Pi, ...) pip has to compile it — which needs
# python3-devel and fails on Python 3.13+ anyway. psutil 6.1.1 still exposes the
# `pconn` type TrafficToll imports and ships abi3 wheels for every
# architecture, so it works without a compiler on all of them.
# Outside x86 pick that wheel-only set straight away instead of failing first.
TT_VERSION="1.5.0"
case "$(uname -m)" in
  x86_64|i686) TT_PINNED_OK=1 ;;
  *)           TT_PINNED_OK=0 ;;
esac
if [[ "$TT_PINNED_OK" -eq 1 ]] && \
   sudo "$OPT/venv/bin/pip" install -q "traffictoll==$TT_VERSION"; then
  :
else
  dim "→ installing TrafficToll with wheel-only dependencies (psutil 6.1.1) …"
  if ! sudo "$OPT/venv/bin/pip" install -q "psutil==6.1.1" "loguru==0.4.1" \
                                           "ruamel.yaml==0.16.13"; then
    sudo "$OPT/venv/bin/pip" install "psutil==6.1.1" "loguru==0.4.1" \
                                     "ruamel.yaml==0.16.13" >&2
  fi
  if ! sudo "$OPT/venv/bin/pip" install -q --no-deps "traffictoll==$TT_VERSION"; then
    sudo "$OPT/venv/bin/pip" install --no-deps "traffictoll==$TT_VERSION" >&2
  fi
fi
# Verify the binary actually imports — a broken tt would only surface much
# later as an obscure daemon error.
if ! sudo "$OPT/venv/bin/tt" --help >/dev/null 2>&1; then
  err "TrafficToll (tt) is not runnable in $OPT/venv:"
  sudo "$OPT/venv/bin/tt" --help 2>&1 | tail -n 5 >&2 || true
  exit 1
fi
ok "TrafficToll installed ($OPT/venv)"

step "==> [3/8] Copy project files"
# Remove the old copy so no stale modules/__pycache__ linger.
sudo rm -rf "$OPT/throtl"
sudo cp -r "$PROJECT_DIR/throtl" "$OPT/"
sudo find "$OPT/throtl" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
sudo install -m 0644 "$PROJECT_DIR/README.md" "$PROJECT_DIR/CHANGELOG.md" \
  "$PROJECT_DIR/LICENSE" "$OPT/"
sudo mkdir -p "$OPT/bin"
sudo cp "$PROJECT_DIR"/bin/throtl-cli "$PROJECT_DIR"/bin/throtl-daemon "$OPT/bin/"
sudo chmod +x "$OPT/bin"/throtl-*
dim "linking launchers to /usr/local/bin/throtl-* (PATH)"
sudo ln -sf "$OPT/bin/throtl-cli"    /usr/local/bin/throtl-cli
sudo ln -sf "$OPT/bin/throtl-daemon" /usr/local/bin/throtl-daemon
ok "project files + launchers installed"

step "==> [4/8] Group, configuration + runtime directories"
# Group 'throtl': only its members may access the daemon socket
# (0660, see Daemon._secure_socket). Idempotent: if the group already exists,
# existing members are kept.
GROUP_ADDED=0
if ! getent group throtl >/dev/null 2>&1; then
  sudo groupadd --system throtl
fi
# Resolve which account to add to 'throtl' — the daemon socket is 0660
# root:throtl, so membership is mandatory for the GUI/CLI. SUDO_USER is empty
# under `sudo -i` / a root shell, so fall back to the checkout owner, then the
# first non-root human session, and never skip silently.
TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  TARGET_USER="$(stat -c %U "$PROJECT_DIR" 2>/dev/null || true)"
fi
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]] && command -v loginctl >/dev/null 2>&1; then
  TARGET_USER="$(loginctl list-users --no-legend 2>/dev/null \
    | awk '$1 >= 1000 { print $2; exit }' || true)"
fi
if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
  if ! id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx throtl; then
    sudo usermod -aG throtl "$TARGET_USER"
    GROUP_ADDED=1
    ok "added $TARGET_USER to group 'throtl'"
  else
    dim "$TARGET_USER is already in group 'throtl'"
  fi
else
  err "could not determine which account to add to the 'throtl' group"
  dim "  add it manually: sudo usermod -aG throtl <user>"
fi
sudo mkdir -p "$ETC" "$RUN"
if [ ! -f "$ETC/config.toml" ]; then
  sudo install -o root -g root -m 0644 "$SELF_DIR"/default-config.toml "$ETC/config.toml"
fi
ok "config + runtime directories ready"

step "==> [5/8] Install systemd service"
sudo install -m 0644 "$SELF_DIR"/throtl.service /etc/systemd/system/
sudo systemctl daemon-reload
# Clear the restart counter (in case the unit was stuck in a start loop before)
sudo systemctl reset-failed throtl 2>/dev/null || true
sudo systemctl enable throtl
# Do not abort via set -e if the daemon fails to start: otherwise the desktop
# file/icon/autostart (step 6) would never run and the installation would
# remain half-finished.
if ! sudo systemctl restart throtl; then
  err "daemon failed to start — last log lines:"
  sudo journalctl -u throtl -n 20 --no-pager 2>/dev/null | sed 's/^/     /' || true
  dim "full log: journalctl -u throtl -n 50 --no-pager"
else
  ok "daemon active (systemctl status throtl)"
fi

step "==> [6/8] Clean up old GTK entries"
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
  dim "no AUR helper (paru/yay) found — TrafficToll was installed via pip, no AUR package needed"
fi

step "==> [7/8] Install the new GUI (Tauri)"
# Per-user GUI: prefer the account resolved in step 4, but honor an explicit
# sudo invoker only when it names a real (non-root) user.
APP_USER="$TARGET_USER"
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]] && id "$SUDO_USER" >/dev/null 2>&1; then
  APP_USER="$SUDO_USER"
fi
# A running dashboard would keep its old binary/AppImage locked during a
# reinstall. Close it first and reopen afterwards (best effort).
APP_WAS_RUNNING=0
if pgrep -f 'throtl-app' >/dev/null 2>&1; then
  APP_WAS_RUNNING=1
  dim "the dashboard is running — closing it for the update …"
  pkill -f 'throtl-app' 2>/dev/null || true
  sleep 1
fi
GUI_NOTE="~/.local/bin/throtl-app"
if [[ "$NO_APP" -eq 1 ]]; then
  GUI_NOTE="skipped (--no-app)"
  dim "skipped (--no-app)"
elif [[ "$APP_USER" == "root" ]]; then
  GUI_NOTE="skipped (run as root — no GUI)"
  dim "called as root — GUI skipped"
  dim "run without sudo later: ./setup/install-app.sh"
elif sudo -u "$APP_USER" -H bash "$SELF_DIR/install-app.sh"; then
  ok "GUI installed — 'throtl-app' or 'Throtl' in the app menu"
else
  GUI_NOTE="not installed (build failed)"
  warn "GUI installation failed (are Node/Rust present?)"
  dim  "  try again later: ./setup/install-app.sh"
fi
if [[ "$APP_WAS_RUNNING" -eq 1 ]]; then
  if [[ "$APP_USER" != "root" ]]; then
    sudo -u "$APP_USER" -H nohup gtk-launch throtl-app.desktop >/dev/null 2>&1 &
    dim "the dashboard was closed for the update — relaunching it"
  else
    dim "the dashboard was closed for the update — reopen it from your launcher"
  fi
fi

step "==> [8/8] Install polkit action for GUI setup"
# Lets the dashboard trigger the one-time setup (the 'throtl' group and, from
# a checkout, the reinstall) via `pkexec` without a terminal. The policy file
# references the helper under its installed path, so pkexec uses the
# org.throtl.setup action (auth_admin) instead of the generic fallback action
# org.freedesktop.policykit.exec.
sudo install -m 0755 "$SELF_DIR"/throtl-setup /usr/local/bin/throtl-setup
sudo mkdir -p /usr/share/polkit-1/actions
sudo install -m 0644 "$SELF_DIR"/polkit/org.throtl.setup.policy \
  /usr/share/polkit-1/actions/org.throtl.setup.policy
ok "polkit action installed (pkexec /usr/local/bin/throtl-setup)"

if [[ "$WITH_WAYBAR" -eq 1 ]]; then
  step "==> Waybar status module"
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
    ok "installed $WB_HOME/.local/bin/throtl-waybar"
    dim "add this module to your Waybar config (modules-right etc.):"
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
    warn "could not create ~/.local/bin — skipping the Waybar module"
  fi
fi

if [[ "$WITH_OMARCHY" -eq 1 ]]; then
  step "==> Omarchy bar widget"
  OM_USER="${SUDO_USER:-$USER}"
  if [[ "$OM_USER" == "root" ]]; then
    OM_HOME="${HOME:-/root}"
  else
    OM_HOME="$(getent passwd "$OM_USER" | cut -d: -f6)"
  fi
  if [[ -n "$OM_HOME" ]]; then
    OM_PLUGINS="$OM_HOME/.config/omarchy/plugins/flenser.throtl"
    install -d -m 0755 "$OM_PLUGINS"
    install -m 0644 "$SELF_DIR"/omarchy/flenser.throtl/manifest.json \
                     "$SELF_DIR"/omarchy/flenser.throtl/BarWidget.qml \
                     "$SELF_DIR"/omarchy/flenser.throtl/Panel.qml \
                     "$OM_PLUGINS/"
    chown -R "$OM_USER" "$OM_HOME/.config/omarchy/plugins/flenser.throtl" 2>/dev/null || true
    # Activate it in the bar (idempotent; backs up shell.json first).
    if python3 "$SELF_DIR"/omarchy/register-widget.py \
         "$OM_HOME/.config/omarchy/shell.json" flenser.throtl 2>/dev/null; then
      ok "installed $OM_PLUGINS"
      if command -v omarchy >/dev/null 2>&1; then
        sudo -u "$OM_USER" -H omarchy restart shell 2>/dev/null || true
      fi
      dim "widget appears in the bar's right section"
    else
      warn "could not register the widget in shell.json — copy the"
      dim  "  plugin and add {\"id\":\"flenser.throtl\"} manually"
    fi
  else
    warn "could not determine the user home — skipping the Omarchy widget"
  fi
fi

step "==> Throtl is installed"
dim "Daemon   /opt/throtl/bin/throtl-daemon"
dim "CLI      /opt/throtl/bin/throtl-cli"
dim "Config   /etc/throtl/config.toml"
dim "GUI      $GUI_NOTE"
echo
dim "Next:"
dim "  throtl-cli status"
dim "  throtl-app"
dim "  sudo ./setup/uninstall.sh"
if [[ "$GROUP_ADDED" -eq 1 ]]; then
  echo
  warn "$TARGET_USER was just added to the 'throtl' group"
  dim  "  log out and back in once, or run: newgrp throtl"
fi
echo
