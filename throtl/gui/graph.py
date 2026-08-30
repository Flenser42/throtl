"""Live-Bandbreiten-Graph (Cairo-Zeichnung ueber GtkDrawingArea).

Zeichnet Download- und Upload-Kurve der Gesamtbandbreite ueber ein Zeitfenster.
Dunkles, minimalistisches Design passend zu Omarchy.
"""

import math
from collections import deque

import gi  # noqa: F401

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Gdk  # noqa: F401


class BandwidthGraph(Gtk.DrawingArea):
    """Zeitlicher Verlauf der Gesamtbandbreite (Down + Up)."""

    def __init__(self, window_seconds: int = 60, max_rate_kbps: int | None = None):
        super().__init__()
        self.window_seconds = window_seconds
        self.max_rate_kbps = max_rate_kbps  # None -> Auto-Skalierung
        self._samples = deque(maxlen=window_seconds)  # (t, down_kbps, up_kbps)
        self._auto_baseline = 1000  # kbit/s Startwert fuer Auto-Skalierung
        self.set_height_request(120)
        self.set_draw_func(self._draw, None)
        self.add_css_class("throtl-graph")

    def push(self, down_kbps: float, up_kbps: float, now: float | None = None) -> None:
        import time

        if now is None:
            now = time.monotonic()
        self._samples.append((now, max(0.0, down_kbps), max(0.0, up_kbps)))
        self.queue_draw()

    def clear(self) -> None:
        self._samples.clear()
        self.queue_draw()

    def _draw(self, area, cr, width, height, _data) -> None:
        # Hintergrund
        cr.set_source_rgba(0.10, 0.12, 0.14, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self._samples:
            self._draw_empty(cr, width, height)
            return

        now = self._samples[-1][0]
        start = now - self.window_seconds
        # Auf Daten im Fenster filtern
        samples = [s for s in self._samples if s[0] >= start]
        if not samples:
            self._draw_empty(cr, width, height)
            return

        # Y-Skalierung
        max_down = max(s[1] for s in samples)
        max_up = max(s[2] for s in samples)
        current_max = max(max_down, max_up, 1.0)
        if self.max_rate_kbps:
            y_max = float(self.max_rate_kbps)
        else:
            # Auto: 1.3x aktuelles Max, mind. Startwert
            y_max = max(current_max * 1.3, self._auto_baseline)
            if y_max > 0:
                self._auto_baseline = y_max * 0.9

        def x_of(t: float) -> float:
            frac = (t - start) / self.window_seconds
            return 6.0 + frac * (width - 12.0)

        def y_of(rate: float) -> float:
            frac = min(1.0, max(0.0, rate / y_max))
            return 8.0 + (1.0 - frac) * (height - 16.0)

        # Rasterlinien
        cr.set_source_rgba(0.22, 0.26, 0.30, 0.5)
        cr.set_line_width(1.0)
        bands = 4
        for i in range(bands + 1):
            yy = 8.0 + i * (float(height - 16.0) / bands)
            cr.move_to(6.0, yy)
            cr.line_to(width - 6.0, yy)
        cr.stroke()

        # Grid-Labels (max-rate)
        cr.set_font_size(10)
        cr.set_source_rgba(0.6, 0.66, 0.72, 0.9)
        cr.move_to(8.0, 14.0)
        cr.show_text(self._fmt_rate(y_max))
        cr.move_to(8.0, height - 10.0)
        cr.show_text("0")

        # Download-Kurve (gruen/teal)
        self._stroke_polyline(cr, samples, x_of, y_of, lambda s: s[1],
                              (0.35, 0.85, 0.55, 1.0))
        # Upload-Kurve (orange)
        self._stroke_polyline(cr, samples, x_of, y_of, lambda s: s[2],
                              (0.95, 0.62, 0.25, 1.0))

        # Legende
        cr.set_font_size(10)
        cr.set_source_rgba(0.35, 0.85, 0.55, 1.0)
        cr.move_to(width - 70, 16)
        cr.show_text("Dwn")
        cr.set_source_rgba(0.95, 0.62, 0.25, 1.0)
        cr.move_to(width - 70, 30)
        cr.show_text("Up")

    def _stroke_polyline(self, cr, samples, x_of, y_of, rate_of, color):
        if len(samples) < 2:
            cr.set_source_rgba(*color)
            cr.set_line_width(2.0)
            cr.move_to(x_of(samples[0][0]), y_of(rate_of(samples[0])))
            cr.line_to(x_of(samples[-1][0] + 0.001), y_of(rate_of(samples[-1])))
            cr.stroke()
            return
        cr.set_source_rgba(*color)
        cr.set_line_width(2.0)
        cr.move_to(x_of(samples[0][0]), y_of(rate_of(samples[0])))
        for s in samples[1:]:
            cr.line_to(x_of(s[0]), y_of(rate_of(s)))
        cr.stroke()

    def _draw_empty(self, cr, width, height):
        cr.set_font_size(11)
        cr.set_source_rgba(0.5, 0.55, 0.6, 1.0)
        text = "Keine Daten"
        cr.move_to((width / 2) - cr.text_extents(text).width / 2,
                   height / 2)
        cr.show_text(text)

    def _fmt_rate(self, kbps: float) -> str:
        if kbps >= 1000:
            return f"{kbps / 1000:.1f} Mbps"
        return f"{kbps:.0f} kbps"
