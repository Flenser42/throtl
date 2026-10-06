"""Consumption budgets: thresholds per app and globally.

Builds directly on the statistics history (``stats.StatsStore``). A budget is
rolling:

* ``day``  -> the last 24 hours (24 hourly buckets)
* ``week`` -> the last 7 days (7 daily buckets)

Download **and** upload are evaluated together. This module is pure logic (no
I/O) so it is easy to test.
"""

from .stats import StatsStore

# window -> (stats resolution, bucket count)
WINDOWS = {
    "day": ("hour", 24),
    "week": ("day", 7),
}

# SI steps (1000s), matching units.parse_size
_VOLUME_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_volume(value) -> str:
    """Format a volume for input fields: ``"20 GB"``, ``"1.5 MB"``.

    ``None`` means "no budget" and yields an empty string. The result can be
    parsed back exactly with ``units.parse_size``.
    """
    if value is None:
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in _VOLUME_UNITS:
        if amount < 1000 or unit == _VOLUME_UNITS[-1]:
            text = f"{amount:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        amount /= 1000.0
    return f"{amount:.1f} {_VOLUME_UNITS[-1]}"


def budget_rows(payload: dict) -> dict:
    """Bring the ``get_budgets`` response into display form.

    Pure and free of widgets, so the mapping of the windows (day/week) and the
    global/app split stays testable independently of the UI.
    """
    payload = payload or {}
    rows = {
        "enabled": bool(payload.get("enabled", True)),
        "global": {"day": None, "week": None},
        "apps": [],
    }
    apps: dict[str, dict] = {}
    for entry in payload.get("entries") or []:
        window = entry.get("window")
        if window not in rows["global"]:
            continue
        cell = {
            "used": entry.get("used") or 0.0,
            "limit": entry.get("limit") or 0,
            "ratio": entry.get("ratio") or 0.0,
            "exceeded": bool(entry.get("exceeded")),
        }
        if entry.get("scope") == "global":
            rows["global"][window] = cell
            continue
        app = str(entry.get("app") or "").strip()
        if not app:
            continue
        bucket = apps.setdefault(app, {"app": app, "day": None, "week": None})
        bucket[window] = cell
    rows["apps"] = [apps[name] for name in sorted(apps)]
    return rows


# Windows below this usage report nothing.
WARN_RATIO = 0.8


def warning_key(entry: dict) -> str:
    """Unique key of a budget for the early warning."""
    return f"{entry.get('scope')}:{entry.get('app')}:{entry.get('window')}"


def pending_warnings(entries, warned, threshold: float = WARN_RATIO) -> list:
    """Budgets that are getting tight and have not been reported yet.

    An already-exceeded budget is reported by the existing path; here we only
    care about the moment before, when there is still time to react.
    """
    out = []
    for entry in entries or []:
        if entry.get("exceeded"):
            continue
        if (entry.get("ratio") or 0.0) < threshold:
            continue
        if warning_key(entry) in warned:
            continue
        out.append(entry)
    return out


def _entry(scope: str, app: str | None, window: str, used: float, limit: float) -> dict:
    ratio = (used / limit) if limit else 0.0
    return {
        "scope": scope,
        "app": app,
        "window": window,
        "used": used,
        "limit": limit,
        "ratio": ratio,
        "exceeded": used >= limit,
    }


def budget_status(cfg: dict, stats: StatsStore, now: float | None = None) -> list:
    """List all configured budgets with their current usage."""
    budgets = cfg.get("budgets") or {}
    if budgets.get("enabled") is False:
        return []

    def used_total(totals: dict) -> float:
        return sum(v.get("download", 0.0) + v.get("upload", 0.0)
                   for v in totals.values())

    entries = []
    for window, (resolution, buckets) in WINDOWS.items():
        limit = budgets.get(window)
        if not limit:
            continue
        totals = stats.recent_totals(resolution, buckets, now=now)
        entries.append(_entry("global", None, window, used_total(totals), limit))

    for rule in budgets.get("rules") or []:
        app = rule.get("app")
        if not app:
            continue
        for window, (resolution, buckets) in WINDOWS.items():
            limit = rule.get(window)
            if not limit:
                continue
            totals = stats.recent_totals(resolution, buckets, now=now)
            values = totals.get(app) or {"download": 0.0, "upload": 0.0}
            used = values.get("download", 0.0) + values.get("upload", 0.0)
            entries.append(_entry("app", app, window, used, limit))

    entries.sort(key=lambda item: item["ratio"], reverse=True)
    return entries
