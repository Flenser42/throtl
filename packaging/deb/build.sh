#!/usr/bin/env bash
# Builds a Debian package (Architecture: all) to dist/throtl_<version>_all.deb.
#
# No root needed. Requires `dpkg-deb` (package dpkg-dev/dpkg). The package
# installs the source tree under /opt/throtl (like setup/install.sh), so the
# systemd unit can use /opt/throtl/venv/bin/python unchanged.
#
# Optional: if `pip` is available and the network is reachable, the TrafficToll
# wheel is bundled into the package -> the installation runs offline.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SELF_DIR")")"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "Error: dpkg-deb missing (apt install dpkg)." >&2
    exit 1
fi

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJECT_DIR/throtl/__init__.py" | head -1)"
if [ -z "$VERSION" ]; then
    echo "Error: could not read version from throtl/__init__.py." >&2
    exit 1
fi
echo "Building throtl ${VERSION} (.deb) …"

STAGE="$SELF_DIR/_build/throtl_${VERSION}_all"
DEBIAN="$STAGE/DEBIAN"
rm -rf "$SELF_DIR/_build"
mkdir -p "$DEBIAN" \
    "$STAGE/opt/throtl/bin" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/lib/systemd/system" \
    "$STAGE/etc/throtl"

# --- Python package + launchers ---------------------------------------------
cp -r "$PROJECT_DIR/throtl" "$STAGE/opt/throtl/"
find "$STAGE/opt/throtl/throtl" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/opt/throtl/throtl" -name '*.pyc' -delete 2>/dev/null || true
install -m 0755 "$PROJECT_DIR"/bin/throtl-cli \
    "$PROJECT_DIR"/bin/throtl-daemon "$STAGE/opt/throtl/bin/"

# --- Documentation ---------------------------------------------------------
for doc in README.md CHANGELOG.md LICENSE; do
    [ -f "$PROJECT_DIR/$doc" ] && install -m 0644 "$PROJECT_DIR/$doc" "$STAGE/opt/throtl/"
done

# --- systemd, default config -------------------------------------------------
install -m 0644 "$PROJECT_DIR/setup/throtl.service" \
    "$STAGE/usr/lib/systemd/system/throtl.service"
install -m 0644 "$PROJECT_DIR/setup/default-config.toml" \
    "$STAGE/etc/throtl/config.toml"
echo "/etc/throtl/config.toml" > "$DEBIAN/conffiles"

# --- PATH symlinks ---------------------------------------------------------
for name in throtl-cli throtl-daemon; do
    ln -s "/opt/throtl/bin/$name" "$STAGE/usr/bin/$name"
done

# --- Optional: TrafficToll wheel for offline installation ----------------
# Without --no-deps, so the dependencies (psutil, loguru, ruamel.yaml) come
# along — otherwise the --no-index install in postinst fails offline.
if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    if python3 -m pip download --dest "$STAGE/opt/throtl/wheels" \
            traffictoll >/dev/null 2>&1; then
        echo "TrafficToll wheel bundled (offline installation possible)."
    else
        rmdir "$STAGE/opt/throtl/wheels" 2>/dev/null || rm -rf "$STAGE/opt/throtl/wheels"
        echo "Note: TrafficToll wheel not downloaded (postinst uses PyPI)."
    fi
fi

# --- Control + maintainer scripts ------------------------------------------
sed "s/@VERSION@/$VERSION/" "$SELF_DIR/control.in" > "$DEBIAN/control"
install -m 0755 "$SELF_DIR/postinst" "$SELF_DIR/prerm" "$SELF_DIR/postrm" "$DEBIAN/"

# --- Build the package -----------------------------------------------------------
mkdir -p "$PROJECT_DIR/dist"
OUT="$PROJECT_DIR/dist/throtl_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT" >/dev/null
rm -rf "$SELF_DIR/_build"
echo "Done: $OUT"
dpkg-deb --info "$OUT" | sed -n '1,20p'
