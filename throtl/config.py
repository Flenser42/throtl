"""Configuration: TOML under ~/.config/throtl/config.toml.

Internal schema (normalised form, see ``default_config``):

    {
      "version": 1,
      "interface": "enp34s0" | None,   # None -> auto-detect at daemon start
      "unit": "kbps" | "kBs",          # display unit of the GUI
      "global": {
        "enabled": bool,
        "download_limit": int|None,    # kbit/s, None = unlimited
        "upload_limit": int|None,
        "download_minimum": int,       # kbps, guaranteed for unmatched traffic
        "upload_minimum": int,
        "download_priority": str,      # "kritisch"|"hoch"|"normal"|"niedrig"
        "upload_priority": str,
      },
      "processes": [                   # rules per application
        {
          "key": "exe:/usr/lib/firefox/firefox",   # stable identity
          "name": "Firefox",
          "match_type": "exe"|"name"|"cmdline",
          "match_value": "...",        # exe/name: re.escape-d; cmdline: regex
          "download_limit": int|None,
          "upload_limit": int|None,
          "priority": str,
          "recursive": bool,
        },
        ...
      ],
    }

TOML file (flat, so it stays readable by hand):

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
import shutil
import time
import tomllib
from pathlib import Path

from . import CONFIG_DIR_NAME, write_text_atomic

CONFIG_FILE_NAME = "config.toml"

# Last warning from load_config() (broken/invalid file). Surfaced by the daemon
# through status() so a config problem stays visible without the service
# failing to start because of it.
_LAST_CONFIG_WARNING: str | None = None

# Priorities. TrafficToll: smaller number = higher priority.
PRIORITY_NAMES = ("kritisch", "hoch", "normal", "niedrig")
PRIORITY_TO_INT = {"kritisch": 0, "hoch": 1, "normal": 2, "niedrig": 3}
PRIORITY_INT_TO_NAME = {value: name for name, value in PRIORITY_TO_INT.items()}
# Display names for the UI (English); the keys are stable.
PRIORITY_LABELS = {
    "kritisch": "Critical",
    "hoch": "High",
    "normal": "Normal",
    "niedrig": "Low",
}

VALID_MATCH_TYPES = ("exe", "name", "cmdline")
MAX_PRIORITY_INT = max(PRIORITY_TO_INT.values())

# Profiles + schedules (additive on top of v0.1.0). "Standard" always exists and
# corresponds to the top-level state; profile_names() returns it at position 0.
STANDARD_PROFILE = "Standard"
MAX_PROFILE_NAME_LENGTH = 64
WEEKDAY_TOKENS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Accepted spellings for weekdays (German + English + number).
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
    """Invalid configuration."""


def priority_to_int(priority) -> int:
    """Convert a symbolic name or integer into a TrafficToll priority number."""
    if isinstance(priority, str):
        name = priority.strip().lower()
        if name not in PRIORITY_TO_INT:
            raise ConfigError(
                f"unknown priority {priority!r} (allowed: {', '.join(PRIORITY_NAMES)})"
            )
        return PRIORITY_TO_INT[name]
    # ``bool`` is an ``int``: ``True``/``False`` would otherwise be 1/0 ("critical").
    if isinstance(priority, bool):
        raise ConfigError(f"invalid priority {priority!r}")
    if isinstance(priority, int) and 0 <= priority <= MAX_PRIORITY_INT:
        return priority
    raise ConfigError(f"invalid priority {priority!r}")


def priority_to_name(priority) -> str:
    if isinstance(priority, str) and priority in PRIORITY_TO_INT:
        return priority
    return PRIORITY_INT_TO_NAME.get(priority, "normal")


def _priority_or_error(priority) -> str:
    name = priority_to_name(priority)
    if isinstance(priority, str) and priority.lower().strip() not in PRIORITY_TO_INT:
        raise ConfigError(
            f"unknown priority {priority!r} (allowed: {', '.join(PRIORITY_NAMES)})"
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
    window=None,
) -> dict:
    """Create a new rule (validated).

    For exe/name values, ``re.escape`` is applied when ``escape`` is set. When
    loading from TOML the pattern is already escaped -> escape=False.
    """
    if match_type not in VALID_MATCH_TYPES:
        raise ConfigError(f"invalid match_type {match_type!r}")
    if not isinstance(match_value, str) or not match_value.strip():
        raise ConfigError("match_value must not be empty")
    if escape and match_type in ("exe", "name"):
        match_value = re.escape(match_value.strip())
    priority_to_int(priority)  # validate
    return {
        "key": key or rule_key(match_type, match_value),
        "name": name or _default_name(match_value),
        "match_type": match_type,
        "match_value": match_value,
        "download_limit": _rate_or_none(download_limit),
        "upload_limit": _rate_or_none(upload_limit),
        "priority": priority_to_name(priority),
        "recursive": bool(recursive),
        # Optional time window (None = always active).
        "window": normalize_window(window),
    }


def unescape_pattern(pattern) -> str:
    """Undo ``re.escape``.

    Stored ``match_value`` patterns are escaped for TrafficToll (where they are
    used as a regex). Whoever wants to compare them with a real path or process
    name must convert them back first.
    """
    try:
        return re.sub(r"\\(.)", r"\1", pattern or "")
    except re.error:
        return pattern or ""


def make_rule(
    name: str,
    match_type: str,
    match_value: str,
    download_limit=None,
    upload_limit=None,
    priority: str = "normal",
    recursive: bool = False,
    key: str | None = None,
    window=None,
) -> dict:
    """Create a new rule from raw values (GUI/CLI): exe/name get regex-escaped."""
    return _build_rule(
        name, match_type, match_value, download_limit, upload_limit,
        priority, recursive, key, escape=True, window=window,
    )


def _default_name(match_value: str) -> str:
    return os.path.basename(match_value.rstrip("/")) or match_value


def _rate_or_none(value):
    """Parse a rate and report errors as ConfigError (uniform for callers).

    ``parse_rate`` rejects bool, ``inf``/``nan`` and absurd values; that is
    mapped to ConfigError here so every call site only catches one type.
    """
    if value is None:
        return None
    from .units import parse_rate

    try:
        return parse_rate(value)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def _size_or_none(value):
    """Parse a volume (bytes); '20GB'/'5 GiB'/None -> int|None."""
    if value is None:
        return None
    from .units import parse_size

    try:
        return parse_size(value)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def default_config() -> dict:
    return {
        "version": 1,
        "interface": None,
        "unit": "kbps",
        # Active profile + profile catalog + schedules (additive, v0.1.0-compatible).
        "active_profile": STANDARD_PROFILE,
        # Profile the daemon activates at every start (None = off).
        "start_profile": None,
        "profiles": {},
        "schedule": [],
        # Consumption budgets (bytes, rolling: day=last 24h, week=7 days).
        "budgets": {"enabled": True, "enforce": False, "floor": None,
                    "day": None, "week": None, "rules": []},
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
    """Configuration directory: $THROTL_CONFIG_DIR or ~/.config/throtl."""
    env = os.environ.get("THROTL_CONFIG_DIR")
    if env:
        return env
    return str(Path.home() / ".config" / CONFIG_DIR_NAME)


def config_path_for(config_dir: str) -> str:
    return str(Path(config_dir) / CONFIG_FILE_NAME)


def normalize(data: dict, *, lenient: bool = False, notes: list | None = None) -> dict:
    """Normalise and validate raw (TOML-loaded) data.

    ``lenient`` (only for :func:`load_config`): a single invalid value in
    ``[global]`` resets just that field to the default instead of discarding the
    whole file — otherwise one typo would take down all rules, profiles and
    budgets. What was reset lands in ``notes``. ``import_config`` stays strict
    (``lenient=False``) and rejects garbage.
    """
    repaired: list = [] if notes is None else notes

    def repair(produce, fallback, what):
        try:
            return produce()
        except (ConfigError, ValueError, TypeError, OverflowError) as error:
            if not lenient:
                raise
            repaired.append(f"{what}={error}")
            return fallback

    cfg = default_config()
    cfg["interface"] = (data.get("interface") or None) if isinstance(data.get("interface"), str) else None
    from .units import DISPLAY_UNITS

    unit = data.get("unit", "kbps")

    def pick_unit():
        if unit not in DISPLAY_UNITS:
            raise ConfigError(f"invalid display unit {unit!r}")
        return unit

    cfg["unit"] = repair(pick_unit, "kbps", "unit")

    raw_global = data.get("global")
    if not isinstance(raw_global, dict):
        if raw_global is not None:
            repaired.append(f"global={raw_global!r} is not a table")
        raw_global = {}
    g = cfg["global"]
    g["enabled"] = bool(raw_global.get("enabled", True))
    g["download_limit"] = repair(
        lambda: _rate_or_none(raw_global.get("download_limit")), None, "download_limit"
    )
    g["upload_limit"] = repair(
        lambda: _rate_or_none(raw_global.get("upload_limit")), None, "upload_limit"
    )
    # ``0`` is a valid value ("no minimum"); only None falls back to the
    # default. Truthiness (``or 100``) silently turned 0 into 100/10.
    download_minimum = repair(
        lambda: _rate_or_none(raw_global.get("download_minimum")), None, "download_minimum"
    )
    g["download_minimum"] = 100 if download_minimum is None else download_minimum
    upload_minimum = repair(
        lambda: _rate_or_none(raw_global.get("upload_minimum")), None, "upload_minimum"
    )
    g["upload_minimum"] = 10 if upload_minimum is None else upload_minimum
    g["download_priority"] = repair(
        lambda: _priority_or_error(raw_global.get("download_priority", "normal")),
        "normal",
        "download_priority",
    )
    g["upload_priority"] = repair(
        lambda: _priority_or_error(raw_global.get("upload_priority", "normal")),
        "normal",
        "upload_priority",
    )

    processes = []
    for raw in data.get("processes") or []:
        rule = _normalize_rule(raw)
        if rule is not None:
            processes.append(rule)
    cfg["processes"] = processes

    # --- Profiles + schedules (additive) -----------------------------------
    # Broken entries are skipped (like processes), so an older/hand-written
    # config stays loadable.
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

    start_profile = data.get("start_profile")
    if isinstance(start_profile, str) and start_profile.strip():
        try:
            cfg["start_profile"] = validate_profile_name(start_profile)
        except ConfigError:
            cfg["start_profile"] = None
    else:
        cfg["start_profile"] = None

    # --- Budgets (additiv) -------------------------------------------------
    raw_budgets = data.get("budgets") or {}
    budgets = cfg["budgets"]
    if isinstance(raw_budgets, dict):
        budgets["enabled"] = bool(raw_budgets.get("enabled", True))
        budgets["enforce"] = bool(raw_budgets.get("enforce", False))
        try:
            budgets["floor"] = _rate_or_none(raw_budgets.get("floor"))
        except (ConfigError, ValueError, OverflowError) as error:
            repaired.append(f"budgets.floor={error}")
            budgets["floor"] = None
        for key in ("day", "week"):
            try:
                budgets[key] = _size_or_none(raw_budgets.get(key))
            except (ConfigError, ValueError, OverflowError) as error:
                repaired.append(f"budgets.{key}={error}")
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
            except (ConfigError, ValueError, OverflowError) as error:
                repaired.append(f"budget_rules[{app}].{key}={error}")
                entry[key] = None
        rules.append(entry)
    budgets["rules"] = rules
    return cfg


def _window_from_toml(raw: dict) -> dict | None:
    """Read a TOML rule's time window: nested OR flat."""
    if "window" in raw:
        window = normalize_window(raw.get("window"))
        if window:
            return window
    return normalize_window({
        "days": raw.get("window_days"),
        "start": raw.get("window_start"),
        "end": raw.get("window_end"),
    })


