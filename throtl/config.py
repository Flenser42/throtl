"""Konfiguration: TOML unter ~/.config/throtl/config.toml.

Internes Schema (normalisierte Form, siehe ``default_config``):

    {
      "version": 1,
      "interface": "enp34s0" | None,   # None -> beim Daemon-Start automatisch ermitteln
      "unit": "kbps" | "kBs",          # Anzeige-Einheit der GUI
      "global": {
        "enabled": bool,
        "download_limit": int|None,    # kbit/s, None = unbegrenzt
        "upload_limit": int|None,
        "download_minimum": int,       # kbps, garantiert fuer nicht gematchten Traffic
        "upload_minimum": int,
        "download_priority": str,      # "kritisch"|"hoch"|"normal"|"niedrig"
        "upload_priority": str,
      },
      "processes": [                   # Regeln pro Anwendung
        {
          "key": "exe:/usr/lib/firefox/firefox",   # stabile Identitaet
          "name": "Firefox",
          "match_type": "exe"|"name"|"cmdline",
          "match_value": "...",        # exe/name: re.escape-t; cmdline: Regex
          "download_limit": int|None,
          "upload_limit": int|None,
          "priority": str,
          "recursive": bool,
        },
        ...
      ],
    }

TOML-Datei (flach, damit sie auch von Hand lesbar bleibt):

    version = 1
    interface = "enp34s0"
    unit = "kbps"

    [global]
    enabled = true
    download_limit = 1024
    ...

    [[processes]]
    key = "exe:/usr/lib/firefox/firefox"
    ...
"""

import os
import re
import tomllib
from pathlib import Path

from . import CONFIG_DIR_NAME

CONFIG_FILE_NAME = "config.toml"

# Prioritaeten. TrafficToll: kleinere Zahl = hoehere Prioritaet.
PRIORITY_NAMES = ("kritisch", "hoch", "normal", "niedrig")
PRIORITY_TO_INT = {"kritisch": 0, "hoch": 1, "normal": 2, "niedrig": 3}
PRIORITY_INT_TO_NAME = {value: name for name, value in PRIORITY_TO_INT.items()}

VALID_MATCH_TYPES = ("exe", "name", "cmdline")
MAX_PRIORITY_INT = max(PRIORITY_TO_INT.values())


class ConfigError(ValueError):
    """Ungueltige Konfiguration."""


def priority_to_int(priority) -> int:
    """Symbolischen Namen oder Integer in TrafficToll-Prioritaetszahl umwandeln."""
    if isinstance(priority, str):
        name = priority.strip().lower()
        if name not in PRIORITY_TO_INT:
            raise ConfigError(
                f"unbekannte Prioritaet {priority!r} (erlaubt: {', '.join(PRIORITY_NAMES)})"
            )
        return PRIORITY_TO_INT[name]
    if isinstance(priority, int) and 0 <= priority <= MAX_PRIORITY_INT:
        return priority
    raise ConfigError(f"ungueltige Prioritaet {priority!r}")


def priority_to_name(priority) -> str:
    if isinstance(priority, str) and priority in PRIORITY_TO_INT:
        return priority
    return PRIORITY_INT_TO_NAME.get(priority, "normal")


def _priority_or_error(priority) -> str:
    name = priority_to_name(priority)
    if isinstance(priority, str) and priority.lower().strip() not in PRIORITY_TO_INT:
        raise ConfigError(
            f"unbekannte Prioritaet {priority!r} (erlaubt: {', '.join(PRIORITY_NAMES)})"
        )
    return name


def rule_key(match_type: str, match_value: str) -> str:
    return f"{match_type}:{match_value}"


def _build_rule(
    name: str,
    match_type: str,
    match_value: str,
    download_limit=None,
    upload_limit=None,
    priority: str = "normal",
    recursive: bool = False,
    key: str | None = None,
    escape: bool = True,
) -> dict:
    """Neue Regel erzeugen (validiert).

    Für exe/name-Werte wird, wenn ``escape`` gesetzt ist, re.escape angewendet.
    Beim Laden aus TOML ist das Pattern bereits escaped -> escape=False.
    """
    if match_type not in VALID_MATCH_TYPES:
        raise ConfigError(f"ungueltiger match_type {match_type!r}")
    if not isinstance(match_value, str) or not match_value.strip():
        raise ConfigError("match_value darf nicht leer sein")
    if escape and match_type in ("exe", "name"):
        match_value = re.escape(match_value.strip())
    priority_to_int(priority)  # validieren
    return {
        "key": key or rule_key(match_type, match_value),
        "name": name or _default_name(match_value),
        "match_type": match_type,
        "match_value": match_value,
        "download_limit": _rate_or_none(download_limit),
        "upload_limit": _rate_or_none(upload_limit),
        "priority": priority_to_name(priority),
        "recursive": bool(recursive),
    }


def make_rule(
    name: str,
    match_type: str,
    match_value: str,
    download_limit=None,
    upload_limit=None,
    priority: str = "normal",
    recursive: bool = False,
    key: str | None = None,
) -> dict:
    """Neue Regel aus Rohwerten (GUI/CLI): exe/name werden regex-escaped."""
    return _build_rule(
        name, match_type, match_value, download_limit, upload_limit,
        priority, recursive, key, escape=True,
    )


def _default_name(match_value: str) -> str:
    return os.path.basename(match_value.rstrip("/")) or match_value


def _rate_or_none(value):
    if value is None:
        return None
    if isinstance(value, str):
        from .units import parse_rate

        return parse_rate(value)
    value = int(value)
    if value < 0:
        raise ConfigError(f"negative Rate ungueltig: {value}")
    return value


