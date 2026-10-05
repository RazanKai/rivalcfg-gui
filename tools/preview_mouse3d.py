#!/usr/bin/env python3
"""Render the Aerox 5 wireframe to PNG files (no GTK, no display needed).

Uses the same :func:`mouse3d.render` the GUI calls, so what you see here is
exactly what the Buttons page draws -- plus the RGB page's lit preview, via
:func:`mouse3d.render_lighting`.

Usage::

    python tools/preview_mouse3d.py [outdir] [--size 900x700]

Writes ``view_<name>.png`` for the three page views plus a few extra angles
useful for checking geometry, and ``lighting_vertical.png`` for the RGB page.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

import mouse3d  # noqa: E402

#: name -> (yaw_deg, pitch_deg)
VIEWS = {
    "3d": (-120.0, 42.0),      # the default page view
    "top": (0.0, 90.0),        # plan view, nose up
    "left": (-90.0, 0.0),      # left flank (side buttons)
    "right": (90.0, 0.0),      # right flank
    "front": (180.0, 0.0),     # straight at the nose
    "low": (-120.0, 12.0),     # near-level 3/4, shows the flank profile
    "high": (-120.0, 72.0),    # steep, shows the top surfaces
}

#: What the RGB page uses: the 3/4 view, rolled so the body stands up.
LIGHTING_YAW = -120.0
LIGHTING_PITCH = 42.0
#: Zone colours for the preview (z1 nose, z2 middle, z3 tail-with-the-strip).
LIGHTING_COLORS = ((1.0, 0.10, 0.10), (0.15, 1.0, 0.20), (0.25, 0.35, 1.0))


def render_view(name, yaw_deg, pitch_deg, w, h, outdir, labels=True):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    view = mouse3d.View(yaw=math.radians(yaw_deg), pitch=math.radians(pitch_deg))
    mouse3d.fit_view(w, h, view=view, fill=0.86)
    label_fn = (lambda key, label: label) if labels else None
    mouse3d.render(cr, w, h, view, label_fn=label_fn)
    path = os.path.join(outdir, "view_%s.png" % name)
    surface.write_to_png(path)
    return path


def render_lighting_view(name, colors, w, h, outdir, size=None):
    """The RGB page's preview: the mouse stood upright and lit per zone.

    *size* overrides the canvas (the page uses a smaller drawing area than
    the wireframe views), so the same code can be eyeballed at page scale.
    """
    if size is not None:
        w, h = size
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    yaw = math.radians(LIGHTING_YAW)
    pitch = math.radians(LIGHTING_PITCH)
    view = mouse3d.View(yaw=yaw, pitch=pitch,
                        roll=mouse3d.upright_roll(yaw, pitch))
    _view, geo = mouse3d.build_lighting_geometry(w, h, view=view)
    mouse3d.render_lighting(cr, w, h, geo, colors)
    path = os.path.join(outdir, "lighting_%s.png" % name)
    surface.write_to_png(path)
    return path


def main():
    args = sys.argv[1:]
    outdir = args[0] if args and not args[0].startswith("-") else "/tmp/mouse3d"
    w, h = 900, 700
    if "--size" in args:
        w, h = (int(v) for v in args[args.index("--size") + 1].split("x"))
    labels = "--no-labels" not in args
    os.makedirs(outdir, exist_ok=True)

    for name, (yaw, pitch) in VIEWS.items():
        print("wrote", render_view(name, yaw, pitch, w, h, outdir, labels))

    # Contact sheet: all views in a grid, for a quick overall look.
    cols, rows = 4, 2
    cw, ch = w // 2, h // 2
    sheet = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw * cols, ch * rows)
    scr = cairo.Context(sheet)
    scr.set_source_rgb(0.09, 0.09, 0.11)
    scr.paint()
    for i, (name, (yaw, pitch)) in enumerate(VIEWS.items()):
        cx, cy = (i % cols) * cw, (i // cols) * ch
        scr.save()
        scr.translate(cx, cy)
        scr.rectangle(0, 0, cw, ch)
        scr.clip()
        view = mouse3d.View(yaw=math.radians(yaw),
                            pitch=math.radians(pitch))
        mouse3d.fit_view(cw, ch, view=view, fill=0.86)
        mouse3d.render(scr, cw, ch, view, label_fn=None)
        scr.restore()
    sheet_path = os.path.join(outdir, "sheet.png")
    sheet.write_to_png(sheet_path)
    print("wrote", sheet_path)

    # The RGB page's preview, at the drawing area's own size, plus a
    # single-colour pass that shows the shading with the zone bands removed
    # and the rainbow, which lights the body with a hue sweep instead of the
    # three zone colours (a callable in place of the colour triplets).
    print("wrote", render_lighting_view("vertical", LIGHTING_COLORS,
                                        w, h, outdir, size=(260, 340)))
    print("wrote", render_lighting_view("shading", ((0.55, 0.75, 1.0),) * 3,
                                        w, h, outdir, size=(260, 340)))
    print("wrote", render_lighting_view("rainbow", mouse3d.rainbow_colour,
                                        w, h, outdir, size=(260, 340)))


if __name__ == "__main__":
    main()