def _normalize_rule(raw) -> dict | None:
    """Build a raw (TOML) rule; invalid ones return ``None``."""
    if not isinstance(raw, dict):
        return None
    try:
        match_type = raw.get("match_type") or raw.get("type")
        match_value = raw.get("match_value") or raw.get("value")
        if not match_type or not match_value:
            raise ConfigError("match_type/match_value missing")
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
            window=_window_from_toml(raw),
        )
    except (ConfigError, ValueError):
        return None


def _normalize_profile(name, raw) -> dict:
    """Bring a profile into the internal ``{global, processes}`` form.

    The profile global may be written either as a nested table
    ``[profiles.X.global]`` or flat with ``global_*`` keys (the latter is the
    format from the spec and the docs).
    """
    name = validate_profile_name(name)
    if not isinstance(raw, dict):
        raise ConfigError(f"profile {name!r} must be a table")
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
        # A single priority value applies to download AND upload.
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
    """Check a profile name; returns the cleaned name."""
    if not isinstance(name, str):
        raise ConfigError("profile name must be text")
    name = name.strip()
    if not name:
        raise ConfigError("profile name must not be empty")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise ConfigError(
            f"profile name too long (max. {MAX_PROFILE_NAME_LENGTH} characters)"
        )
    for char in name:
        if not (char.isalnum() or char in "_- "):
            raise ConfigError(f"invalid character {char!r} in profile name")
    return name


