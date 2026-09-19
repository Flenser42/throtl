# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Flenser42/throtl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Flenser42/throtl/releases/tag/v0.1.0
