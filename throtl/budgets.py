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

# SI-Schritte (1000er), passend zu units.parse_size
_VOLUME_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_volume(value) -> str:
    """Volumen fuer Eingabefelder formatieren: ``"20 GB"``, ``"1.5 MB"``.

    ``None`` bedeutet "kein Budget" und ergibt einen leeren Text. Das Ergebnis
    laesst sich mit ``units.parse_size`` wieder exakt einlesen.
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
    """Antwort von ``get_budgets`` in Anzeigeform bringen.

    Rein und ohne Widgets, damit die Zuordnung der Fenster (Tag/Woche) und die
    Trennung global/App unabhaengig von der Oberflaeche testbar bleibt.
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


# Fenster unterhalb dieser Auslastung melden nichts.
WARN_RATIO = 0.8


def warning_key(entry: dict) -> str:
    """Eindeutiger Schluessel eines Budgets fuer die Vorwarnung."""
    return f"{entry.get('scope')}:{entry.get('app')}:{entry.get('window')}"


def pending_warnings(entries, warned, threshold: float = WARN_RATIO) -> list:
    """Budgets, die knapp werden und noch nicht gemeldet wurden.

    Ein bereits ueberschrittenes Budget meldet der bestehende Pfad; hier geht es
    nur um den Moment davor, in dem man noch reagieren kann.
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
