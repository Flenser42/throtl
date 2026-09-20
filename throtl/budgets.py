"""Verbrauchs-Budgets: Schwellwerte pro App und global.

Baut direkt auf der Statistik-History auf (``stats.StatsStore``). Ein Budget
ist rollierend gemeint:

* ``day``  -> die letzten 24 Stunden (24 Stundenbuckets)
* ``week`` -> die letzten 7 Tage (7 Tagesbuckets)

Bewertet werden Download **und** Upload zusammen. Das Modul ist reine Logik
(kein I/O), damit es sich leicht testen laesst.
"""

from .stats import StatsStore

# window -> (stats-Aufloesung, Anzahl Buckets)
WINDOWS = {
    "day": ("hour", 24),
    "week": ("day", 7),
}


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
    """Alle konfigurierten Budgets mit aktuellem Verbrauch auflisten."""
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
