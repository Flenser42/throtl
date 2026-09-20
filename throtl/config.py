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

import copy
import os
import re
import tomllib
from pathlib import Path

from . import CONFIG_DIR_NAME, write_text_atomic

CONFIG_FILE_NAME = "config.toml"

# Letzte Warnung aus load_config() (kaputte/ungueltige Datei). Wird vom Daemon
# ueber status() nach aussen gereicht, damit ein Config-Problem sichtbar ist,
# ohne dass der Dienst deshalb nicht startet.
_LAST_CONFIG_WARNING: str | None = None

# Prioritaeten. TrafficToll: kleinere Zahl = hoehere Prioritaet.
PRIORITY_NAMES = ("kritisch", "hoch", "normal", "niedrig")
PRIORITY_TO_INT = {"kritisch": 0, "hoch": 1, "normal": 2, "niedrig": 3}
PRIORITY_INT_TO_NAME = {value: name for name, value in PRIORITY_TO_INT.items()}

VALID_MATCH_TYPES = ("exe", "name", "cmdline")
MAX_PRIORITY_INT = max(PRIORITY_TO_INT.values())

# Profile + Zeitplaene (additiv zu v0.1.0). "Standard" ist immer vorhanden und
# entspricht dem Top-Level-Zustand; profile_names() liefert ihn an Position 0.
STANDARD_PROFILE = "Standard"
MAX_PROFILE_NAME_LENGTH = 64
WEEKDAY_TOKENS = ("mo", "di", "mi", "do", "fr", "sa", "so")
# Akzeptierte Schreibweisen fuer Wochentage (deutsch + englisch + Zahl).
_WEEKDAY_ALIASES = {
    "mo": 0, "montag": 0, "mon": 0, "monday": 0, "0": 0,
    "di": 1, "dienstag": 1, "die": 1, "tue": 1, "tues": 1, "tuesday": 1, "1": 1,
    "mi": 2, "mittwoch": 2, "mit": 2, "wed": 2, "wednesday": 2, "2": 2,
    "do": 3, "donnerstag": 3, "don": 3, "thu": 3, "thur": 3, "thurs": 3,
    "thursday": 3, "3": 3,
    "fr": 4, "freitag": 4, "fre": 4, "fri": 4, "friday": 4, "4": 4,
    "sa": 5, "samstag": 5, "sam": 5, "sat": 5, "saturday": 5, "5": 5,
    "so": 6, "sonntag": 6, "son": 6, "sun": 6, "sunday": 6, "6": 6,
}


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


def _size_or_none(value):
    """Volumen (Bytes) parsen; '20GB'/'5 GiB'/None -> int|None."""
    if value is None:
        return None
    from .units import parse_size

    return parse_size(value)


