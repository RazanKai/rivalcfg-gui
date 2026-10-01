#!/usr/bin/env python3
"""Render the Aerox 5 wireframe to PNG files (no GTK, no display needed).

Uses the same :func:`mouse3d.render` the GUI calls, so what you see here is
exactly what the Buttons page draws.

Usage::

    python tools/preview_mouse3d.py [outdir] [--size 900x700]

Writes ``view_<name>.png`` for the three page views plus a few extra angles
useful for checking geometry.
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


if __name__ == "__main__":
    main()
