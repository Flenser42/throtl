"""Bandbreiten-Einheiten.

Interne Speicherung und TrafficToll-Konfiguration arbeiten in **kbit/s** (kbps).
Die GUI kann die Anzeige zwischen kbps (Mbit/s) und kBs (KB/s) umschalten.
"""

# 1 kbit/s = 0.125 KB/s
KB_PER_KBIT = 0.125

# Anzeige-Einheiten, die die GUI anbietet (Wert in "unit"-Config-Feld)
DISPLAY_UNITS = ("kbps", "kBs")

_PARSERS = {
    "kbps": 1.0,
    "kbit": 1.0,
    "kbit/s": 1.0,
    "mbps": 1000.0,
    "mbit": 1000.0,
    "mbit/s": 1000.0,
    "gbps": 1_000_000.0,
    "gbit": 1_000_000.0,
    "gbit/s": 1_000_000.0,
}


def kbit_to_kBs(kbit_per_s: float) -> float:
    """kbit/s -> KB/s."""
    return kbit_per_s * KB_PER_KBIT


def kBs_to_kbit(kb_per_s: float) -> float:
    """KB/s -> kbit/s."""
    return kb_per_s / KB_PER_KBIT


def parse_rate(value) -> int:
    """Raten-String wie '1.5mbps'/'512kbps'/'2000000' in ganzzahlige kbit/s parsen.

    Akzeptiert auch int/float (wird als kbit/s interpretiert) und None (-> None).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"negative Rate ungueltig: {value!r}")
        return int(round(value))
    if not isinstance(value, str):
        raise ValueError(f"ungueltige Rate: {value!r}")
    text = value.strip().lower().replace(" ", "")
    if not text:
        return None
    for suffix, factor in sorted(_PARSERS.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                amount = float(number)
            except ValueError:
                break
            if amount < 0:
                raise ValueError(f"negative Rate ungueltig: {value!r}")
            return int(round(amount * factor))
    # nackte Zahl -> kbit/s
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"ungueltige Rate: {value!r}") from None
    if amount < 0:
        raise ValueError(f"negative Rate ungueltig: {value!r}")
    return int(round(amount))


def format_rate(kbit_per_s, unit: str = "auto", precision: int = 1) -> str:
    """Rate (kbit/s) fuer die Anzeige formatieren.

    unit: "kbps" | "kBs" | "mbps" | "mBs" | "auto"
    """
    if kbit_per_s is None:
        return "∞"
    value = float(kbit_per_s)
    if unit == "kBs":
        return f"{value * KB_PER_KBIT:.{precision}f} KB/s"
    if unit == "mBs":
        return f"{value * KB_PER_KBIT / 1000:.{precision}f} MB/s"
    if unit == "kbps":
        return f"{value:.{precision}f} kbit/s"
    if unit == "mbps":
        return f"{value / 1000:.{precision}f} Mbit/s"
    # auto: kompaktere Einheit waehlen
    if value >= 1000:
        return f"{value / 1000:.{precision}f} Mbit/s"
    return f"{value:.{precision}f} kbit/s"
