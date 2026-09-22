"""Live bandwidth graph: time-based, auto-scrolling history + hover readout.

The graph shows a configurable time window (default: the last 60 seconds) and
scrolls automatically as new samples arrive, so you always see the most recent
traffic. Scrolling back through the history pauses auto-scroll; scrolling back
to the right edge resumes it. A small dropdown switches the window between
30 s / 1 min / 5 min / 15 min / All (fit the whole history).

The x axis is real time (epoch seconds), so irregular poll intervals show up as
gaps instead of being squashed together.

Hovering with the mouse shows the exact values at that point in time (marker
line + readout text below the graph).
"""

import bisect
import time as _time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk

from ..units import format_rate

GRAPH_HEIGHT = 118
PAD = 16.0            # horizontal padding inside the canvas
MIN_CANVAS = 320.0

# (label, seconds); 0 = fit the entire history into the viewport
WINDOW_CHOICES = (
    ("30 s", 30),
    ("1 min", 60),
    ("5 min", 300),
    ("15 min", 900),
    ("All", 0),
)


def theme_colors() -> dict:
    """Graph-Farben je nach Hell/Dunkel (Adwaita-Schema).

    Ein ``Gtk.DrawingArea`` kann die benannten Adwaita-Farben nicht lesen,
    deshalb liegen die Literale hier an genau einer Stelle: Live-Graph und
    Statistik-Graph teilen sie sich, damit beide Themes stimmen.
    """
    try:
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        dark = Adw.StyleManager.get_default().get_dark()
    except Exception:
        dark = True
    if dark:
        return {
            "bg": (0.055, 0.067, 0.082, 1.0),
            "grid": (0.20, 0.24, 0.28, 0.6),
            "text": (0.55, 0.61, 0.67, 0.95),
            "down": (0.31, 0.82, 0.50, 1.0),
            "up": (0.96, 0.64, 0.35, 1.0),
            "marker": (0.85, 0.89, 0.94, 0.55),
        }
    return {
        "bg": (0.98, 0.98, 0.98, 1.0),
        "grid": (0.85, 0.87, 0.89, 1.0),
        "text": (0.33, 0.36, 0.40, 0.95),
        "down": (0.18, 0.76, 0.47, 1.0),
        "up": (0.90, 0.42, 0.00, 1.0),
        "marker": (0.20, 0.22, 0.25, 0.55),
    }


