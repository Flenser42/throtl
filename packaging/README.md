# Packaging Throtl

Throtl is **pure Python** (the wheel is `py3-none-any`), so there is nothing to
compile. "Prebuilt" therefore means:

- **sdist + wheel** attached to every GitHub release (built automatically by
  [`../.github/workflows/release.yml`](../.github/workflows/release.yml)),
- a **Debian package** (`.deb`, also built and attached by the release
  workflow), and
- an **AUR package** for Arch / Omarchy.

> A wheel alone only provides the Python modules and the `throtl-cli` /
> `throtl-daemon` entry points. For a working install you still need the
> systemd unit and the system dependencies. The dashboard is a separate Tauri
> app (see [`../throtl-app/`](../throtl-app/)) and is not part of the wheel.

## Per-distro status

| Distro | Artifact | Status |
|--------|----------|--------|
| Any | `pip install throtl` (wheel from PyPI / release) | only the Python part |
| Debian / Ubuntu | `.deb` — see [`deb/`](deb/) | ✅ built in CI |
| Arch / Omarchy | AUR package — see [`aur/PKGBUILD`](aur/PKGBUILD) | template included |
| Fedora | `.rpm` | planned |

Common runtime dependencies: `nethogs`, `iproute2` (`tc`) and a TrafficToll
backend that provides the `tt` binary (`traffictoll`). The backend needs only
the Python standard library — no PyGObject, no GTK. The dashboard needs
`webkit2gtk-4.1` at runtime (it ships separately, see below).

## Debian / Ubuntu (`.deb`)

```bash
make deb                     # -> dist/throtl_<version>_all.deb
sudo apt install ./dist/throtl_<version>_all.deb
```

The package:

- installs the sources under `/opt/throtl/` (same layout as
  `setup/install.sh`, so the systemd unit works unchanged),
- ships `/usr/bin/throtl-{cli,daemon}` symlinks and the systemd unit,
- creates the `throtl` group and enables the service in its `postinst`,
- creates a venv at `/opt/throtl/venv` and installs **TrafficToll** into it.
  When the build machine had network access, the TrafficToll wheel is bundled
  in the package (`/opt/throtl/wheels`), so the install works offline.

After installing, add your user to the `throtl` group (the socket is
`root:throtl 0660`) and log in again:

```bash
sudo usermod -aG throtl "$USER"
sudo systemctl start throtl
throtl-cli status
```

For the desktop GUI, install the dashboard per user (no root):

```bash
./setup/install-app.sh
```

The release workflow builds the `.deb` on `ubuntu-latest` and attaches it to
the GitHub release. `packaging/deb/build.sh` only needs `dpkg-deb`.

## Arch / AUR

```bash
cd packaging/aur
makepkg -si          # after running `updpkgsums` to fill in the checksum
```

`PKGBUILD` installs the wheel into the system site-packages, plus the systemd
unit from `packaging/aur/`. The dashboard is installed per user via
`./setup/install-app.sh` (or the Tauri `.deb`/`.rpm` from the release).

## Why the backend is not an AppImage (but the dashboard is)

An AppImage is a portable **user-space** application bundle. Throtl's core is a
**privileged daemon** that:

- runs as `root` under systemd,
- drives `tc` and the `ifb` kernel module,
- reads `/proc` via `nethogs`.

None of that fits in an AppImage, so the backend ships as distro packages (or
`setup/install.sh`). The **dashboard**, by contrast, is pure user space and is
installed as an AppImage under `~/.local` by `setup/install-app.sh` — the same
bundle the Tauri `.deb`/`.rpm` contains.
