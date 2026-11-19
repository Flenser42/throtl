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

# Windows below this usage report nothing.
WARN_RATIO = 0.8


def warning_key(entry: dict) -> str:
    """Unique key of a budget for the early warning."""
    return f"{entry.get('scope')}:{entry.get('app')}:{entry.get('window')}"


def budget_level(entry: dict) -> int:
    """Alert level of a budget: 0 = fine, 1 = past 80 %, 2 = over the limit."""
    if entry.get("exceeded") or (entry.get("ratio") or 0.0) >= 1.0:
        return 2
    if (entry.get("ratio") or 0.0) >= WARN_RATIO:
        return 1
    return 0


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