class BandwidthGraph(Gtk.Box):
    """Bandwidth over time with auto-scrolling and a hover readout."""

    def __init__(self, max_samples: int = 900, unit: str = "mBs",
                 window_seconds: int = 60):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._max_samples = max_samples
        self._samples = []          # (t_epoch, down_kbit, up_kbit)
        self._times = []            # cached timestamps, for bisect()
        self._unit = unit
        self._window_seconds = window_seconds
        self._pps = 1.0             # pixels per second
        self._hover = None
        self._autoscroll = True
        self._baseline = 1000.0     # kbit/s, smooths the auto Y-scale

        self._area = Gtk.DrawingArea()
        self._area.set_size_request(int(MIN_CANVAS), GRAPH_HEIGHT)
        self._area.set_draw_func(self._draw, None)
        self._area.add_css_class("throtl-graph")

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.add_css_class("graph-scroll")
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._scroll.set_child(self._area)
        self._scroll.set_size_request(-1, GRAPH_HEIGHT + 6)
        self._scroll.set_vexpand(False)
        self.append(self._scroll)

        # --- bottom row: readout | window selector | legend ---
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._readout = Gtk.Label(label="", xalign=0.0, hexpand=True)
        self._readout.add_css_class("dim-label")
        bottom.append(self._readout)

        self._window_dd = Gtk.DropDown(model=Gio.ListStore.new(Gtk.StringObject))
        for label, _seconds in WINDOW_CHOICES:
            self._window_dd.get_model().append(Gtk.StringObject.new(label))
        self._window_dd.set_selected(self._window_index(window_seconds))
        self._window_dd.set_tooltip_text("Visible time window")
        self._window_dd.add_css_class("throtl-window")
        self._window_dd.connect("notify::selected", self._on_window_changed)
        bottom.append(self._window_dd)

        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        legend_down = Gtk.Label(label="● Download")
        legend_down.add_css_class("legend-down")
        legend_up = Gtk.Label(label="● Upload")
        legend_up.add_css_class("legend-up")
        legend.append(legend_down)
        legend.append(legend_up)
        bottom.append(legend)
        self.append(bottom)

        self._hadj = self._scroll.get_hadjustment()
        self._hadj.connect("value-changed", self._on_scrolled)
        # Sobald das Adjustment sich aendert (Canvas-Breite), ist upper aktuell
        # -> dann ans Ende springen, solange Auto-Scroll aktiv ist.
        self._hadj.connect("changed", self._on_adjustment_changed)
        # Viewport-Groesse (Fenster-Resize) -> Skala/Canvas neu berechnen.
        self._hadj.connect("notify::page-size", lambda *_a: self._recompute())

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self._area.add_controller(motion)

    # --- Public API -------------------------------------------------------

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self._update_readout()
        self._area.queue_draw()

    def push(self, down_kbit: float, up_kbit: float, now: float | None = None) -> None:
        t = _time.time() if now is None else now
        self._samples.append((t, max(0.0, down_kbit), max(0.0, up_kbit)))
        self._times.append(t)
        if len(self._samples) > self._max_samples:
            del self._samples[: len(self._samples) - self._max_samples]
            self._times = [sample[0] for sample in self._samples]
        self._recompute()
        if self._autoscroll:
            self._scroll_to_end()
        self._update_readout()
        self._area.queue_draw()

    def clear(self) -> None:
        self._samples.clear()
        self._times.clear()
        self._hover = None
        self._update_readout()
        self._area.queue_draw()

    # --- Scaling / layout -------------------------------------------------

    @staticmethod
    def _window_index(seconds: int) -> int:
        for index, (_label, value) in enumerate(WINDOW_CHOICES):
            if value == seconds:
                return index
        return 1  # 1 min

    def _window_label(self) -> str:
        return WINDOW_CHOICES[self._window_index(self._window_seconds)][0]

    def _viewport_width(self) -> float:
        page = self._hadj.get_page_size()
        if page <= 0:
            page = self._scroll.get_width()
        return max(MIN_CANVAS, float(page))

    def _time_span(self) -> float:
        if len(self._times) < 2:
            return 0.0
        return max(0.0, self._times[-1] - self._times[0])

    def _recompute(self) -> None:
        """Pixel-pro-Sekunde + Canvas-Breite an Fenster/Viewport anpassen."""
        viewport = self._viewport_width()
        span = self._time_span()
        window = float(self._window_seconds)
        if window <= 0 or span < window:
            # Weniger Historie als das Fenster (z. B. direkt nach dem Start):
            # die vorhandenen Samples auf die volle Breite ziehen, damit der
            # Graph nicht als schmaler Strich am rechten Rand erscheint.
            # Sobald die Historie das Fenster fuellt, wird normal gescrollt.
            window = max(span, 1.0)
        self._pps = max(0.05, (viewport - 2 * PAD) / window)
        needed = span * self._pps + 2 * PAD
        # Scrollbalken nur einblenden, wenn die Historie wirklich breiter ist
        # als der Viewport (sonst blitzte unten ein voller Track auf).
        fits = needed <= viewport + 1.0
        policy = Gtk.PolicyType.NEVER if fits else Gtk.PolicyType.AUTOMATIC
        if self._scroll.get_policy()[0] != policy:
            self._scroll.set_policy(policy, Gtk.PolicyType.NEVER)
        wanted = int(viewport) if fits else int(needed)
        if wanted != self._area.get_width():
            self._area.set_size_request(wanted, GRAPH_HEIGHT)

    def _scroll_to_end(self) -> None:
        upper = self._hadj.get_upper()
        page = self._hadj.get_page_size()
        self._hadj.set_value(max(0.0, upper - page))

    def _on_adjustment_changed(self, *_args):
        if self._autoscroll:
            self._scroll_to_end()

    def _on_scrolled(self, hadj, *_args):
        upper = hadj.get_upper()
        page = hadj.get_page_size()
        at_end = hadj.get_value() >= (upper - page - 2.0)
        self._autoscroll = at_end
        self._update_readout()

    def _on_window_changed(self, dd, *_args):
        index = dd.get_selected()
        if 0 <= index < len(WINDOW_CHOICES):
            self._window_seconds = WINDOW_CHOICES[index][1]
        # Fensterwechsel: immer wieder ans aktuelle Ende springen.
        self._autoscroll = True
        self._recompute()
        self._scroll_to_end()
        self._update_readout()
        self._area.queue_draw()

    # --- Hover ------------------------------------------------------------

    def _index_at(self, x: float):
        if not self._samples:
            return None
        width = float(self._area.get_width())
        t_end = self._times[-1]
        t = t_end - (width - PAD - x) / max(self._pps, 1e-6)
        index = bisect.bisect_left(self._times, t)
        if index >= len(self._samples):
            index = len(self._samples) - 1
        elif index > 0 and abs(self._times[index - 1] - t) <= abs(self._times[index] - t):
            index -= 1
        return max(0, min(len(self._samples) - 1, index))

    def _on_motion(self, _controller, x, _y):
        index = self._index_at(x)
        if index != self._hover:
            self._hover = index
            self._update_readout()
            self._area.queue_draw()

    def _on_leave(self, *_args):
        if self._hover is not None:
            self._hover = None
            self._update_readout()
            self._area.queue_draw()

    def _update_readout(self) -> None:
        if self._hover is None or self._hover >= len(self._samples):
            window = "all" if self._window_seconds <= 0 else self._window_label()
            self._readout.set_text(
                f"History: {self._time_span():.0f} s · window {window}"
                " — hover the graph to inspect a moment")
            return
        t, down, up = self._samples[self._hover]
        stamp = _time.strftime("%H:%M:%S", _time.localtime(t))
        self._readout.set_text(
            f"{stamp}   ▼ {format_rate(down, self._unit, 2)}"
            f"   ▲ {format_rate(up, self._unit, 2)}")

    # --- Drawing ----------------------------------------------------------

    def _colors(self) -> dict:
        """Farben je nach Hell/Dunkel (Adwaita-Schema)."""
        return theme_colors()

    def _draw(self, _area, cr, width, height, _data) -> None:
        colors = self._colors()
        cr.set_source_rgba(*colors["bg"])
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self._samples:
            self._draw_centered_text(cr, width, height, "Waiting for traffic…",
                                     colors["text"])
            return

        t_end = self._times[-1]

        def x_of(index: int) -> float:
            return width - PAD - (t_end - self._times[index]) * self._pps

        y_max = self._y_scale()

        def y_of(rate: float) -> float:
            frac = min(1.0, max(0.0, rate / y_max)) if y_max > 0 else 0.0
            return 8.0 + (1.0 - frac) * (height - 16.0)

        # Grid
        cr.set_source_rgba(*colors["grid"])
        cr.set_line_width(1.0)
        bands = 4
        for i in range(bands + 1):
            yy = 8.0 + i * ((height - 16.0) / bands)
            cr.move_to(0, yy)
            cr.line_to(width, yy)
        cr.stroke()

        cr.set_font_size(10)
        cr.set_source_rgba(*colors["text"])
        cr.move_to(6, 14)
        cr.show_text(format_rate(y_max, self._unit, 1))
        cr.move_to(6, height - 8)
        cr.show_text("0")

        self._stroke_curve(cr, x_of, y_of, 1, colors["down"])
        self._stroke_curve(cr, x_of, y_of, 2, colors["up"])

        # Hover marker
        if self._hover is not None and self._hover < len(self._samples):
            hx = x_of(self._hover)
            _t, down, up = self._samples[self._hover]
            cr.set_source_rgba(*colors["marker"])
            cr.set_line_width(1.0)
            cr.move_to(hx, 4)
            cr.line_to(hx, height - 4)
            cr.stroke()
            cr.set_source_rgba(*colors["down"])
            cr.arc(hx, y_of(down), 3.0, 0, 6.2832)
            cr.fill()
            cr.set_source_rgba(*colors["up"])
            cr.arc(hx, y_of(up), 3.0, 0, 6.2832)
            cr.fill()

    def _y_scale(self) -> float:
        peak = 1.0
        for _t, down, up in self._samples:
            peak = max(peak, down, up)
        target = max(peak * 1.25, self._baseline)
        # sanfte Anpassung, damit die Skala nicht springt
        self._baseline = max(self._baseline * 0.94, target * 0.9, 100.0)
        return max(target, 100.0)

    def _stroke_curve(self, cr, x_of, y_of, index, color) -> None:
        if not self._samples:
            return
        cr.set_source_rgba(*color)
        cr.set_line_width(2.0)
        cr.move_to(x_of(0), y_of(self._samples[0][index]))
        for i in range(1, len(self._samples)):
            cr.line_to(x_of(i), y_of(self._samples[i][index]))
        cr.stroke()

    def _draw_centered_text(self, cr, width, height, text, color) -> None:
        cr.set_font_size(11)
        cr.set_source_rgba(*color)
        extents = cr.text_extents(text)
        cr.move_to((width - extents.width) / 2, height / 2)
        cr.show_text(text)
