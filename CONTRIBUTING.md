# Contributing to Throtl

Thanks for taking the time to improve Throtl. This document describes the
development setup and the expectations for changes.

## Development setup

Throtl's backend is pure standard library, so it can be developed on any Linux
box — no GTK, no PyGObject, no pip dependencies.

```bash
git clone https://github.com/Flenser42/throtl.git
cd throtl

# Arch / Omarchy
sudo pacman -S --needed nethogs
# Debian / Ubuntu
# sudo apt install nethogs

make check           # ruff + full unittest suite
```

Notes:

- The tests need no display and no root; `make check` runs everywhere.
- To exercise real shaping you need root, `tc` and
  [TrafficToll](https://github.com/cryzed/TrafficToll). Without them, use the
  simulation mode: `throtl-daemon --simulate --socket /tmp/throtl.sock`.
- The **dashboard** (`throtl-app/`) is a separate Tauri + React app: it needs
  Node 20+ and Rust to build, and `webkit2gtk-4.1` to run. See
  [`throtl-app/README.md`](throtl-app/README.md).

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

Please use the GitHub issue templates and include:

- distribution and kernel version,
- output of `throtl-cli status` (with `systemctl status throtl`),
- whether the daemon runs in simulation or real mode,
- the exact steps to reproduce.

For security issues, do **not** open a public issue — see
[`SECURITY.md`](SECURITY.md).

## Code of conduct

Participation in this project is covered by
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
