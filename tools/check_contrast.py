#!/usr/bin/env python3
"""WCAG-Kontrast der Design-Tokens pruefen (Teil von `make check` und CI).

Liest die OKLCH-Tokens aus ``throtl-app/src/styles/globals.css`` (Light als
Basis, Dark als Ueberschreibung), rechnet sie nach sRGB um und prueft jedes
Text/Hintergrund-Paar gegen 4.5:1 (AA fuer kleinen Text). Exitcode 1, sobald
ein Paar darunter liegt — so bleibt die Zusage im CHANGELOG reproduzierbar.

Warum eigene Mathematik: die Tokens sind OKLCH, und ein zusaetzliches Paket
fuer eine Formel waere unnoetig. Standardbibliothek, kein Netz, kein Display.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "throtl-app" / "src" / "styles" / "globals.css"

# Tokens, die als Text auf den Flaechen vorkommen koennen.
FOREGROUNDS = ("text", "text-2", "text-3", "down", "up", "signal", "warn", "danger")
BACKGROUNDS = ("bg", "surface", "surface-2")
MIN_RATIO = 4.5


def oklch_to_srgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    rad = math.radians(hue)
    a, b = chroma * math.cos(rad), chroma * math.sin(rad)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l_cube, m_cube, s_cube = l_**3, m_**3, s_**3
    r = 4.0767416621 * l_cube - 3.3077115913 * m_cube + 0.2309699292 * s_cube
    g = -1.2684380046 * l_cube + 2.6097574011 * m_cube - 0.3413193965 * s_cube
    bl = -0.0041960863 * l_cube - 0.7034186147 * m_cube + 1.7076147010 * s_cube

    def encode(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055

    return encode(r), encode(g), encode(bl)


def luminance(rgb: tuple[float, float, float]) -> float:
    def linear(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _block(text: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", text, re.S)
    return match.group(1) if match else ""


def _tokens(block: str) -> dict[str, tuple[float, float, float]]:
    tokens: dict[str, tuple[float, float, float]] = {}
    for match in re.finditer(r"--([a-z0-9-]+):\s*oklch\(([^)]+)\)", block):
        first = match.group(2).split("/")[0]
        parts = first.split()
        if len(parts) == 3:
            tokens[match.group(1)] = (float(parts[0]), float(parts[1]), float(parts[2]))
    return tokens


def scan(css_path: Path = CSS) -> list[str]:
    text = css_path.read_text(encoding="utf-8")
    light = _tokens(_block(text, ":root"))
    dark = dict(light)
    dark.update(_tokens(_block(text, ':root[data-theme="dark"]')))
    failures: list[str] = []
    checked = 0
    for theme, tokens in (("light", light), ("dark", dark)):
        for background in BACKGROUNDS:
            if background not in tokens:
                continue
            for foreground in FOREGROUNDS:
                if foreground not in tokens:
                    continue
                checked += 1
                ratio = contrast(
                    oklch_to_srgb(*tokens[foreground]), oklch_to_srgb(*tokens[background])
                )
                if ratio < MIN_RATIO:
                    failures.append(
                        f"{theme}: --{foreground} auf --{background} = {ratio:.2f} "
                        f"(< {MIN_RATIO})"
                    )
        ink = tokens.get("on-signal")
        if ink is not None and "signal" in tokens:
            checked += 1
            ratio = contrast(oklch_to_srgb(*ink), oklch_to_srgb(*tokens["signal"]))
            if ratio < MIN_RATIO:
                failures.append(f"{theme}: --on-signal auf --signal = {ratio:.2f} (< {MIN_RATIO})")
    print(f"contrast: {checked} Paare geprueft, {len(failures)} unter {MIN_RATIO}:1")
    return failures


def main() -> int:
    failures = scan()
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
