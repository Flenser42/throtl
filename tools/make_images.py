#!/usr/bin/env python3
"""Erzeugt die README-Screenshots und das Demo-GIF aus der echten GTK-App.

Unter einem Display ausfuehren (oder headless via xvfb-run):

    xvfb-run -a -s "-screen 0 1280x900x24" python3 tools/make_images.py

Schreibt nach docs/images/: screenshot.png, graph.png, table.png, demo.gif.
Es wird kein Daemon/root/TrafficToll benoetigt — die Fensterdaten werden
direkt injiziert, damit die Bilder deterministisch sind.
"""

import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

import cairo  # noqa: F401  (registers the cairo foreign struct converter)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="throtl-img-"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")

# cairo-Context fuer draw_funcs (Graph) verfuegbar machen.
gi.require_foreign("cairo")

from gi.repository import Adw, GLib, Graphene, Gsk, Gtk  # noqa: E402

from throtl.gui.app import ThrotlWindow  # noqa: E402

IMAGES = os.path.join(REPO, "docs", "images")
WIDTH, HEIGHT = 1060, 780
GRAPH_BASE = 1000.0  # synthetische Zeitbasis (stabil, kein Epoch-Mix)

# Ein einziger Renderer fuer den ganzen Lauf; beim Verwerfen eines realisierten
# Renderers bricht GSK ab (gsk_renderer_dispose assertion).
_RENDERER = None


def _renderer():
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = Gsk.CairoRenderer.new()
        _RENDERER.realize(None)
    return _RENDERER

# Regeln inkl. Limit, Prioritaet und einem gesetzten Zeitfenster (Uhr-Button).
RULES = [
    {"key": "exe:/usr/lib/firefox/firefox", "name": "Firefox",
     "match_type": "exe", "match_value": "/usr/lib/firefox/firefox",
     "download_limit": 4000, "upload_limit": 500, "priority": "hoch",
     "recursive": False, "window": None},
    {"key": "name:steam", "name": "Steam", "match_type": "name",
     "match_value": "steam", "download_limit": None, "upload_limit": None,
     "priority": "normal", "recursive": False, "window": None},
    {"key": "name:legendary", "name": "legendary", "match_type": "name",
     "match_value": "legendary", "download_limit": 8000, "upload_limit": None,
     "priority": "niedrig", "recursive": False, "window": None},
    {"key": "name:spotify", "name": "Spotify", "match_type": "name",
     "match_value": "spotify", "download_limit": 320, "upload_limit": 64,
     "priority": "niedrig", "recursive": False,
     "window": {"days": [0, 1, 2, 3, 4], "start": "20:00", "end": "00:00"}},
]

APPS = [
    ("Steam", "/usr/bin/steam", 48800.0, 800.0, ["2211"]),
    ("legendary", "python3", 26400.0, 420.0, ["8301", "8302", "8303", "8304"]),
    ("Firefox", "/usr/lib/firefox/firefox", 19200.0, 1150.0, ["4120", "4188"]),
    ("Spotify", "/usr/share/spotify/spotify", 2560.0, 520.0, ["7710"]),
    ("ssh", "ssh", 160.0, 1600.0, ["3366"]),
    ("(unattributed)", "", 4000.0, 210.0, ["-"]),
]


class FakeGui:
    connected = True

    def call(self, method, params=None, timeout=10):
        if method == "get_config":
            return {
                "unit": "mBs",
                "active_profile": "Evening",
                "start_profile": "Evening",
                "global": {"enabled": True, "download_limit": None,
                           "upload_limit": None, "download_minimum": 100,
                           "upload_minimum": 10,
                           "download_priority": "normal",
                           "upload_priority": "normal"},
                "processes": RULES,
            }
        if method == "list_profiles":
            return {"profiles": ["Standard", "University", "Evening"], "active": "Evening"}
        if method == "status":
            return {"monitoring": True, "daemon": "0.5.0"}
        if method == "get_budgets":
            return {"enabled": True, "entries": []}
        return {}

    def call_async(self, *args, **kwargs):
        pass

    def shutdown(self):
        pass


def _factor(t, phase):
    return 0.75 + 0.25 * math.sin(t / 7.0 + phase) + random.uniform(-0.04, 0.04)


def make_state(t):
    apps = []
    for name, exe, down, up, pids in APPS:
        f = _factor(t, len(name))
        apps.append({
            "name": name, "exe": exe,
            "download": round(down * f, 1), "upload": round(up * f, 1),
            "pids": pids, "pid_count": len(pids),
            "unattributed": name.startswith("("),
        })
    apps.sort(key=lambda a: a["download"], reverse=True)
    g_down = round(sum(a["download"] for a in apps) * 1.04, 1)
    g_up = round(sum(a["upload"] for a in apps) * 1.04, 1)
    proc = []
    return {
        "interface": "enp34s0", "enabled": True,
        "global": {"download": g_down, "upload": g_up},
        "attributed": {"download": round(g_down * 0.96, 1),
                       "upload": round(g_up * 0.96, 1)},
        "apps": apps, "processes": proc, "rules": RULES,
    }


