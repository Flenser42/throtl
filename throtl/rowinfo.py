"""Why is this app behaving like this right now? — the explainer line of a row.

Answers the question that otherwise only the README answers: does an own limit
apply, a global one, and is a time window active right now? Deliberately free of
widgets and GTK, so the explanation stays testable without a display (and in CI).
"""

from .config import PRIORITY_LABELS, format_window, rule_active
from .units import format_rate

# Order of mention: first the own limit, then the window.
MAX_LENGTH = 120


def _limits(rule: dict, unit: str) -> list[str]:
    # Same precision as the input field, without trailing zeros: the explanation
    # should name the value the user sees there.
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
    """Display name, when a priority is set (Normal is the quiet default)."""
    name = rule.get("priority") or "normal"
    if name == "normal":
        return None
    return PRIORITY_LABELS.get(name, str(name))


def explain(rule, global_limits, unit: str = "mBs", when=None) -> str | None:
    """One sentence explaining the state of this row.

    ``None`` means: there is nothing to explain — no priority, no limit, no
    window. Then the row stays silent instead of repeating "no limit" in every
    row.
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
