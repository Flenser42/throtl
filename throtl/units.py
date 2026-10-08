"""Bandbreiten-Einheiten.

Interne Speicherung und TrafficToll-Konfiguration arbeiten in **kbit/s** (kbps).
Die GUI kann die Anzeige zwischen kbps (kbit/s), mbps (Mbit/s), kBs (KB/s) und
mBs (MB/s) umschalten.
"""

import math

# 1 kbit/s = 0.125 KB/s
KB_PER_KBIT = 0.125

# Display units offered by the GUI (value in the "unit" config field)
DISPLAY_UNITS = ("kbps", "mbps", "kBs", "mBs")

_PARSERS = {
    # bit/s-Formen (Faktor in kbit/s)
    "kbps": 1.0,
    "kbit/s": 1.0,
    "kbit": 1.0,
    "mbps": 1000.0,
    "mbit/s": 1000.0,
    "mbit": 1000.0,
    "gbps": 1_000_000.0,
    "gbit/s": 1_000_000.0,
    "gbit": 1_000_000.0,
    # byte/s-Formen (kB/s, MB/s, GB/s) -> in kbit/s umrechnen
    "mb/s": 8000.0,
    "mb": 8000.0,
    "kb/s": 8.0,
    "kb": 8.0,
    "gb/s": 8_000_000.0,
    "gb": 8_000_000.0,
    # The GUI's own unit tokens (DISPLAY_UNITS) also as a suffix. parse_rate()
    # lowercases "mBs" -> "mbs"; without these entries values in the displayed
    # unit would be rejected.
    "mbs": 8000.0,
    "kbs": 8.0,
    # mbps/kbps short without slash (for parse_rate_lenient)
}


def kbit_to_kBs(kbit_per_s: float) -> float:
    """kbit/s -> KB/s."""
    return kbit_per_s * KB_PER_KBIT


def kBs_to_kbit(kb_per_s: float) -> float:
    """KB/s -> kbit/s."""
    return kb_per_s / KB_PER_KBIT


# Nothing real exceeds this; it keeps absurd input out of the config.
MAX_RATE_KBIT = 100_000_000       # 100 Gbit/s
MAX_SIZE_BYTES = 1_000_000_000_000_000  # 1 PB


def _bounded_rate(amount: float) -> int:
    """Validate a kbit/s amount: finite, non-negative, within a sane ceiling.

    ``inf``/``nan`` and absurd sizes used to slip through (TOML's ``1e309`` is
    ``inf``, and ``int(inf)`` raises OverflowError past every handler).
    """
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"invalid rate: {amount!r}")
    if amount > MAX_RATE_KBIT:
        raise ValueError(f"rate too large: {amount!r} kbit/s (max {MAX_RATE_KBIT})")
    if 0 < amount < 1:
        return 1
    return round(amount)


def _bounded_size(amount: float) -> int:
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"invalid volume: {amount!r}")
    if amount > MAX_SIZE_BYTES:
        raise ValueError(f"volume too large: {amount!r} bytes (max {MAX_SIZE_BYTES})")
    return int(round(amount))


def parse_rate(value) -> int:
    """Parse a rate string like '1.5mbps'/'512kbps'/'2000000' into integer kbit/s.

    Also accepts int/float (interpreted as kbit/s) and None (-> None). ``bool``
    is rejected (``True`` would otherwise be 1 kbit/s).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid rate: {value!r}")
    if isinstance(value, (int, float)):
        return _bounded_rate(value)
    if not isinstance(value, str):
        raise ValueError(f"invalid rate: {value!r}")
    text = value.strip().lower().replace(" ", "").replace(",", ".")
    if not text:
        return None
    for suffix, factor in sorted(_PARSERS.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                amount = float(number)
            except ValueError:
                break
            return _bounded_rate(amount * factor)
    # bare number -> kbit/s
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"invalid rate: {value!r}") from None
    return _bounded_rate(amount)


def format_rate(kbit_per_s, unit: str = "auto", precision: int = 1,
                trim: bool = False) -> str:
    """Format a rate (kbit/s) for display.

    unit: "kbps" | "mbps" | "kBs" | "mBs" | "auto"
    trim: drop trailing zeros (for configured limits that should look exactly
          like the input field: "0.5" instead of "0.50").
    """
    if kbit_per_s is None:
        return "∞"
    value = float(kbit_per_s)
    if unit == "mBs":
        number, suffix = value * KB_PER_KBIT / 1000, "MB/s"
    elif unit == "kBs":
        number, suffix = value * KB_PER_KBIT, "KB/s"
    elif unit == "mbps":
        number, suffix = value / 1000, "Mbit/s"
    elif unit == "kbps":
        number, suffix = value, "kbit/s"
    elif value >= 1000:
        number, suffix = value / 1000, "Mbit/s"
    else:
        number, suffix = value, "kbit/s"
    text = f"{number:.{precision}f}"
    if trim:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {suffix}"


def parse_rate_lenient(value) -> int:
    """Parse a value like '1,5' / '1.5' / '2 MB/s' / '512 kbps' / '100' into kbit/s.

    Accepts comma as decimal separator and unit suffixes (MB/s, KB/s, Mbit/s,
    kbit/s, mbps, kbps). Intended for GUI input fields; None/empty/unlimited -> None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return parse_rate(value)
    text = value.strip().replace(",", ".")
    if not text:
        return None
    low = text.lower().replace(" ", "")
    if low in ("unlimited", "unbegrenzt", "none", "unendlich", "∞", "-"):
        return None
    return parse_rate(text)


