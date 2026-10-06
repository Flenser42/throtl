# Security Policy

## Supported versions

Security fixes are provided for the latest release and `master`.

| Version | Supported |
|---------|-----------|
| 0.12.x  | ✅        |
| 0.11.x  | ⚠️ security fixes only |
| < 0.11  | ❌        |

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
user) can manage bandwidth limits. If the `throtl` group does not exist (a
manual start without `install.sh`), the daemon **fails closed** to `0600`
(root only) and logs a hint — it never falls back to a world-writable socket.
`throtl-cli doctor` reports the effective permissions and `throtl-cli status`
exposes them as `socket.restricted`. No network port is ever opened.

The trust boundary is therefore "anything running as root or as a member of
group `throtl`" — the GUI/CLI run in the user session and reach the daemon
through that socket only. `setup/install-app.sh` installs the dashboard
per-user, so it inherits exactly those rights and nothing more.

### Polkit setup (one-time, not per-toggle)

`setup/install.sh` installs a polkit policy
(`/usr/share/polkit-1/actions/org.throtl.setup.policy`, action
`org.throtl.setup` with `auth_admin`) and a root-owned helper
(`/usr/local/bin/throtl-setup`). The dashboard's "no access" state spawns
`pkexec /usr/local/bin/throtl-setup`; pkexec authenticates the admin and runs
the helper as root. The helper's only privileged action is adding the invoking
user to the `throtl` group (or, from a source checkout, delegating to
`setup/install.sh`).

This does **not** widen the trust boundary: the daemon stays root-only, the
socket stays `root:throtl 0660`, and the GUI still reaches the daemon only over
the socket. polkit is used for the one-time install/group grant, never for the
every-tick toggles. The helper is installed `root:root 0755` at a fixed,
non-user-writable path, and pkexec runs it with a sanitized environment
(`PKEXEC_UID` is the only way it learns the caller), so the webview can request
the setup without gaining arbitrary shell access. The action intentionally has
no `auth_admin_keep` variant because there is no recurring privileged toggle to
authorise.
