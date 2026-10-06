"""Version check against the public GitHub release API.

This is the **only outbound network request** the app makes: a GET to the
public release API, with no account, no identifier, no user data. The check runs
in the user session (not in the root daemon), can be disabled and installs
nothing — on request the app only opens the release page in the default
browser.

Standard library only, no GTK: the logic is testable without a display and
without network (the HTTP opener is injectable). The dashboard mirrors the same
comparison logic in TypeScript (`throtl-app/src/lib/update.ts`).
"""

import json
import urllib.error
import urllib.request

from . import __version__

REPOSITORY = "Flenser42/throtl"
PROJECT_URL = f"https://github.com/{REPOSITORY}"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"{PROJECT_URL}/releases"
INSTALL_URL = f"{PROJECT_URL}#installation"
TIMEOUT = 3.0


def parse_tag(payload) -> str | None:
    """Read ``tag_name`` from an API response, without the ``v`` prefix."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _version_key(version):
    """Split a version into a comparable form (semver-like).

    Result ``(numeric_parts, is_release, prerelease_parts)`` or None. ``is_release``
    is 1 for a final version and 0 for a prerelease, so ``1.0.0`` is newer than
    ``1.0.0-rc1``. Build metadata after ``+`` is ignored.
    """
    if not version:
        return None
    text = str(version).strip().lstrip("vV")
    if not text:
        return None
    core = text.split("+", 1)[0]
    if "-" in core:
        numeric, prerelease = core.split("-", 1)
        is_release = 0
        identifiers = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in prerelease.split(".") if part != ""
        )
    else:
        numeric, is_release, identifiers = core, 1, ()
    parts = []
    for chunk in numeric.split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    if not parts:
        return None
    return (tuple(parts), is_release, identifiers)


def is_newer(current, latest) -> bool:
    """Is ``latest`` newer than ``current``?

    Compared numerically, not as text (``0.10.0`` is newer than ``0.9.0``). An
    unparseable version counts as "not newer" — better no hint than a wrong one.
    """
    cur, new = _version_key(current), _version_key(latest)
    if cur is None or new is None:
        return False
    width = max(len(cur[0]), len(new[0]))
    cur_nums = cur[0] + (0,) * (width - len(cur[0]))
    new_nums = new[0] + (0,) * (width - len(new[0]))
    return (new_nums, new[1], new[2]) > (cur_nums, cur[1], cur[2])


def fetch_latest(timeout: float = TIMEOUT, opener=None) -> str | None:
    """Fetch the newest release version.

    Any error (offline, rate limit, broken JSON) yields ``None``: the version
    check must never disturb the app.
    """
    request = urllib.request.Request(
        LATEST_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"Throtl/{__version__}"})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return parse_tag(payload)
