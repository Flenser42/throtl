"""Versionscheck gegen die oeffentliche GitHub-Release-API.

Das ist die **einzige ausgehende Netzwerkanfrage** der App: ein GET auf die
oeffentliche Release-API, ohne Konto, ohne Kennung, ohne Nutzerdaten. Der Check
laeuft in der Nutzer-Session (nicht im root-Daemon), ist abschaltbar und
installiert nichts — auf Wunsch oeffnet die App nur die Release-Seite im
Standardbrowser.

Nur Standardbibliothek, kein GTK: damit ist die Logik ohne Display und ohne Netz
testbar (der HTTP-Opener ist injizierbar).
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
    """``tag_name`` aus einer API-Antwort lesen, ohne ``v``-Praefix."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None
    return tag[1:] if tag[:1] in ("v", "V") else tag


def _parts(version) -> tuple[int, ...] | None:
    """Version in Zahlen zerlegen; None, wenn sie nicht lesbar ist."""
    if not version:
        return None
    text = str(version).strip().lstrip("vV")
    if not text:
        return None
    parts = []
    for chunk in text.split("."):
        head = chunk.split("-")[0].split("+")[0]
        if not head.isdigit():
            return None
        parts.append(int(head))
    return tuple(parts) or None


def is_newer(current, latest) -> bool:
    """Ist ``latest`` neuer als ``current``?

    Numerisch verglichen, nicht als Text (``0.10.0`` ist neuer als ``0.9.0``).
    Eine unlesbare Version zaehlt als "nicht neuer" — lieber kein Hinweis als
    ein falscher.
    """
    cur, new = _parts(current), _parts(latest)
    if cur is None or new is None:
        return False
    width = max(len(cur), len(new))
    cur += (0,) * (width - len(cur))
    new += (0,) * (width - len(new))
    return new > cur


def fetch_latest(timeout: float = TIMEOUT, opener=None) -> str | None:
    """Neueste Release-Version holen.

    Jeder Fehler (offline, Rate-Limit, kaputtes JSON) ergibt ``None``: der
    Versionscheck darf die App nie stoeren.
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