def parse_days(value) -> set:
    """Parse weekdays: ``["mo","di"]`` or ``"mo-fr"`` -> ``{0,1,...}``.

    Mon=0 ... Sun=6. Unknown tokens are ignored (robust loading).
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
            else:  # wrapping range, e.g. "fr-mo"
                days.update(range(start, 7))
                days.update(range(0, end + 1))
        else:
            day = _WEEKDAY_ALIASES.get(text)
            if day is not None:
                days.add(day)
    return days


def _parse_time(value) -> str | None:
    """``"8:5"``/``"08:05"`` -> ``"08:05"``; invalid -> ``None``."""
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
    """Validate raw schedule rules; broken ones are skipped."""
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
        if start is None or end is None or start == end:
            continue
        result.append({"profile": name, "days": sorted(days),
                       "start": start, "end": end})
    return result


def normalize_window(raw) -> dict | None:
    """Normalise a rule's optional time window.

    Accepts ``{"days": [...], "start": "HH:MM", "end": "HH:MM"}``. Missing/broken
    values -> ``None`` (= rule always applies). ``start == end`` is rejected: as
    a half-open interval the window would be *never* active, which is almost
    always a typo — when loading the rule is skipped, over RPC there is an error
    instead of a silent nothing.
    """
    if not isinstance(raw, dict):
        return None
    days = parse_days(raw.get("days"))
    start = _parse_time(raw.get("start"))
    end = _parse_time(raw.get("end"))
    if not days or start is None or end is None:
        return None
    if start == end:
        raise ConfigError(
            f"time window with equal start and end ({start}) is never active"
        )
    return {"days": sorted(days), "start": start, "end": end}


def _window_matches(window: dict, when) -> bool:
    """Check whether ``when`` falls into the time window (incl. across midnight)."""
    days = set(window.get("days") or [])
    start = window.get("start")
    end = window.get("end")
    if not days or not start or not end:
        return True
    weekday = when.weekday()
    now_minutes = when.hour * 60 + when.minute
    start_minutes = _time_to_minutes(start)
    end_minutes = _time_to_minutes(end)
    if start_minutes <= end_minutes:
        return weekday in days and start_minutes <= now_minutes < end_minutes
    # Across midnight: evening part on the start day, morning part on the next.
    if weekday in days and now_minutes >= start_minutes:
        return True
    if ((weekday - 1) % 7) in days and now_minutes < end_minutes:
        return True
    return False


def rule_active(rule: dict, when=None) -> bool:
    """True when the rule applies now (no time window = always active)."""
    window = rule.get("window")
    if not window:
        return True
    if when is None:
        from datetime import datetime

        when = datetime.now()
    return _window_matches(window, when)


def active_rules(processes, when=None) -> list:
    """Only the rules whose time window is currently active."""
    return [rule for rule in (processes or []) if rule_active(rule, when)]


def _day_ranges(days) -> list:
    """Bundle consecutive weekdays into ranges: ``Mon-Fri``."""
    valid = [day for day in days if isinstance(day, int) and 0 <= day <= 6]
    ranges = []
    start = prev = None
    for day in valid:
        if start is None:
            start = prev = day
        elif day == prev + 1:
            prev = day
        else:
            ranges.append((start, prev))
            start = prev = day
    if start is not None:
        ranges.append((start, prev))
    tokens = []
    for first, last in ranges:
        if last >= first + 2:
            tokens.append(f"{WEEKDAY_TOKENS[first]}-{WEEKDAY_TOKENS[last]}")
        else:
            tokens.extend(WEEKDAY_TOKENS[day] for day in range(first, last + 1))
    return tokens


def format_window(window) -> str:
    """Short display of a time window, e.g. ``"Mon-Fri 20:00-00:00"``."""
    if not window:
        return ""
    days = sorted(set(window.get("days") or []))
    if len(days) == 7:
        day_text = "daily"
    else:
        day_text = ",".join(_day_ranges(days))
    return f"{day_text} {window.get('start')}-{window.get('end')}".strip()


def profile_names(cfg) -> list:
    """All profile names; ``"Standard"`` always at position 0."""
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
    """Copy the profile values into the top-level ``global``/``processes``.

    Unknown (and not ``"Standard"``) -> :class:`ConfigError`.
    """
    name = validate_profile_name(name)
    profile = get_profile(cfg, name)
    if profile is None:
        raise ConfigError(f"unknown profile {name!r}")
    # A stored profile has no None values (TOML drops them), so a missing limit
    # key means "unlimited", not "keep the previous profile's limit". Reset the
    # global to the defaults before overlaying the profile's values.
    cfg["global"] = copy.deepcopy(default_config()["global"])
    for key, value in profile["global"].items():
        cfg["global"][key] = copy.deepcopy(value)
    cfg["processes"] = copy.deepcopy(profile["processes"])
    cfg["active_profile"] = name
    return cfg


def capture_profile(cfg, name) -> dict:
    """Save the current top-level state as a profile."""
    name = validate_profile_name(name)
    profile = {
        "global": copy.deepcopy(cfg.get("global") or {}),
        "processes": copy.deepcopy(cfg.get("processes") or []),
    }
    cfg.setdefault("profiles", {})[name] = profile
    cfg["active_profile"] = name
    return profile


def delete_profile(cfg, name) -> bool:
    """Delete a profile; ``True`` when it existed."""
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
    """Which profile is active now according to ``schedule``? (first rule wins)

    ``when`` is a ``datetime`` (default: now). Across midnight (``end < start``)
    the rule applies into the next day: the morning part counts for the weekday
    of the start day.
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
            # Evening part on the start day ...
            if weekday in days and now_minutes >= start_minutes:
                return name
            # ... morning part on the next day.
            if ((weekday - 1) % 7) in days and now_minutes < end_minutes:
                return name
    return None


