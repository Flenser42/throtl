"""Small user-level GUI preferences (display state only).

Kept separate from the daemon config on purpose: the GUI runs as the user and
must not write into ``/etc/throtl``, and these are display preferences, not
throttling rules.
"""

import json
import os
from pathlib import Path

from .. import CONFIG_DIR_NAME


def prefs_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / CONFIG_DIR_NAME / "gui.json"


def load_prefs() -> dict:
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(data: dict) -> None:
    """Best-effort: eine nicht schreibbare Pref-Datei darf die GUI nicht killen."""
    path = prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
