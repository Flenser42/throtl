# Contributing to Throtl

Thanks for taking the time to improve Throtl. This document describes the
development setup and the expectations for changes.

## Development setup

Throtl's backend is pure standard library, so it can be developed on any Linux
box. The GUI additionally needs GTK4 + libadwaita through **system** PyGObject
(it is intentionally not a pip dependency).

```bash
git clone https://github.com/Flenser42/throtl.git
cd throtl

# Arch / Omarchy
sudo pacman -S --needed nethogs gtk4 libadwaita python-gobject python-cairo
# Debian / Ubuntu
# sudo apt install nethogs gir1.2-gtk-4.0 gir1.2-adw-1 python3-gi

make check           # ruff + full unittest suite
```

Notes:

- Use the system Python for GUI tests (`/usr/bin/python3`), because a
  virtualenv/mise Python typically does not have PyGObject. The `Makefile`
  already prefers `/usr/bin/python3` when present.
- GUI widget tests are skipped automatically when no display is available.
- To exercise real shaping you need root, `tc` and
  [TrafficToll](https://github.com/cryzed/TrafficToll). Without them, use the
  simulation mode: `throtl-daemon --simulate --socket /tmp/throtl.sock`.

## Style

- Keep the backend free of third-party Python dependencies.
- Formatting/linting is enforced with [ruff](https://docs.astral.sh/ruff/)
  (`make lint`, config in `pyproject.toml`). Run `make lint-fix` for import
  sorting.
- Match the existing code style and keep user-facing GUI text in English while
  code comments may stay German.

## Pull requests

1. Add or update tests for behaviour changes (`tests/`).
2. Run `make check` and make sure it is green.
3. Keep commits focused and describe *why* a change is made.
4. Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.

## Reporting bugs

Please include:

- distribution and kernel version,
- output of `throtl-cli status` (with `systemctl status throtl`),
- whether the daemon runs in simulation or real mode,
- the exact steps to reproduce.
