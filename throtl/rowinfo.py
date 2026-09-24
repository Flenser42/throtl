"""Warum laeuft diese App gerade so? — die Erklaerzeile einer Tabellenzeile.

Beantwortet die Frage, die sonst nur das README beantwortet: gilt ein eigenes
Limit, ein globales, und ist ein Zeitfenster gerade aktiv? Bewusst ohne Widgets
und ohne GTK, damit die Erklaerung ohne Display (und im CI) pruefbar bleibt.
"""

from .config import PRIORITY_LABELS, format_window, rule_active
from .units import format_rate

# Reihenfolge der Nennung: erst das eigene Limit, dann das Fenster.
MAX_LENGTH = 120


def _limits(rule: dict, unit: str) -> list[str]:
    # Gleiche Genauigkeit wie das Eingabefeld, ohne abschliessende Nullen:
    # die Erklaerung soll den Wert nennen, den der Nutzer dort sieht.
    parts = []
    down = rule.get("download_limit")
    up = rule.get("upload_limit")
    if down:
        parts.append(f"{format_rate(down, unit, 3, trim=True)} download")
    if up:
        parts.append(f"{format_rate(up, unit, 3, trim=True)} upload")
    return parts


def _window(rule: dict, when) -> str | None:
    window = rule.get("window")
    if not window:
        return None
    text = format_window(window)
    if not text:
        return None
    if rule_active(rule, when):
        return f"{text} (active now)"
    return f"{text} (not active now)"


def _priority(rule: dict) -> str | None:
    """Anzeigename, wenn eine Prioritaet gesetzt ist (Normal ist die Ruhe)."""
    name = rule.get("priority") or "normal"
    if name == "normal":
        return None
    return PRIORITY_LABELS.get(name, str(name))


def explain(rule, global_limits, unit: str = "mBs", when=None) -> str | None:
    """Ein Satz, der den Zustand dieser Zeile erklaert.

    ``None`` bedeutet: es gibt nichts zu erklaeren — keine Prioritaet, kein
    Limit, kein Fenster. Dann bleibt die Zeile still, statt in jeder Zeile
    "kein Limit" zu wiederholen.
    """
    rule = rule or {}
    global_limits = global_limits or {}
    parts = _limits(rule, unit)
    priority = _priority(rule)
    window = _window(rule, when)
    if parts:
        text = "Your rule: " + ", ".join(parts)
        if priority:
            text += f" · Priority {priority}"
        if window:
            text += " · " + window
        return text[:MAX_LENGTH]
    if priority and window:
        return f"Priority {priority} · {window}"[:MAX_LENGTH]
    if priority:
        return f"Priority {priority}"[:MAX_LENGTH]
    if window:
        return f"Time window: {window}"[:MAX_LENGTH]
    global_parts = _limits(global_limits, unit)
    if global_parts:
        return ("Global limit: " + ", ".join(global_parts))[:MAX_LENGTH]
    return None