def pump(n=6, delay=0.008):
    ctx = GLib.MainContext.default()
    for _ in range(n):
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(delay)


def render(widget, path):
    width, height = widget.get_width(), widget.get_height()
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Widget hat keine Groesse: {widget}")
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, width, height)
    rect = Graphene.Rect().init(0, 0, width, height)
    _renderer().render_texture(snapshot.to_node(), rect).save_to_png(path)
    _flatten(path)
    return width, height


def _flatten(path):
    """RGBA auf den App-Hintergrund legen (vermeidet weisse Transparenz)."""
    from PIL import Image

    image = Image.open(path)
    if image.mode == "RGBA":
        background = Image.new("RGBA", image.size, (18, 22, 27, 255))
        image = Image.alpha_composite(background, image)
    image.convert("RGB").save(path)


def trim_bottom(path, margin=16, tol=18):
    """Einfarbige (leere) Zeilen unten abschneiden — bis zur letzten Textzeile.

    Achtung: transparente Bereiche liest PIL als Weiss, deshalb wird nicht auf
    Helligkeit, sondern auf Einfarbigkeit pro Zeile geprueft.
    """
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, height = image.size
    pixels = image.load()

    def uniform(y):
        ref = pixels[2, y]
        return all(
            sum(abs(pixels[x, y][i] - ref[i]) for i in range(3)) <= tol
            for x in range(0, width, 3)
        )

    last = height - 1
    while last > 4 and uniform(last):
        last -= 1
    new_height = min(height, last + margin)
    if new_height < height:
        image.crop((0, 0, width, new_height)).save(path)


def crop_widget(full_path, widget, root, out_path, trim=False):
    """Einen Widget-Bereich aus dem Vollfenster-Screenshot ausschneiden.

    Widgets einzeln zu rendern liefert fuer die Tabelle ein leeres Bild
    (ScrolledWindow-Kind wird ohne Layout nicht gezeichnet) — daher schneiden
    wir aus dem vollstaendig gerenderten Fenster.
    """
    from PIL import Image

    _ok, rect = widget.compute_bounds(root)
    image = Image.open(full_path).convert("RGB")
    x = max(0, int(rect.get_x()))
    y = max(0, int(rect.get_y()))
    width = min(int(rect.get_width()), image.width - x)
    height = min(int(rect.get_height()), image.height - y)
    image.crop((x, y, x + width, y + height)).save(out_path)
    _flatten(out_path)
    if trim:
        trim_bottom(out_path)


def main():
    random.seed(7)
    os.makedirs(IMAGES, exist_ok=True)
    app = Adw.Application(application_id="io.github.throtl.images")
    app.register(None)
    win = ThrotlWindow(app, FakeGui())
    win.set_default_size(WIDTH, HEIGHT)
    pump(8)
    win.reload()
    pump(8)

    # Graph mit Historie fuellen (60 s Fenster).
    # `_apply_state` wuerde mit echten Epoch-Zeiten pushen und den Graphen
    # leerscrollen -> waehrend der Bilderzeugung stilllegen und die Samples
    # selbst mit konsistenter Zeitbasis pushen.
    real_push = win.graph.push
    win.graph.push = lambda *a, **k: None
    win.graph.clear()   # evtl. Epoch-Sample aus reload() verwerfen
    t = 0
    for _ in range(60):
        t += 1
        state = make_state(t)
        win._apply_state(state)
        real_push(state["global"]["download"], state["global"]["upload"],
                  now=GRAPH_BASE + t)
    pump(8)
    print("Graph-Spanne: %.0f s" % win.graph._time_span())

    # Screenshot des ganzen Fensters; Graph/Table daraus zuschneiden.
    screenshot = os.path.join(IMAGES, "screenshot.png")
    render(win, screenshot)
    crop_widget(screenshot, win.graph, win,
                os.path.join(IMAGES, "graph.png"))
    crop_widget(screenshot, win.table, win,
                os.path.join(IMAGES, "table.png"), trim=True)
    print("Screenshots geschrieben.")

    # Demo-GIF: 30 Frames, Graph scrollt/animiert.
    tmp = tempfile.mkdtemp(prefix="throtl-gif-")
    frames = 30
    for index in range(frames):
        t += 1
        state = make_state(t)
        win._apply_state(state)
        real_push(state["global"]["download"], state["global"]["upload"],
                  now=GRAPH_BASE + t)
        pump(3)
        render(win, os.path.join(tmp, f"frame_{index:03d}.png"))
    print(f"{frames} GIF-Frames gerendert.")

    gif = os.path.join(IMAGES, "demo.gif")
    vf = ("fps=10,scale=720:-1:flags=lanczos,split[s0][s1];"
          "[s0]palettegen[p];[s1][p]paletteuse")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", "10",
         "-i", os.path.join(tmp, "frame_%03d.png"),
         "-vf", vf, "-loop", "0", gif],
        check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("demo.gif geschrieben:", os.path.getsize(gif), "Bytes")
    win.destroy()
    if _RENDERER is not None:
        _RENDERER.unrealize()


if __name__ == "__main__":
    main()