# Factor: 1 number in this display unit -> kbit/s
_UNIT_TO_KBIT = {
    "kbps": 1.0,
    "mbps": 1000.0,
    "kBs": 8.0,
    "mBs": 8000.0,
}

_UNIT_SUFFIXES = (
    "kbps", "mbps", "gbps", "kbit/s", "mbit/s", "gbit/s", "kbit", "mbit", "gbit",
    "kb/s", "mb/s", "gb/s", "kb", "mb", "gb", "kbs", "mbs",
)


def has_explicit_unit(text: str) -> bool:
    """Does the text contain a unit suffix? (e.g. '2 MB/s')"""
    low = (text or "").strip().lower().replace(" ", "")
    return any(low.endswith(suffix) for suffix in _UNIT_SUFFIXES)


def parse_rate_in_unit(value, unit: str = "kbps"):
    """Parse GUI input in the DISPLAYED unit.

    Important: a bare number like '2' means in the unit ``unit`` (e.g. 'mBs' ->
    2 MB/s), not kbit/s. An explicit suffix ('2 kbps', '1.5 MB/s') always wins.
    Empty/unlimited -> None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value) * _UNIT_TO_KBIT.get(unit, 1.0))
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    low = text.lower().replace(" ", "")
    if low in ("unlimited", "unbegrenzt", "none", "unendlich", "∞", "-"):
        return None
    if has_explicit_unit(text):
        return parse_rate(text)
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"invalid rate: {value!r}") from None
    if number < 0:
        raise ValueError(f"negative rate invalid: {value!r}")
    return round(number * _UNIT_TO_KBIT.get(unit, 1.0))


def format_rate_for_entry(kbit_per_s, unit: str = "kbps", precision: int = 3) -> str:
    """Rate (kbit/s) as an editable number in the display unit (without suffix).

    This keeps the field content and the placeholder/unit in sync (before, the
    fields always showed kbit/s while the unit displayed was MB/s).
    """
    if kbit_per_s is None:
        return ""
    factor = _UNIT_TO_KBIT.get(unit, 1.0)
    value = float(kbit_per_s) / factor
    text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return text if text else "0"


# Byte/volume suffixes (SI, 1000-steps — consistent with _fmt_bytes).
_SIZE_UNITS = {
    "b": 1,
    "kb": 1000, "kib": 1024,
    "mb": 1000 ** 2, "mib": 1024 ** 2,
    "gb": 1000 ** 3, "gib": 1024 ** 3,
    "tb": 1000 ** 4, "tib": 1024 ** 4,
}


def parse_size(value) -> int | None:
    """Parse a volume like '20GB', '5 GiB' or '1500000' into bytes.

    None/empty/'unlimited' -> None (no budget). Negative values are invalid.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid volume: {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"negative volume invalid: {value!r}")
        return _bounded_size(value)
    text = str(value).strip().lower().replace(" ", "").replace(",", ".")
    if not text:
        return None
    if text in ("unlimited", "unbegrenzt", "none", "unendlich", "∞", "-"):
        return None
    for suffix in sorted(_SIZE_UNITS, key=len, reverse=True):
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                amount = float(number)
            except ValueError:
                break
            return _bounded_size(amount * _SIZE_UNITS[suffix])
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"invalid volume: {value!r}") from None
    return _bounded_size(amount)