def _backup_broken_config(path: str) -> str | None:
    """Copy an unreadable config aside.

    Without this the next persist would write the defaults over the user's
    rules, profiles and budgets — a typo would silently destroy the file.
    """
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = f"{path}.invalid-{stamp}"
        shutil.copy2(path, target)
        return target
    except OSError:
        return None


def load_config(path) -> dict:
    """Load the config (normalised). Missing file -> defaults.

    A broken file must not make the daemon unstartable: syntactic garbage and
    (as a safety net) an unexpected normalisation error fall back to the
    defaults, with the original set aside as ``*.invalid-*``. A single invalid
    ``[global]`` value, in contrast, is reset only for that field, so rules,
    profiles and budgets survive.
    """
    global _LAST_CONFIG_WARNING
    _LAST_CONFIG_WARNING = None
    if not os.path.exists(path):
        return default_config()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as error:
        backup = _backup_broken_config(path)
        _LAST_CONFIG_WARNING = (
            f"Config {path} could not be read "
            f"({type(error).__name__}: {error}) — using the defaults."
            + (f" The original was saved as {backup}." if backup else "")
        )
        print(f"Warning: {_LAST_CONFIG_WARNING}", flush=True)
        return default_config()
    notes: list = []
    try:
        cfg = normalize(data, lenient=True, notes=notes)
    except (ConfigError, ValueError, TypeError, KeyError, AttributeError,
            IndexError, OverflowError) as error:
        # Should hardly happen with lenient=True; stays as a safety net.
        backup = _backup_broken_config(path)
        _LAST_CONFIG_WARNING = (
            f"Config {path} could not be loaded "
            f"({type(error).__name__}: {error}) — using the defaults."
            + (f" The original was saved as {backup}." if backup else "")
        )
        print(f"Warning: {_LAST_CONFIG_WARNING}", flush=True)
        return default_config()
    if notes:
        _LAST_CONFIG_WARNING = (
            f"Config {path}: invalid values reset to defaults — "
            + "; ".join(notes)
        )
        print(f"Warning: {_LAST_CONFIG_WARNING}", flush=True)
    return cfg


