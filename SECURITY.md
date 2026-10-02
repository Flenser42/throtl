# Security Policy

## Supported versions

Security fixes are provided for the latest release and `master`.

| Version | Supported |
|---------|-----------|
| 0.10.x  | ✅        |
| < 0.10  | ❌        |

## Known advisories

### `RUSTSEC-2024-0429` — `glib` < 0.20 (dashboard, informational)

The dashboard's Rust shell pulls `glib` 0.18 through Tauri → gtk-rs, and that
version carries an **informational** advisory (`informational = "unsound"`, no
CVE): `glib::VariantStrIter`'s iterator impls wrote through a shared reference,
which can lead to a NULL dereference. It is fixed in `glib` 0.20.

Why it is not fixed here: the fix lives in gtk-rs 0.20, and Tauri 2.x still
pins gtk 0.18 (checked with Tauri 2.12.1). It cannot be resolved from this
repository, and Throtl's own Rust code never iterates `GVariant` string
iterators — the affected API is only reachable from gtk-rs internals. The
Dependabot rule in `.github/dependabot.yml` ignores `glib < 0.20` and allows the
bump as soon as Tauri moves.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, the affected version and steps to reproduce.

You can expect an initial response within a few days. If private reporting is
not available, open a minimal issue asking for a private channel — without
details.

## Scope and design notes

Throtl's daemon runs as **root** and exposes a **local** Unix socket
(`/run/throtl/daemon.sock`). Relevant classes of issues include:

- privilege escalation from the socket API,
- unsafe rendering of the TrafficToll YAML or of the `tt` command arguments,
- path/symlink attacks around `/run/throtl` or `/etc/throtl`.

The socket is owned by `root:throtl` and has mode `0660`, so only members of
the `throtl` group (created by `setup/install.sh`, which also adds the invoking
user) can manage bandwidth limits. The daemon falls back to `0666` with a clear
warning only if the `throtl` group does not exist and the daemon was started
manually without `install.sh`. No network port is ever opened.

If the socket is world-writable (`0666`), any local account can set global
limits (including `0`, which blocks all traffic) and can delete rules — treat
that as a misconfiguration rather than the intended mode. `throtl-cli doctor`
reports the effective socket permissions and `throtl-cli status` exposes them
as `socket.restricted`.
