# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-17

### Added

- NetLimiter-style GTK4 + libadwaita GUI with a live bandwidth graph, global
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

- IPC framing: bytes following a message's newline in the same `recv()` are now
  buffered instead of discarded, so coalesced responses/events and pipelined
  requests are no longer lost.
- Monitor shutdown now reaps the `nethogs` subprocess and closes its pipe
  (no more leaked/zombie processes).
- Daemon: `monitor_factory=None` now genuinely disables monitoring instead of
  silently starting `nethogs`.

[Unreleased]: https://github.com/Flenser42/throtl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Flenser42/throtl/releases/tag/v0.1.0
