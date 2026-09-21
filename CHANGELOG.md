# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.2] - 2026-09-21

### Fixed

- **Per-app rates were inaccurate.** nethogs' built-in rate divides by its
  assumed `PERIOD` and drifts under load (measured: 2.0 MB/s reported while the
  kernel and the app itself showed ~2.5 MB/s). The monitor now runs nethogs in
  `-v 1` mode (cumulative kB) and computes the rate itself from the delta over a
  monotonic clock. Verified against the kernel: Agent.exe 2.42–2.73 vs 2.52 MB/s.

## [0.5.1] - 2026-09-20

### Fixed

- **Debian package**: the `.deb` now depends on `python3-gi-cairo`. Without
  that (separate) Debian package PyGObject cannot marshal a `cairo.Context`
  into the graph's draw function, so the bandwidth graph stayed blank. Arch's
  `python-gobject` bundles this already, which is why it only affected the
  `.deb`.

### Changed

- Refreshed the README screenshots and the demo GIF to show the current UI
  (filter box, per-row time-window button, startup-profile menu). They are now
  reproducible via `make images` (see `tools/make_images.py`).
- Example profile names in the docs are English now (`University`, `Evening`,
  `Night`) instead of German.

## [0.5.0] - 2026-09-20

### Added

- **Per-rule time windows**: limit a rule to weekdays and a time range (e.g.
  Firefox Mon–Fri 20:00–00:00). Windows across midnight are supported, and the
  daemon re-applies the engine automatically when a window opens or closes. In
  the GUI the clock button in each row opens the editor; the CLI accepts
  `--window-days` / `--window-start` / `--window-end` and `--clear-window`.
- **Startup profile** (`start_profile`): activate a named profile whenever the
  daemon starts (a matching schedule still takes priority). GUI: *Startup
  profile…* menu; CLI: `throtl-cli start-profile`.
- **Application filter** in the process table; the last filter is remembered.
- **`throtl-cli watch`**: watch processes for N seconds and print a report.
  `--alert <rate>` exits with code 4 when an app exceeds the threshold, `--json`
  prints machine-readable output.
- **Debian package**: `make deb` builds `dist/throtl_<version>_all.deb`, which
  CI attaches to releases. It bundles the TrafficToll wheel for an offline
  install when the build machine has network access.

### Changed

- README and TESTING document time windows, the startup profile, `watch`, the
  filter and the `.deb` package.

## [0.4.2] - 2026-09-20

### Fixed

- **GUI busy-loop**: refilling the profile dropdown fired `notify::selected`
  outside the guard, so `activate_profile -> reload -> _reload_profiles` looped
  ~135×/s. Side effects: the bandwidth graph pushed ~135 samples/s and its
  history got stuck at ~7 s, the global rate flickered to "measuring…", and the
  GUI burned CPU. The dropdown is now filled under its own guard.
- The global interface rate is cached between samples: short-interval callers
  (monitor tick + GUI poll share one sample) no longer get `None`.

## [0.4.1] - 2026-09-20

### Fixed

- Per-app rates were ~2.4 % too low: nethogs reports **KiB/s** (it divides by
  1024, `#define KB (1UL << 10)`), but Throtl treated the value as kB/s.
- The per-process limit field lost focus while typing, so the text disappeared
  and the limit never applied: the table re-sorted/removed rows on every poll.
  Sorting and row removal are now paused while a limit field is focused (plus a
  short latch after focusing), so the value can be typed and is sent.

## [0.4.0] - 2026-09-20

### Added

- **Consumption budgets**: rolling `day` (last 24 h) and `week` (last 7 days)
  volume thresholds, global (`[budgets]`) or per app (`[[budget_rules]]`). The
  GUI warns in the status bar and sends a desktop notification when a budget is
  exceeded; the CLI gained `budgets`, `budget-set` and `budget-remove`.
- **Statistics graph**: the statistics dialog now draws download/upload per
  bucket for the selected range (hour / 2 days / 30 days) instead of only a
  table.
- `units.parse_size()` ("20GB", "5 GiB") for human-friendly budget values.

### Fixed

- The bandwidth graph looked broken right after startup: with less history than
  the selected window it drew a narrow strip on the right. It now stretches the
  available samples across the full width until the window is full, then scrolls.

## [0.3.1] - 2026-09-20

### Fixed

- "Daemon not reachable" was misleading when the real cause was a permission
  problem on the socket. `throtl-cli` (status + doctor) and the GUI now detect
  that the socket exists but the *running session* lacks the `throtl` group,
  and tell you to run `newgrp throtl` or log out and back in. `install.sh`
  prints that reminder on every run.

## [0.3.0] - 2026-09-20

### Added

- `throtl-cli top`: full-screen live ranking of applications (htop-style),
  sortable by download/upload/name.
- `throtl-cli selftest`: end-to-end check that limits really throttle. Measures
  a baseline download, applies a temporary `curl` rule, samples the *shaped*
  rate from nethogs after a warm-up, then restores the previous rule. Exits
  non-zero when the limit is not effective.
- Observability: `status` now reports engine `applies` / `restarts` /
  `apply_failures` / last+avg apply duration, plus monitor `starts` and the last
  monitor crash reason.
- The GUI remembers the process-table sort column and direction (stored in
  `~/.config/throtl/gui.json`).
- README: animated demo GIF.

### Fixed

- The daemon now handles `SIGTERM`/`SIGINT` cleanly: `systemctl stop` or a plain
  `kill` stops the monitor (reaping `nethogs`) and removes the socket instead of
  leaving orphaned processes behind.

