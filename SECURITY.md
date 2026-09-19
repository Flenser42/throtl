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

The socket is intentionally world-connectable (`0666`) so that any local user
can manage bandwidth limits; that alone is **not** considered a vulnerability.
No network port is ever opened.
