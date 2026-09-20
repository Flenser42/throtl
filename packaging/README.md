# Packaging Throtl

Throtl is **pure Python** (the wheel is `py3-none-any`), so there is nothing to
compile. "Prebuilt" therefore means:

- **sdist + wheel** attached to every GitHub release (built automatically by
  [`../.github/workflows/release.yml`](../.github/workflows/release.yml)),
- a **Debian package** (`.deb`, also built and attached by the release
  workflow), and
- an **AUR package** for Arch / Omarchy.

> A wheel alone only provides the Python modules and the `throtl-cli` /
> `throtl-daemon` / `throtl-gui` entry points. For a working install you still
> need the systemd unit, the desktop file and the system dependencies.

## Per-distro status

| Distro | Artifact | Status |
|--------|----------|--------|
| Any | `pip install throtl` (wheel from PyPI / release) | only the Python part |
| Debian / Ubuntu | `.deb` — see [`deb/`](deb/) | ✅ built in CI |
| Arch / Omarchy | AUR package — see [`aur/PKGBUILD`](aur/PKGBUILD) | template included |
| Fedora | `.rpm` | planned |

Common runtime dependencies: `python-gobject`, `gtk4`, `libadwaita`,
`python-cairo`, `nethogs`, `iproute2` (`tc`) and a TrafficToll backend that
provides the `tt` binary (`traffictoll`).

## Debian / Ubuntu (`.deb`)

```bash
make deb                     # -> dist/throtl_<version>_all.deb
sudo apt install ./dist/throtl_<version>_all.deb
```

The package:

- installs the sources under `/opt/throtl/` (same layout as
  `setup/install.sh`, so the systemd unit works unchanged),
- ships `/usr/bin/throtl-{gui,cli,daemon}` symlinks, the desktop file, the
  icon and the systemd unit,
- creates the `throtl` group and enables the service in its `postinst`,
- creates a venv at `/opt/throtl/venv` and installs **TrafficToll** into it.
  When the build machine had network access, the TrafficToll wheel is bundled
  in the package (`/opt/throtl/wheels`), so the install works offline.

After installing, add your user to the `throtl` group (the socket is
`root:throtl 0660`) and log in again:

```bash
sudo usermod -aG throtl "$USER"
sudo systemctl start throtl
throtl-gui
```

The release workflow builds the `.deb` on `ubuntu-latest` and attaches it to
the GitHub release. `packaging/deb/build.sh` only needs `dpkg-deb`.

## Arch / AUR

```bash
cd packaging/aur
makepkg -si          # after running `updpkgsums` to fill in the checksum
```

`PKGBUILD` installs the wheel into the system site-packages, plus the systemd
unit and desktop entry from `packaging/aur/`.

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