def default_config() -> dict:
    return {
        "version": 1,
        "interface": None,
        "unit": "kbps",
        "global": {
            "enabled": True,
            "download_limit": None,
            "upload_limit": None,
            "download_minimum": 100,
            "upload_minimum": 10,
            "download_priority": "normal",
            "upload_priority": "normal",
        },
        "processes": [],
    }


def config_dir_default() -> str:
    """Konfigurationsverzeichnis: $THROTL_CONFIG_DIR oder ~/.config/throtl."""
    env = os.environ.get("THROTL_CONFIG_DIR")
    if env:
        return env
    return str(Path.home() / ".config" / CONFIG_DIR_NAME)


def config_path_for(config_dir: str) -> str:
    return str(Path(config_dir) / CONFIG_FILE_NAME)


def normalize(data: dict) -> dict:
    """Rohe (aus TOML geladene) Daten normalisieren und validieren."""
    cfg = default_config()
    cfg["interface"] = (data.get("interface") or None) if isinstance(data.get("interface"), str) else None
    from .units import DISPLAY_UNITS

    unit = data.get("unit", "kbps")
    if unit not in DISPLAY_UNITS:
        raise ConfigError(f"ungueltige Anzeige-Einheit {unit!r}")
    cfg["unit"] = unit

    raw_global = data.get("global") or {}
    g = cfg["global"]
    g["enabled"] = bool(raw_global.get("enabled", True))
    g["download_limit"] = _rate_or_none(raw_global.get("download_limit"))
    g["upload_limit"] = _rate_or_none(raw_global.get("upload_limit"))
    g["download_minimum"] = _rate_or_none(raw_global.get("download_minimum")) or 100
    g["upload_minimum"] = _rate_or_none(raw_global.get("upload_minimum")) or 10
    g["download_priority"] = _priority_or_error(raw_global.get("download_priority", "normal"))
    g["upload_priority"] = _priority_or_error(raw_global.get("upload_priority", "normal"))

    processes = []
    for raw in data.get("processes") or []:
        if not isinstance(raw, dict):
            continue
        try:
            match_type = raw.get("match_type") or raw.get("type")
            match_value = raw.get("match_value") or raw.get("value")
            if not match_type or not match_value:
                raise ConfigError("match_type/match_value fehlen")
            rule = _build_rule(
                name=str(raw.get("name") or ""),
                match_type=str(match_type),
                match_value=str(match_value),
                download_limit=_rate_or_none(raw.get("download_limit")),
                upload_limit=_rate_or_none(raw.get("upload_limit")),
                priority=str(raw.get("priority") or "normal"),
                recursive=bool(raw.get("recursive", False)),
                key=str(raw.get("key") or ""),
                escape=False,
            )
            processes.append(rule)
        except ConfigError:
            continue  # kaputte Regel beim Laden ueberspringen
    cfg["processes"] = processes
    return cfg


def load_config(path) -> dict:
    """Konfiguration laden (normalisiert). Fehlende Datei -> Defaults."""
    if not os.path.exists(path):
        return default_config()
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return normalize(data)


# --------------------------------------------------------------------------
# Minimaler TOML-Schreiber (bewusst handgerollt: keine externen Dependencies,
# Schema ist bekannt und flach; Tests decken Escaping/Sonderfaelle ab).
# --------------------------------------------------------------------------

def _quote(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _scalar(value):
    """TOML-Skalar rendern; None -> None (Schluessel wird ausgelassen)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    raise TypeError(f"nicht als TOML-Skalar darstellbar: {value!r}")


_GLOBAL_KEYS = (
    "enabled",
    "download_limit",
    "upload_limit",
    "download_minimum",
    "upload_minimum",
    "download_priority",
    "upload_priority",
)

_PROCESS_KEYS = (
    "key",
    "name",
    "match_type",
    "match_value",
    "download_limit",
    "upload_limit",
    "priority",
    "recursive",
)


def dump_config(config: dict) -> str:
    lines = ["version = 1"]
    if config.get("interface"):
        lines.append(f"interface = {_quote(config['interface'])}")
    lines.append(f"unit = {_quote(config.get('unit', 'kbps'))}")
    lines.append("")

    lines.append("[global]")
    for key in _GLOBAL_KEYS:
        value = _scalar(config["global"].get(key))
        if value is None:
            continue
        lines.append(f"{key} = {value}")
    lines.append("")

    for rule in config.get("processes", []):
        lines.append("[[processes]]")
        for key in _PROCESS_KEYS:
            value = _scalar(rule.get(key))
            if value is None:
                continue
            lines.append(f"{key} = {value}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_config(path, config: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(dump_config(config))


def detect_default_interface() -> str | None:
    """Standard-Routing-Interface ermitteln (rein aus /proc, ohne root)."""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as handle:
            next(handle, None)  # Kopfzeile
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000" and fields[2] != "00000000":
                    return fields[0]
    except OSError:
        pass
    # Fallback: erstes Nicht-Loopback-Interface mit Traffic
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                name, _rest = line.split(":", 1)
                name = name.strip()
                if name == "lo":
                    continue
                return name
    except OSError:
        pass
    return None


def matching_rules(processes, exe: str | None = None, name: str | None = None,
                   cmdline: str | None = None):
    """Regeln finden, die auf einen Prozess passen (regex, wie TrafficToll)."""
    result = []
    for rule in processes:
        match_type = rule["match_type"]
        pattern = rule["match_value"]
        candidate = {"exe": exe, "name": name, "cmdline": cmdline}[match_type]
        if candidate is None:
            continue
        try:
            if re.search(pattern, candidate):
                result.append(rule)
        except re.error:
            continue
    return result
