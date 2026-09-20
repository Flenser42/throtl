#!/usr/bin/env bash
# Baut ein Debian-Paket (Architecture: all) nach dist/throtl_<version>_all.deb.
#
# Kein Root noetig. Benoetigt `dpkg-deb` (Paket dpkg-dev/dpkg). Das Paket
# installiert den Quellbaum unter /opt/throtl (wie setup/install.sh), damit die
# systemd-Unit unveraendert /opt/throtl/venv/bin/python nutzen kann.
#
# Optional: Ist `pip` verfuegbar und das Netz erreichbar, wird das
# TrafficToll-Wheel mit ins Paket gelegt -> die Installation laeuft offline.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SELF_DIR")")"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "Fehler: dpkg-deb fehlt (apt install dpkg)." >&2
    exit 1
fi

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJECT_DIR/throtl/__init__.py" | head -1)"
if [ -z "$VERSION" ]; then
    echo "Fehler: Version konnte nicht aus throtl/__init__.py gelesen werden." >&2
    exit 1
fi
echo "Baue throtl ${VERSION} (.deb) …"

STAGE="$SELF_DIR/_build/throtl_${VERSION}_all"
DEBIAN="$STAGE/DEBIAN"
rm -rf "$SELF_DIR/_build"
mkdir -p "$DEBIAN" \
    "$STAGE/opt/throtl/bin" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/lib/systemd/system" \
    "$STAGE/etc/throtl"

# --- Python-Paket + Launcher ----------------------------------------------
cp -r "$PROJECT_DIR/throtl" "$STAGE/opt/throtl/"
find "$STAGE/opt/throtl/throtl" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/opt/throtl/throtl" -name '*.pyc' -delete 2>/dev/null || true
install -m 0755 "$PROJECT_DIR"/bin/throtl-gui \
    "$PROJECT_DIR"/bin/throtl-cli \
    "$PROJECT_DIR"/bin/throtl-daemon "$STAGE/opt/throtl/bin/"

# --- Dokumentation ---------------------------------------------------------
for doc in README.md CHANGELOG.md LICENSE; do
    [ -f "$PROJECT_DIR/$doc" ] && install -m 0644 "$PROJECT_DIR/$doc" "$STAGE/opt/throtl/"
done

# --- Desktop, Icon, systemd, Default-Config --------------------------------
install -m 0644 "$PROJECT_DIR/setup/throtl.desktop" \
    "$STAGE/usr/share/applications/throtl.desktop"
install -m 0644 "$PROJECT_DIR/data/hicolor/scalable/apps/throtl.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/throtl.svg"
install -m 0644 "$PROJECT_DIR/setup/throtl.service" \
    "$STAGE/usr/lib/systemd/system/throtl.service"
install -m 0644 "$PROJECT_DIR/setup/default-config.toml" \
    "$STAGE/etc/throtl/config.toml"
echo "/etc/throtl/config.toml" > "$DEBIAN/conffiles"

# --- PATH-Symlinks ---------------------------------------------------------
for name in throtl-gui throtl-cli throtl-daemon; do
    ln -s "/opt/throtl/bin/$name" "$STAGE/usr/bin/$name"
done

# --- Optional: TrafficToll-Wheel fuer Offline-Installation ----------------
if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    if python3 -m pip download --no-deps --dest "$STAGE/opt/throtl/wheels" \
            traffictoll >/dev/null 2>&1; then
        echo "TrafficToll-Wheel mitgebundelt (Offline-Installation moeglich)."
    else
        rmdir "$STAGE/opt/throtl/wheels" 2>/dev/null || rm -rf "$STAGE/opt/throtl/wheels"
        echo "Hinweis: TrafficToll-Wheel nicht geladen (postinst nutzt PyPI)."
    fi
fi

# --- Control + Maintainer-Skripte ------------------------------------------
sed "s/@VERSION@/$VERSION/" "$SELF_DIR/control.in" > "$DEBIAN/control"
install -m 0755 "$SELF_DIR/postinst" "$SELF_DIR/prerm" "$SELF_DIR/postrm" "$DEBIAN/"

# --- Paket bauen -----------------------------------------------------------
mkdir -p "$PROJECT_DIR/dist"
OUT="$PROJECT_DIR/dist/throtl_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT" >/dev/null
rm -rf "$SELF_DIR/_build"
echo "Fertig: $OUT"
dpkg-deb --info "$OUT" | sed -n '1,20p'
