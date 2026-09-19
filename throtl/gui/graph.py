"""Live bandwidth graph: scrollable history + hover readout (Cairo / GtkDrawingArea).

The graph keeps up to ``max_samples`` samples (1 per poll). Samples are drawn
``PX_PER_SAMPLE`` pixels apart, and the drawing area lives in a horizontal
ScrolledWindow — so you can scroll back through history. New samples keep the
view pinned to the right edge until you scroll away from it.

Hovering with the mouse shows the exact values at that point in time (marker
line + readout text below the graph).
"""

import time as _time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ..units import format_rate

PX_PER_SAMPLE = 6
GRAPH_HEIGHT = 132


class BandwidthGraph(Gtk.Box):
    """Bandwidth over time with scrolling and a hover readout."""

    def __init__(self, max_samples: int = 900, unit: str = "mBs"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._max_samples = max_samples
        self._samples = []          # (t_epoch, down_kbit, up_kbit)
        self._unit = unit
        self._hover = None
        self._autoscroll = True
        self._baseline = 1000.0     # kbit/s, smooths the auto Y-scale

        self._area = Gtk.DrawingArea()
        self._area.set_size_request(320, GRAPH_HEIGHT)
        self._area.set_draw_func(self._draw, None)
        self._area.add_css_class("throtl-graph")

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._scroll.set_child(self._area)
        self._scroll.set_size_request(-1, GRAPH_HEIGHT + 6)
        self._scroll.set_vexpand(False)
        self.append(self._scroll)

        self._readout = Gtk.Label(label="", xalign=0.0, hexpand=True)
        self._readout.add_css_class("dim-label")
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bottom.append(self._readout)
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
        if len(self._samples) > self._max_samples:
            del self._samples[: len(self._samples) - self._max_samples]
        self._resize_area()
        if self._autoscroll:
            self._scroll_to_end()
        self._update_readout()
        self._area.queue_draw()

    def clear(self) -> None:
        self._samples.clear()
        self._hover = None
        self._update_readout()
        self._area.queue_draw()

    # --- Scrolling --------------------------------------------------------

    def _resize_area(self) -> None:
        visible = max(320, self._scroll.get_width())
        wanted = max(visible, len(self._samples) * PX_PER_SAMPLE)
        if wanted != self._area.get_width():
            self._area.set_size_request(wanted, GRAPH_HEIGHT)

    def _scroll_to_end(self) -> None:
        upper = self._hadj.get_upper()
        page = self._hadj.get_page_size()
        self._hadj.set_value(max(0.0, upper - page))

    def _on_scrolled(self, hadj, *_args):
        upper = hadj.get_upper()
        page = hadj.get_page_size()
        at_end = hadj.get_value() >= (upper - page - 2.0)
        self._autoscroll = at_end
        # Beim Scrollen die Auslese aktualisieren (Position bleibt gleich x)
        self._update_readout()

    # --- Hover ------------------------------------------------------------

    def _index_at(self, x: float):
        if not self._samples:
            return None
        idx = int(x // PX_PER_SAMPLE)
        return max(0, min(len(self._samples) - 1, idx))

    def _on_motion(self, _controller, x, _y):
        idx = self._index_at(x)
        if idx != self._hover:
            self._hover = idx
            self._update_readout()
            self._area.queue_draw()

    def _on_leave(self, *_args):
        if self._hover is not None:
            self._hover = None
            self._update_readout()
            self._area.queue_draw()

    def _update_readout(self) -> None:
        if self._hover is None or self._hover >= len(self._samples):
            self._readout.set_text(
                f"History: {len(self._samples)} s — hover the graph to inspect a moment")
            return
        t, down, up = self._samples[self._hover]
        stamp = _time.strftime("%H:%M:%S", _time.localtime(t))
        self._readout.set_text(
            f"{stamp}   ▼ {format_rate(down, self._unit, 2)}"
            f"   ▲ {format_rate(up, self._unit, 2)}")

    # --- Drawing ----------------------------------------------------------

    def _draw(self, _area, cr, width, height, _data) -> None:
        cr.set_source_rgba(0.06, 0.08, 0.10, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self._samples:
            self._draw_centered_text(cr, width, height, "Waiting for traffic…")
            return

        y_max = self._y_scale()

        def x_of(index: int) -> float:
            return index * PX_PER_SAMPLE + PX_PER_SAMPLE / 2.0

        def y_of(rate: float) -> float:
            frac = min(1.0, max(0.0, rate / y_max)) if y_max > 0 else 0.0
            return 8.0 + (1.0 - frac) * (height - 16.0)

        # Grid
        cr.set_source_rgba(0.20, 0.24, 0.28, 0.6)
        cr.set_line_width(1.0)
        bands = 4
        for i in range(bands + 1):
            yy = 8.0 + i * ((height - 16.0) / bands)
            cr.move_to(0, yy)
            cr.line_to(width, yy)
        cr.stroke()

        cr.set_font_size(10)
        cr.set_source_rgba(0.55, 0.61, 0.67, 0.95)
        cr.move_to(6, 14)
        cr.show_text(format_rate(y_max, self._unit, 1))
        cr.move_to(6, height - 8)
        cr.show_text("0")

        self._stroke_curve(cr, x_of, y_of, 1, (0.35, 0.85, 0.55, 1.0))
        self._stroke_curve(cr, x_of, y_of, 2, (0.95, 0.62, 0.25, 1.0))

        # Hover marker
        if self._hover is not None and self._hover < len(self._samples):
            hx = x_of(self._hover)
            _t, down, up = self._samples[self._hover]
            cr.set_source_rgba(0.85, 0.89, 0.94, 0.55)
            cr.set_line_width(1.0)
            cr.move_to(hx, 4)
            cr.line_to(hx, height - 4)
            cr.stroke()
            cr.set_source_rgba(0.35, 0.85, 0.55, 1.0)
            cr.arc(hx, y_of(down), 3.0, 0, 6.2832)
            cr.fill()
            cr.set_source_rgba(0.95, 0.62, 0.25, 1.0)
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

    def _draw_centered_text(self, cr, width, height, text) -> None:
        cr.set_font_size(11)
        cr.set_source_rgba(0.5, 0.55, 0.6, 1.0)
        extents = cr.text_extents(text)
        cr.move_to((width - extents.width) / 2, height / 2)
        cr.show_text(text)