def last_config_warning() -> str | None:
    """Last warning from :func:`load_config` (None = all good)."""
    return _LAST_CONFIG_WARNING


# --------------------------------------------------------------------------
# Minimal TOML writer (deliberately hand-rolled: no external dependencies, the
# schema is known and flat; tests cover escaping/special cases).
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
    """Render a TOML scalar; None -> None (the key is omitted)."""
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
    raise TypeError(f"cannot render as a TOML scalar: {value!r}")


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
    """Render a table key; quote on special characters/two spaces."""
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


def _dump_rule_keys(rule: dict) -> list:
    """The TOML lines of a rule (incl. optional time window)."""
    out = []
    for key in _PROCESS_KEYS:
        value = _scalar(rule.get(key))
        if value is None:
            continue
        out.append(f"{key} = {value}")
    window = rule.get("window")
    if window:
        out.append(f"window_days = {_string_array(_days_to_tokens(window.get('days')))}")
        out.append(f"window_start = {_quote(str(window.get('start', '')))}")
        out.append(f"window_end = {_quote(str(window.get('end', '')))}")
    return out


def dump_config(config: dict) -> str:
    lines = ["version = 1"]
    if config.get("interface"):
        lines.append(f"interface = {_quote(config['interface'])}")
    lines.append(f"unit = {_quote(config.get('unit', 'kbps'))}")
    lines.append(
        f"active_profile = {_quote(config.get('active_profile', STANDARD_PROFILE))}"
    )
    start_profile = config.get("start_profile")
    if start_profile:
        lines.append(f"start_profile = {_quote(str(start_profile))}")
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
        lines.extend(_dump_rule_keys(rule))
        lines.append("")

    # --- Profiles (nested: [[profiles.NAME.processes]]) --------------
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
            lines.extend(_dump_rule_keys(rule))
            lines.append("")

    # --- Schedules (flat array-of-tables) -------------------------------
    for rule in config.get("schedule") or []:
        lines.append("[[schedule]]")
        lines.append(f"profile = {_quote(str(rule.get('profile', '')))}")
        lines.append(f"days = {_string_array(_days_to_tokens(rule.get('days')))}")
        lines.append(f"start = {_quote(str(rule.get('start', '')))}")
        lines.append(f"end = {_quote(str(rule.get('end', '')))}")
        lines.append("")

    # --- Consumption budgets ------------------------------------------------
    budgets = config.get("budgets") or {}
    budgets_active = (budgets.get("day") or budgets.get("week")
                      or budgets.get("rules") or budgets.get("enforce")
                      or budgets.get("floor") is not None
                      or budgets.get("enabled") is False)
    if budgets_active:
        lines.append("[budgets]")
        lines.append(
            f"enabled = {'true' if budgets.get('enabled', True) else 'false'}"
        )
        if budgets.get("enforce"):
            lines.append("enforce = true")
        if budgets.get("floor") is not None:
            lines.append(f"floor = {int(budgets['floor'])}")
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
    """Write the config atomically (temp + fsync + os.replace, see __init__)."""
    write_text_atomic(path, dump_config(config), mode=mode)


def detect_default_interface() -> str | None:
    """Determine the default-route interface (purely from /proc, no root)."""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as handle:
            next(handle, None)  # header line
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000" and fields[2] != "00000000":
                    return fields[0]
    except OSError:
        pass
    # Fallback: first non-loopback interface with traffic
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
