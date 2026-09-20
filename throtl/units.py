"""Bandbreiten-Einheiten.

Interne Speicherung und TrafficToll-Konfiguration arbeiten in **kbit/s** (kbps).
Die GUI kann die Anzeige zwischen kbps (kbit/s), mbps (Mbit/s), kBs (KB/s) und
mBs (MB/s) umschalten.
"""

# 1 kbit/s = 0.125 KB/s
KB_PER_KBIT = 0.125

# Anzeige-Einheiten, die die GUI anbietet (Wert im "unit"-Config-Feld)
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
    # mbps/kbps kurz ohne slash (fuer parse_rate_lenient)
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
        return round(value)
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
            return round(amount * factor)
    # nackte Zahl -> kbit/s
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"ungueltige Rate: {value!r}") from None
    if amount < 0:
        raise ValueError(f"negative Rate ungueltig: {value!r}")
    return round(amount)


def format_rate(kbit_per_s, unit: str = "auto", precision: int = 1) -> str:
    """Rate (kbit/s) fuer die Anzeige formatieren.

    unit: "kbps" | "mbps" | "kBs" | "mBs" | "auto"
    """
    if kbit_per_s is None:
        return "∞"
    value = float(kbit_per_s)
    if unit == "mBs":
        mb = value * KB_PER_KBIT / 1000
        return f"{mb:.{precision}f} MB/s"
    if unit == "kBs":
        return f"{value * KB_PER_KBIT:.{precision}f} KB/s"
    if unit == "mbps":
        return f"{value / 1000:.{precision}f} Mbit/s"
    if unit == "kbps":
        return f"{value:.{precision}f} kbit/s"
    # auto: kompaktere Einheit waehlen
    if value >= 1000:
        return f"{value / 1000:.{precision}f} Mbit/s"
    return f"{value:.{precision}f} kbit/s"


def parse_rate_lenient(value) -> int:
    """Wert wie '1,5' / '1.5' / '2 MB/s' / '512 kbps' / '100' in kbit/s parsen.

    Akzeptiert Komma als Dezimaltrenner und Einheissuffixe (MB/s, KB/s, Mbit/s,
    kbit/s, mbps, kbps). Fuer GUI-Eingabefelder gedacht; None/leer/unlimited -> None.
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


# Faktor: 1 Zahl in dieser Anzeige-Einheit -> kbit/s
_UNIT_TO_KBIT = {
    "kbps": 1.0,
    "mbps": 1000.0,
    "kBs": 8.0,
    "mBs": 8000.0,
}

_UNIT_SUFFIXES = (
    "kbps", "mbps", "gbps", "kbit/s", "mbit/s", "gbit/s", "kbit", "mbit", "gbit",
    "kb/s", "mb/s", "gb/s", "kb", "mb", "gb",
)


def has_explicit_unit(text: str) -> bool:
    """Enthaelt der Text ein Einheiten-Suffix? (z.B. '2 MB/s')"""
    low = (text or "").strip().lower().replace(" ", "")
    return any(low.endswith(suffix) for suffix in _UNIT_SUFFIXES)


def parse_rate_in_unit(value, unit: str = "kbps"):
    """GUI-Eingabe in der ANGEZEIGTEN Einheit parsen.

    Wichtig: Eine nackte Zahl wie '2' bedeutet in der Einheit ``unit``
    (z.B. 'mBs' -> 2 MB/s), nicht kbit/s. Ein explizites Suffix ('2 kbps',
    '1.5 MB/s') hat immer Vorrang. Leer/unlimited -> None.
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
        raise ValueError(f"ungueltige Rate: {value!r}") from None
    if number < 0:
        raise ValueError(f"negative Rate ungueltig: {value!r}")
    return round(number * _UNIT_TO_KBIT.get(unit, 1.0))


def format_rate_for_entry(kbit_per_s, unit: str = "kbps", precision: int = 3) -> str:
    """Rate (kbit/s) als editierbare Zahl in der Anzeige-Einheit (ohne Suffix).

    Damit passen Feldinhalt und Platzhalter/Einheit zusammen (vorher stand in
    den Feldern immer kbit/s, waehrend die Einheit MB/s angezeigt wurde).
    """
    if kbit_per_s is None:
        return ""
    factor = _UNIT_TO_KBIT.get(unit, 1.0)
    value = float(kbit_per_s) / factor
    text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return text if text else "0"


# Byte-/Volumen-Suffixe (SI, 1000er-Schritte — konsistent zu _fmt_bytes).
_SIZE_UNITS = {
    "b": 1,
    "kb": 1000, "kib": 1024,
    "mb": 1000 ** 2, "mib": 1024 ** 2,
    "gb": 1000 ** 3, "gib": 1024 ** 3,
    "tb": 1000 ** 4, "tib": 1024 ** 4,
}


def parse_size(value) -> int | None:
    """Volumen wie '20GB', '5 GiB' oder '1500000' in Bytes parsen.

    None/leer/'unlimited' -> None (kein Budget). Negative Werte sind ungueltig.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"ungueltiges Volumen: {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"negatives Volumen ungueltig: {value!r}")
        return int(value)
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
            if amount < 0:
                raise ValueError(f"negatives Volumen ungueltig: {value!r}")
            return int(round(amount * _SIZE_UNITS[suffix]))
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"ungueltiges Volumen: {value!r}") from None
    if amount < 0:
        raise ValueError(f"negatives Volumen ungueltig: {value!r}")
    return int(round(amount))
