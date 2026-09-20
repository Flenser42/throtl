# Security Policy

## Supported versions

Security fixes are provided for the latest release and `master`.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

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
