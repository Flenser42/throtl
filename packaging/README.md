# Packaging Throtl

Throtl is **pure Python** (the wheel is `py3-none-any`), so there is nothing to
compile. "Prebuilt" therefore means:

- **sdist + wheel** attached to every GitHub release (built automatically by
  [`../.github/workflows/release.yml`](../.github/workflows/release.yml)), and
- **native packages** for the distro, which is what users actually want,
  because Throtl installs a **root systemd service** and needs system libraries.

> A wheel alone only provides the Python modules and the `throtl-cli` /
> `throtl-daemon` / `throtl-gui` entry points. For a working install you still
> need the systemd unit, the desktop file and the system dependencies.

## Per-distro status

| Distro | Artifact | Status |
|--------|----------|--------|
| Any | `pip install throtl` (wheel from PyPI / release) | only the Python part |
| Arch / Omarchy | AUR package — see [`aur/PKGBUILD`](aur/PKGBUILD) | template included |
| Debian / Ubuntu | `.deb` (control template below) | planned |
| Fedora | `.rpm` | planned |

Common runtime dependencies: `python-gobject`, `gtk4`, `libadwaita`,
`python-cairo`, `nethogs`, `iproute2` (`tc`) and a TrafficToll backend that
provides the `tt` binary (`traffictoll`).

## Arch / AUR

```bash
cd packaging/aur
makepkg -si          # after running `updpkgsums` to fill in the checksum
```

`PKGBUILD` installs the wheel into the system site-packages, plus the systemd
unit and desktop entry from `packaging/aur/`.

## Debian / Ubuntu (sketch)

A minimal `control` file:

```
Package: throtl
Version: 0.1.0
Architecture: all
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1,
         python3-cairo, nethogs, iproute2
Recommends: traffictoll
Maintainer: Flenser42
Description: Per-application bandwidth limits and traffic prioritisation
```

Build with `python -m build` + `python -m installer --destdir=debian/throtl`,
then add `usr/lib/systemd/system/throtl.service` and the desktop file.

## Why not an AppImage?

An AppImage is a portable **user-space** application bundle. Throtl's core is a
**privileged daemon** that:

- runs as `root` under systemd,
- drives `tc` and the `ifb` kernel module,
- reads `/proc` via `nethogs`.

None of that fits in an AppImage. The best an AppImage could do is ship the GUI,
which cannot do anything without the system daemon — and bundling GTK4,
PyGObject and all GObject-introspection typelibs is large and fragile. Distro
packages (or the plain `setup/install.sh`) are the right vehicle.