## [0.2.1] - 2026-09-20

### Fixed

- Monitoring no longer goes silently dark when the `nethogs` process dies: the
  daemon now detects the dead process, reaps it (no more zombie), records the
  exit code and the last stderr lines, and restarts the monitor automatically.
  Previously a dead `nethogs` left the GUI/CLI showing no processes and no
  bandwidth while `status` still claimed `Monitoring: yes`.
- `nethogs` stderr is captured (it was discarded) so the reason is visible in
  `status`/`doctor` instead of only vanishing.

## [0.2.0] - 2026-09-20

### Added

- **Profiles**: save the current global limits and process rules under a name
  (`[profiles.*]`) and switch between them. The top-level `global`/`processes`
  remain the active view, so v0.1.0 configs stay loadable.
- **Schedules**: activate a profile by weekday and time (`[[schedule]]`),
  including overnight windows. `parse_days()` understands `mo`…`so`, ranges
  like `"mo-fr"` and English weekday names.
- **Statistics**: the daemon persists per-app download/upload volume to
  `stats.json` in three rolling windows (1 h of minute buckets, 2 days of hour
  buckets, 30 days of day buckets). New RPC methods `get_stats` and
  `reset_stats`.
- GUI: profile dropdown and refresh button in the header bar, a menu with
  "Save settings as profile…" / "Delete profile", and a **Statistics** dialog
  with a window switcher and a per-app list.
- CLI: `profiles`, `profile-use`, `profile-save`, `profile-delete`, `stats`,
  `export`, `import` and `doctor`.
- Daemon RPC: `list_profiles`, `set_profile`, `delete_profile`,
  `activate_profile`, `set_schedule` and `import_config`.
- Tests: `tests/test_stats.py` and `tests/test_profiles.py`.

### Changed

- Daemon persists `config.toml`, the generated TrafficToll YAML and
  `stats.json` atomically (temp file + `fsync` + `os.replace`).
- The monitor tick applies a scheduled profile automatically when it differs
  from the active one (only when a schedule exists).

### Fixed

- A syntactically broken `config.toml` no longer prevents the daemon from
  starting: it falls back to defaults and exposes the reason via `status()`
  (`config_warning`).

### Security

- The daemon socket is now owned by `root:throtl` with mode `0660` instead of
  the world-accessible `0666`. `install.sh` creates the `throtl` group and adds
  the invoking user.
- The generated TrafficToll YAML is written with mode `0600`.

## [0.1.0] - 2026-09-17

### Added

- GTK4 + libadwaita GUI with a live bandwidth graph, global
  limits, per-application download/upload limits and priority levels.
- Privileged daemon (`throtl.daemon`) speaking JSON-over-Unix-socket, backed by
  [TrafficToll](https://github.com/cryzed/TrafficToll) (`tt`) for `tc`-based
  shaping.
- Live per-process monitoring via `nethogs -t`, including TCP and UDP traffic
  and a synthetic entry for traffic that cannot be attributed to a process.
- `throtl-cli` for headless control (status, limits, rules, live monitor) and a
  simulation mode that needs neither root nor `tt`.
- Debian/Arch-friendly installer (`setup/install.sh`), systemd unit, desktop
  entry, icon and uninstaller.
- Test suite (unittest) covering units, config persistence, IPC framing,
  nethogs parsing, the TrafficToll engine and the daemon/CLI end-to-end paths.
- A dedicated app icon (a throttle/gauge mark) and repository/community files:
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and PR templates, `.editorconfig`,
  `.gitattributes` and Dependabot.

### Fixed

- GUI responsiveness: changing a limit, priority or the global switch no longer
  freezes the window for ~2 s. Mutating RPCs run on a background worker, the
  control updates optimistically and reverts on error.
- IPC framing: bytes following a message's newline in the same `recv()` are now
  buffered instead of discarded, so coalesced responses/events and pipelined
  requests are no longer lost.
- GUI: editing a limit/priority on a grouped per-application row no longer
  re-escapes the stored match pattern (which produced duplicate, non-matching
  rules); updates are applied to the existing rule by key.
- GUI: the PID column shows the real PID(s) instead of repeating the app name.
- Monitor shutdown now reaps the `nethogs` subprocess and closes its pipe
  (no more leaked/zombie processes).
- Daemon: `monitor_factory=None` now genuinely disables monitoring instead of
  silently starting `nethogs`.

### Changed

- Performance: TrafficToll restarts happen on a background worker and are
  coalesced, identical configs are skipped entirely, and shutdown waits are
  bounded — so repeated edits no longer trigger needless `tt` restarts.
- GUI styling overhaul: an explicit dark palette (readable regardless of the
  host theme), colour-coded download/upload rates, a framed table body, a
  status bar with an error state and a proper empty state with icon.
- Bandwidth graph now uses a real time axis and auto-scrolls: it shows only the
  last 60 s by default (switchable to 30 s / 1 min / 5 min / 15 min / All) while
  keeping the full history scrollable. Scrolling back pauses auto-scroll until
  you return to the live edge.

[Unreleased]: https://github.com/Flenser42/throtl/compare/v0.5.2...HEAD
[0.5.2]: https://github.com/Flenser42/throtl/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/Flenser42/throtl/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Flenser42/throtl/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/Flenser42/throtl/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Flenser42/throtl/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Flenser42/throtl/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Flenser42/throtl/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Flenser42/throtl/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Flenser42/throtl/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Flenser42/throtl/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Flenser42/throtl/releases/tag/v0.1.0