def default_config() -> dict:
    return {
        "version": 1,
        "interface": None,
        "unit": "kbps",
        # Aktives Profil + Profilkatalog + Zeitplaene (additiv, v0.1.0-kompatibel).
        "active_profile": STANDARD_PROFILE,
        "profiles": {},
        "schedule": [],
        # Verbrauchs-Budgets (Bytes, rollierend: day=letzte 24h, week=7 Tage).
        "budgets": {"enabled": True, "day": None, "week": None, "rules": []},
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
        rule = _normalize_rule(raw)
        if rule is not None:
            processes.append(rule)
    cfg["processes"] = processes

    # --- Profile + Zeitplaene (additiv) -----------------------------------
    # Kaputte Eintraege werden uebersprungen (wie bei processes), damit eine
    # aeltere/handgeschriebene Config weiterhin ladbar ist.
    try:
        cfg["active_profile"] = validate_profile_name(
            data.get("active_profile", STANDARD_PROFILE)
        )
    except ConfigError:
        cfg["active_profile"] = STANDARD_PROFILE
    profiles = {}
    raw_profiles = data.get("profiles") or {}
    if isinstance(raw_profiles, dict):
        for name, raw in raw_profiles.items():
            try:
                profiles[validate_profile_name(name)] = _normalize_profile(name, raw)
            except ConfigError:
                continue
    cfg["profiles"] = profiles
    cfg["schedule"] = normalize_schedule(data.get("schedule"))

    # --- Budgets (additiv) -------------------------------------------------
    raw_budgets = data.get("budgets") or {}
    budgets = cfg["budgets"]
    if isinstance(raw_budgets, dict):
        budgets["enabled"] = bool(raw_budgets.get("enabled", True))
        for key in ("day", "week"):
            try:
                budgets[key] = _size_or_none(raw_budgets.get(key))
            except (ConfigError, ValueError):
                budgets[key] = None
    rules = []
    for raw in data.get("budget_rules") or []:
        if not isinstance(raw, dict):
            continue
        app = str(raw.get("app") or "").strip()
        if not app:
            continue
        entry = {"app": app}
        for key in ("day", "week"):
            try:
                entry[key] = _size_or_none(raw.get(key))
            except (ConfigError, ValueError):
                entry[key] = None
        rules.append(entry)
    budgets["rules"] = rules
    return cfg


def _normalize_rule(raw) -> dict | None:
    """Eine rohe (TOML-)Regel bauen; ungueltige liefern ``None``."""
    if not isinstance(raw, dict):
        return None
    try:
        match_type = raw.get("match_type") or raw.get("type")
        match_value = raw.get("match_value") or raw.get("value")
        if not match_type or not match_value:
            raise ConfigError("match_type/match_value fehlen")
        return _build_rule(
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
    except (ConfigError, ValueError):
        return None


def _normalize_profile(name, raw) -> dict:
    """Ein Profil in die interne Form ``{global, processes}`` bringen.

    Das Profil-Global darf sowohl als verschachtelte Tabelle ``[profiles.X.global]``
    als auch flach mit ``global_*``-Keys geschrieben sein (letzteres ist das
    Format aus der Aufgabenstellung und der Doku).
    """
    name = validate_profile_name(name)
    if not isinstance(raw, dict):
        raise ConfigError(f"Profil {name!r} muss eine Tabelle sein")
    profile = {"global": {}, "processes": []}
    candidates = {}
    nested = raw.get("global")
    if isinstance(nested, dict):
        for key in ("enabled", "download_limit", "upload_limit",
                    "download_minimum", "upload_minimum",
                    "download_priority", "upload_priority"):
            if key in nested:
                candidates[key] = nested[key]
    flat_map = {
        "global_enabled": "enabled",
        "global_download_limit": "download_limit",
        "global_upload_limit": "upload_limit",
        "global_download_minimum": "download_minimum",
        "global_upload_minimum": "upload_minimum",
        "global_download_priority": "download_priority",
        "global_upload_priority": "upload_priority",
    }
    for flat_key, key in flat_map.items():
        if flat_key in raw:
            candidates[key] = raw[flat_key]
    if "global_priority" in raw:
        # Ein einzelner Priority-Wert gilt fuer Download UND Upload.
        candidates["download_priority"] = raw["global_priority"]
        candidates["upload_priority"] = raw["global_priority"]

    g = profile["global"]
    if "enabled" in candidates:
        g["enabled"] = bool(candidates["enabled"])
    for key in ("download_limit", "upload_limit",
                "download_minimum", "upload_minimum"):
        if key in candidates:
            try:
                g[key] = _rate_or_none(candidates[key])
            except (ConfigError, ValueError):
                pass
    for key in ("download_priority", "upload_priority"):
        if key in candidates:
            try:
                g[key] = _priority_or_error(candidates[key])
            except ConfigError:
                pass
    for raw_rule in raw.get("processes") or []:
        rule = _normalize_rule(raw_rule)
        if rule is not None:
            profile["processes"].append(rule)
    return profile


def validate_profile_name(name) -> str:
    """Profilnamen pruefen; gibt den bereinigten Namen zurueck."""
    if not isinstance(name, str):
        raise ConfigError("Profilname muss Text sein")
    name = name.strip()
    if not name:
        raise ConfigError("Profilname darf nicht leer sein")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise ConfigError(
            f"Profilname zu lang (max. {MAX_PROFILE_NAME_LENGTH} Zeichen)"
        )
    for char in name:
        if not (char.isalnum() or char in "_- "):
            raise ConfigError(f"ungueltiges Zeichen {char!r} im Profilnamen")
    return name


def parse_days(value) -> set:
    """Wochentage parsen: ``["mo","di"]`` oder ``"mo-fr"`` -> ``{0,1,...}``.

    Mo=0 ... So=6. Unbekannte Tokens werden ignoriert (robustes Laden).
    """
    if value is None:
        return set()
    if isinstance(value, str):
        tokens = [value]
    elif isinstance(value, (list, tuple, set)):
        tokens = list(value)
    else:
        return set()
    days = set()
    for token in tokens:
        if isinstance(token, int):
            if 0 <= token <= 6:
                days.add(token)
            continue
        text = str(token).strip().lower()
        if not text:
            continue
        if "-" in text:
            start_text, _, end_text = text.partition("-")
            start = _WEEKDAY_ALIASES.get(start_text.strip())
            end = _WEEKDAY_ALIASES.get(end_text.strip())
            if start is None or end is None:
                continue
            if start <= end:
                days.update(range(start, end + 1))
            else:  # umlaufende Range, z.B. "fr-mo"
                days.update(range(start, 7))
                days.update(range(0, end + 1))
        else:
            day = _WEEKDAY_ALIASES.get(text)
            if day is not None:
                days.add(day)
    return days


def _parse_time(value) -> str | None:
    """``"8:5"``/``"08:05"`` -> ``"08:05"``; ungueltig -> ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if ":" not in text:
        return None
    hour_text, _, minute_text = text.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _time_to_minutes(text: str) -> int:
    hour, _, minute = text.partition(":")
    return int(hour) * 60 + int(minute)


def normalize_schedule(rules) -> list:
    """Rohe Zeitplan-Regeln validieren; kaputte werden uebersprungen."""
    result = []
    for raw in rules or []:
        if not isinstance(raw, dict):
            continue
        try:
            name = validate_profile_name(raw.get("profile"))
        except ConfigError:
            continue
        days = parse_days(raw.get("days"))
        if not days:
            continue
        start = _parse_time(raw.get("start"))
        end = _parse_time(raw.get("end"))
        if start is None or end is None:
            continue
        result.append({"profile": name, "days": sorted(days),
                       "start": start, "end": end})
    return result


def profile_names(cfg) -> list:
    """Alle Profilnamen; ``"Standard"`` steht immer an Position 0."""
    names = [STANDARD_PROFILE]
    for name in (cfg.get("profiles") or {}):
        if name != STANDARD_PROFILE:
            names.append(name)
    return names


def get_profile(cfg, name) -> dict | None:
    """Profil als ``{"global":…, "processes":[…]}`` oder ``None``.

    ``"Standard"`` ist auch ohne gespeicherten Eintrag bekannt und entspricht
    dem aktuellen Top-Level-Zustand.
    """
    name = validate_profile_name(name)
    profiles = cfg.get("profiles") or {}
    if name in profiles:
        profile = profiles[name] or {}
        return {
            "global": copy.deepcopy(profile.get("global") or {}),
            "processes": copy.deepcopy(profile.get("processes") or []),
        }
    if name == STANDARD_PROFILE:
        return {
            "global": copy.deepcopy(cfg.get("global") or {}),
            "processes": copy.deepcopy(cfg.get("processes") or []),
        }
    return None


def apply_profile(cfg, name) -> dict:
    """Profilwerte in die Top-Level-``global``/``processes`` kopieren.

    Unbekanntes (und nicht ``"Standard"``) -> :class:`ConfigError`.
    """
    name = validate_profile_name(name)
    profile = get_profile(cfg, name)
    if profile is None:
        raise ConfigError(f"unbekanntes Profil {name!r}")
    global_cfg = cfg.setdefault("global", {})
    for key, value in profile["global"].items():
        global_cfg[key] = copy.deepcopy(value)
    cfg["processes"] = copy.deepcopy(profile["processes"])
    cfg["active_profile"] = name
    return cfg


def capture_profile(cfg, name) -> dict:
    """Aktuellen Top-Level-Zustand als Profil sichern."""
    name = validate_profile_name(name)
    profile = {
        "global": copy.deepcopy(cfg.get("global") or {}),
        "processes": copy.deepcopy(cfg.get("processes") or []),
    }
    cfg.setdefault("profiles", {})[name] = profile
    cfg["active_profile"] = name
    return profile


def delete_profile(cfg, name) -> bool:
    """Profil loeschen; ``True``, wenn es existierte."""
    try:
        name = validate_profile_name(name)
    except ConfigError:
        return False
    profiles = cfg.get("profiles") or {}
    if name not in profiles:
        return False
    del profiles[name]
    if cfg.get("active_profile") == name:
        cfg["active_profile"] = STANDARD_PROFILE
    return True


def active_scheduled_profile(cfg, when=None) -> str | None:
    """Welches Profil ist laut ``schedule`` jetzt aktiv? (erste Regel gewinnt)

    ``when`` ist ein ``datetime`` (Default: jetzt). Ueber Mitternacht
    (``end < start``) gilt die Regel bis in den Folgetag: der Morgenteil zaehlt
    fuer den Wochentag des Starttags.
    """
    if when is None:
        from datetime import datetime

        when = datetime.now()
    weekday = when.weekday()
    now_minutes = when.hour * 60 + when.minute
    for rule in cfg.get("schedule") or []:
        name = rule.get("profile")
        days = set(rule.get("days") or [])
        start = rule.get("start")
        end = rule.get("end")
        if not name or not days or not start or not end:
            continue
        start_minutes = _time_to_minutes(start)
        end_minutes = _time_to_minutes(end)
        if start_minutes <= end_minutes:
            if weekday in days and start_minutes <= now_minutes < end_minutes:
                return name
        else:
            # Abendteil am Starttag ...
            if weekday in days and now_minutes >= start_minutes:
                return name
            # ... Morgenteil am Folgetag.
            if ((weekday - 1) % 7) in days and now_minutes < end_minutes:
                return name
    return None


def load_config(path) -> dict:
    """Konfiguration laden (normalisiert). Fehlende Datei -> Defaults.

    Eine syntaktisch kaputte TOML-Datei darf den Daemon nicht unstartbar
    machen: frueher propagierte ``tomllib.load`` den ParseError direkt, der
    Daemon starb beim Start und liess sich nur durch Loeschen der Config
    wiederbeleben. Jetzt fallen wir auf die Defaults zurueck und melden den
    Grund ueber ``CONFIG_WARNINGS`` (siehe :func:`last_config_warning`).
    """
    global _LAST_CONFIG_WARNING
    _LAST_CONFIG_WARNING = None
    if not os.path.exists(path):
        return default_config()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as error:
        _LAST_CONFIG_WARNING = (
            f"Config {path} konnte nicht gelesen werden "
            f"({type(error).__name__}: {error}) — es gelten die Defaults."
        )
        print(f"Warnung: {_LAST_CONFIG_WARNING}", flush=True)
        return default_config()
    return normalize(data)


def last_config_warning() -> str | None:
    """Letzte Warnung aus :func:`load_config` (None = alles in Ordnung)."""
    return _LAST_CONFIG_WARNING


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


def _toml_key(key: str) -> str:
    """Tabellen-Schluessel rendern; bei Sonderzeichen/zwei Leerzeichen quoten."""
    if re.match(r"^[A-Za-z0-9_-]+$", key or ""):
        return key
    return _quote(key)


def _string_array(values) -> str:
    return "[" + ", ".join(_quote(str(value)) for value in values) + "]"


def _days_to_tokens(days) -> list:
    tokens = []
    for day in sorted(set(days or [])):
        if isinstance(day, int) and 0 <= day <= 6:
            tokens.append(WEEKDAY_TOKENS[day])
    return tokens


def dump_config(config: dict) -> str:
    lines = ["version = 1"]
    if config.get("interface"):
        lines.append(f"interface = {_quote(config['interface'])}")
    lines.append(f"unit = {_quote(config.get('unit', 'kbps'))}")
    lines.append(
        f"active_profile = {_quote(config.get('active_profile', STANDARD_PROFILE))}"
    )
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

    # --- Profile (verschachtelt: [[profiles.NAME.processes]]) --------------
    _PROFILE_GLOBAL = (
        ("global_enabled", "enabled"),
        ("global_download_limit", "download_limit"),
        ("global_upload_limit", "upload_limit"),
        ("global_download_minimum", "download_minimum"),
        ("global_upload_minimum", "upload_minimum"),
    )
    for name, profile in (config.get("profiles") or {}).items():
        profile = profile or {}
        table = f"profiles.{_toml_key(name)}"
        lines.append(f"[{table}]")
        global_cfg = profile.get("global") or {}
        for out_key, key in _PROFILE_GLOBAL:
            value = _scalar(global_cfg.get(key))
            if value is not None:
                lines.append(f"{out_key} = {value}")
        download_priority = global_cfg.get("download_priority")
        upload_priority = global_cfg.get("upload_priority")
        if download_priority is not None and download_priority == upload_priority:
            lines.append(f"global_priority = {_quote(download_priority)}")
        else:
            if download_priority is not None:
                lines.append(f"global_download_priority = {_quote(download_priority)}")
            if upload_priority is not None:
                lines.append(f"global_upload_priority = {_quote(upload_priority)}")
        lines.append("")
        for rule in profile.get("processes") or []:
            lines.append(f"[[{table}.processes]]")
            for key in _PROCESS_KEYS:
                value = _scalar(rule.get(key))
                if value is None:
                    continue
                lines.append(f"{key} = {value}")
            lines.append("")

    # --- Zeitplaene (flache Array-of-Tables) -------------------------------
    for rule in config.get("schedule") or []:
        lines.append("[[schedule]]")
        lines.append(f"profile = {_quote(str(rule.get('profile', '')))}")
        lines.append(f"days = {_string_array(_days_to_tokens(rule.get('days')))}")
        lines.append(f"start = {_quote(str(rule.get('start', '')))}")
        lines.append(f"end = {_quote(str(rule.get('end', '')))}")
        lines.append("")

    # --- Verbrauchs-Budgets ------------------------------------------------
    budgets = config.get("budgets") or {}
    budgets_active = (budgets.get("day") or budgets.get("week")
                      or budgets.get("rules") or budgets.get("enabled") is False)
    if budgets_active:
        lines.append("[budgets]")
        lines.append(
            f"enabled = {'true' if budgets.get('enabled', True) else 'false'}"
        )
        for key in ("day", "week"):
            value = budgets.get(key)
            if value is not None:
                lines.append(f"{key} = {int(value)}")
        lines.append("")
        for rule in budgets.get("rules") or []:
            lines.append("[[budget_rules]]")
            lines.append(f"app = {_quote(str(rule.get('app', '')))}")
            for key in ("day", "week"):
                value = rule.get(key)
                if value is not None:
                    lines.append(f"{key} = {int(value)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_config(path, config: dict, mode: int | None = None) -> None:
    """Config atomar schreiben (temp + fsync + os.replace, siehe __init__)."""
    write_text_atomic(path, dump_config(config), mode=mode)


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
