#!/usr/bin/env python3
"""Fit the corrections drawn in ``aerox5_3d_files/error_shape*.png``.

Both files are screenshots of the button-mapping page with red strokes drawn
over them *by hand*.  They are the shape the edges should have, not markers
pointing at defects:

* in ``error_shape.png`` the long S-stroke runs down the channel the left
  click panel leaves for the two top side paddles (buttons 7 and 8), and the
  short bracket crosses button 4 -- entering along its top edge, crossing to
  its bottom edge -- roughly 7 mm before the drawn tip;
* in ``error_shape2.png``, drawn later over the page as the first fit leaves
  it, the arc runs down the left click panel's rear-lower corner, and a bowed
  arc crosses button 5 where its leading end should be -- with an X on the
  material forward of it, the part that comes off;

This script registers each screenshot against a headless render of the same
view (``fit_view(1400, 900)``), reads each stroke as a centre line, and turns
them into the three tables ``mouse3d`` carries:

* the two panel strokes into :data:`mouse3d._SEAM_FIX`, per outline vertex of
  the panel they run along, and
* the bracket and the arc into :data:`mouse3d._TAIL_CUT_MM` and
  :data:`mouse3d._LEAD_CUT_MM`, the stations those two paddles end at.

The strokes are hand-drawn: they were meant to *show* the edges, not to be
them.  So none of them is copied.  Each panel stroke is estimated --
:func:`seam_estimate` fits a stiff polynomial through it, with the pen's own
wobble and the branches it throws off averaged or clipped away -- and each
outline vertex is then pulled onto the nearest point of *that* line by the
distance between them, weighted by the angle between the two; see
:func:`seam_field`.  Each end stroke is read back into its paddle's own
planar ``(y, z)`` frame: the bracket crosses the paddle, so the station is the
median station of the rows that crossed it, and the arc is drawn across the
paddle without crossing it, so the station is the most forward point it
reaches.

The two panel strokes were drawn over *different* pages.  ``error_shape.png``
corrects the outline the mesh seats; ``error_shape2.png`` corrects the outline
*as the first fit leaves it*.  So the strokes are fitted **in order**, each
against the page the ones before it draw: the second stroke's displacements are
measured from the corrected outline, not from the mesh's, and the two fields
are summed.  Fitting the second against the mesh's own outline would fold the
first correction back into it; fitting the first against the module's shipped
table would inherit that table's own error, since it no longer reproduces a fit
of its own stroke.

Run it after changing the mesh, the view, or an annotation::

    python3 tools/fit_seam_correction.py            # report only
    python3 tools/fit_seam_correction.py --write    # rewrite mouse3d.py
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rivalcfg_gui import mouse3d  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The page's own view angles, and the render the first annotation was
#: registered against.
YAW, PITCH = math.radians(-120.0), math.radians(42.0)
VIEW_W, VIEW_H, VIEW_FILL = 1400, 900, 0.66


def make_view(scale=None, offset=None):
    """The page's view, optionally re-registered to one annotation's frame.

    ``fit_view`` sets the scale and offset that put the whole mouse in a
    ``VIEW_W`` x ``VIEW_H`` render; an annotation's registration replaces them,
    which turns :meth:`mouse3d.View.project` into the map from the model
    straight to that image's pixels.  Working in the annotation's own frame is
    what lets one view serve a stroke at any zoom: everything downstream --
    the Jacobian, the gap to the stroke, the weights -- is either invariant
    under the change or scaled with it, and the solve divides it back out.
    """
    view = mouse3d.View(yaw=YAW, pitch=PITCH)
    mouse3d.fit_view(VIEW_W, VIEW_H, view=view, fill=VIEW_FILL)
    if scale is not None:
        view.scale = scale
        view.offset_x, view.offset_y = offset
    return view


_FIT = make_view()

#: The annotations, and how each is read.  Each carries the view its own pixels
#: are in, registered by hand: an annotation pixel is ``scale * model +
#: offset``.  The first was marked up over a headless render at
#: ``fit_view(VIEW_W, VIEW_H)`` and is registered against *that* render's own
#: scale and offset -- 1.27x, then shifted.  The second was marked up over a
#: screenshot of the running page, whose view is the same angles at a
#: different zoom, and is registered directly.  Both land at 600-1100
#: annotation pixels per model unit, so a pixel is a pixel's worth in either
#: and the constants below are shared.
#:
#: ``nib`` is half the pen's width, and ``runs`` says how a row of ink is
#: reduced to one sample.  Every stroke is the leftmost ink on its row -- each
#: of them throws a branch off to the right somewhere -- but only the second
#: image's pen is thinner than the gap its branches leave, which is what puts
#: two runs on a row: there the leftmost *run* is followed and half a nib
#: stepped in from it.  The first image's rows are read as one span instead,
#: half a nib in from the left edge of it, which is the reading its table was
#: fitted at.
ANNOTATIONS = {
    "error_shape.png": dict(
        view=make_view(_FIT.scale * 1.27,
                       (_FIT.offset_x * 1.27 - 352.0,
                        _FIT.offset_y * 1.27 - 210.0)),
        nib=4.5, runs="span"),
    "error_shape2.png": dict(
        view=make_view(627.0, (147.0, 91.0)),
        nib=2.0, runs="leftmost"),
}

#: A stroke is only read as describing an edge from within this many
#: annotation pixels of it; past that it belongs to some other part.  Just
#: over the ~41 px the drawn seam is off the panel's edge at its widest --
#: the parallelism weight, not this cap, is what takes the correction back
#: off where a stroke leaves the edge and runs along a paddle.
NEAR = 45.0

#: Vertices between knots in the emitted table, and the half-width of the
#: smoothing window (in vertices) applied along the outline.
STRIDE = 6
SMOOTH = 16

#: Red minus blue above the first, and red above the second, is stroke ink.
RED_RB, RED_MIN = 40, 90

#: How far the pen may be off the seam before what it drew stops being a
#: description of it: at the ends of the long stroke the hand carried on round
#: the keycap and ran the pen down its bottom edge, a turn of 30-50 degrees
#: against the seam's own few.  Measured on a median-filtered copy of the
#: stroke, so the branch it threw off at the paddles' ends -- a 40 px spike
#: over a dozen rows -- cannot fake the turn.
#:
#: A stroke with no seam in it -- the second image's arc is a curve the whole
#: way down -- has no such turn to find, and ``None`` fits all of it.
SEAM_TURN_DEG = 30.0

#: The estimate is a polynomial this many degrees of freedom in the stroke's
#: own length, fitted with outliers beyond this many sigma clipped out.  A
#: cubic is already stiffer than the ink by two orders of magnitude (four
#: coefficients against 230 rows) and leaves a residual of ~1 px, which is the
#: hand's wobble; anything stiffer just re-draws the wobble.
SEAM_DEGREE, SEAM_CLIP = 3, 2.5

#: Annotation pixels over which the correction eases out past the ends of the
#: estimated seam.  The drawn seam stops where the pen left it, and the mesh's
#: own edge carries on well to one side of the estimate: cutting the correction
#: off at its last knot steps the edge across that gap in a few vertices, so it
#: is faded out over this distance instead.
SEAM_FADE = 45.0

#: The strokes that describe an *edge*: ``(annotation, component, key,
#: seating, turn)``.  *seating* is the outline that stroke was drawn over --
#: ``"mesh"`` for the cap as the mesh seats it, ``"drawn"`` for the cap as
#: :mod:`mouse3d` draws it today -- and each vertex is pulled onto the stroke
#: nearest it, so a stroke drawn over the corrected page adds to the
#: correction drawn over the mesh's.
SEAM_STROKES = (
    # annotation, component, key, turn -- fitted in this order, each against
    # the page the strokes before it draw
    ("error_shape.png", 1, "button1", SEAM_TURN_DEG),
    ("error_shape2.png", 1, "button1", None),
)

#: The strokes that describe an *end*: ``(annotation, component, key, end,
#: runs)``.  The station read off each is where that end now stops.
#:
#: How a row of ink is reduced to one sample differs between them for the same
#: reason it does between the panel strokes: button 4's bracket is crossed by
#: nothing, so the row's *last* run is the stroke and the rows where the pen
#: ran along the paddle's top edge can be dropped by width; button 5's arc has
#: the "X" that marks the material to remove drawn *left* of it -- the paddle's
#: leading end is the left of its cap -- so the row's *last* run is the stroke
#: and no width can be trusted.
CUT_STROKES = (
    # annotation, component, key, end, runs
    ("error_shape.png", 2, "button4", "tail", "last"),
    ("error_shape2.png", 2, "button5", "lead", "rightmost"),
)

#: End strokes overshoot the paddle at both ends, so only stations this far
#: inside the drawn height are read as the cut.
CUT_MARGIN_MM = 0.2

#: How near the extreme a station has to be to count as part of the apex.
#: The arc names where an end *stops*, and its most forward point is what the
#: part reaches to; taking the median of the stations that share it shrugs off
#: the odd row where the pen doubled back over itself.
APEX_BAND_MM = 0.3

#: The mesh cap each corrected outline is built from.  The fit runs against
#: that cap's outline *before* the correction is applied -- the one the
#: annotation was drawn over -- because fitting against the corrected one
#: measures the correction's residual instead, and re-running the tool would
#: then apply it a second time.
MESH = {
    "button1": "main_button_left",
}

#: The model axes each outline is built in, in the order displacements are
#: expressed in: the top panels are planar in ``(x, y)``, the flank buttons
#: in ``(y, z)``.
PLANE = {
    "button1": ("x", "y"),
}


def annotation_path(name):
    return os.path.join(ROOT, "aerox5_3d_files", name)


def stroke_centre_lines(name):
    """Return ``{component: [(x, y), ...]}`` centre lines of the red strokes."""
    spec = ANNOTATIONS[name]
    ann = np.asarray(Image.open(annotation_path(name)).convert("RGB")).astype(int)
    red = (ann[:, :, 0] - ann[:, :, 2] > RED_RB) & (ann[:, :, 0] > RED_MIN)
    labels, count = ndimage.label(red, np.ones((3, 3)))
    lines = {}
    for comp in range(1, count + 1):
        ys, xs = np.nonzero(labels == comp)
        if len(xs) < 40:
            continue
        pts = []
        for y in range(ys.min(), ys.max() + 1):
            row = np.sort(xs[ys == y])
            if len(row) == 0:
                continue
            if spec["runs"] == "leftmost":
                runs, start = [], row[0]
                for a, b in zip(row[:-1], row[1:]):
                    if b > a + 1:
                        runs.append((start, a))
                        start = b
                runs.append((start, row[-1]))
                left, right = runs[0]
                sample = left + min(0.5 * (right - left), spec["nib"])
            else:
                # The stroke is always the leftmost ink, and only its branch
                # is wider than the nib: track the row's left edge and step in
                # by half the nib -- or, on the rows the branch joins, by half
                # the span, which is where the stroke itself sits.
                width = row.max() - row.min()
                sample = row.min() + (0.5 * width if width > 30 else spec["nib"])
            pts.append((float(sample), float(y)))
        lines[comp] = np.array(pts, float)
    return lines


def bracket_rows(name, comp, runs="last", max_width=12):
    """Read a crossing stroke as ``[(x, y), ...]``, one centre per row.

    Reads only one component of one annotation -- the strokes that describe an
    end -- and the caller says which of the row's runs is the stroke.  With
    ``"last"`` the rows where the pen ran along the paddle's top edge and turned
    are dropped by width: they merge the approach and the descent into one run
    several nibs wide.  ``"leftmost"`` and ``"rightmost"`` take an end run
    outright, for rows the pen shares with a second mark: the removal X drawn
    beside the hook, and the branch the panel stroke throws off across the
    channel.
    """
    spec = ANNOTATIONS[name]
    ann = np.asarray(Image.open(annotation_path(name)).convert("RGB")).astype(int)
    red = (ann[:, :, 0] - ann[:, :, 2] > RED_RB) & (ann[:, :, 0] > RED_MIN)
    labels, _count = ndimage.label(red, np.ones((3, 3)))
    ys, xs = np.nonzero(labels == comp)
    rows = []
    for y in range(ys.min(), ys.max() + 1):
        row = np.sort(xs[ys == y])
        if len(row) == 0:
            continue
        spans, start = [], row[0]
        for a, b in zip(row[:-1], row[1:]):
            if b > a + 1:
                spans.append((start, a))
                start = b
        spans.append((start, row[-1]))
        if runs in ("leftmost", "rightmost"):
            a, b = spans[0] if runs == "leftmost" else spans[-1]
            edge = a if runs == "leftmost" else b
            step = min(0.5 * (b - a), spec["nib"])
            rows.append((edge + step if runs == "leftmost" else edge - step,
                         float(y)))
            continue
        if max(b - a for a, b in spans) > max_width:
            continue
        a, b = spans[-1]
        rows.append((0.5 * (a + b), float(y)))
    return np.array(rows, float)


def seam_estimate(line, degree=SEAM_DEGREE, clip=SEAM_CLIP, max_turn=SEAM_TURN_DEG):
    """The edge the drawn seam describes: ``(polyline, y_start, y_end)``.

    The stroke is *evidence* of the edge, not the edge: read literally it
    wobbles a few pixels row to row, throws a branch off to the right where the
    pen met the paddles' ends, and at each end runs off the seam entirely.  So
    the part of it that follows the seam is found, and a stiff polynomial is
    fitted through that part with the branch clipped out; the fit is then
    sampled, and what comes back is the seam with the hand's error averaged
    away.

    The seam portion is the run that holds the top of the stroke, ending where
    the pen turns off.  The turn is measured on a median-filtered copy --
    a single row can be wildly off without moving a median -- so the branch,
    which is a wide spike rather than a wide run, leaves it alone.  A stroke
    that holds the pen all the way down has no such turn: *max_turn* is then
    ``None`` and the whole of it is the edge.

    The two ends returned are the seam's, not the polyline's: the polyline is
    carried on straight past each of them by :data:`SEAM_FADE`, because the
    correction has to keep pointing the way the seam runs while it eases out,
    and a vertex below the seam would otherwise be dragged sideways at the last
    knot.  A stroke that is the edge the whole way -- *max_turn* ``None`` -- is
    not carried on at all: it needs no fade (its own ends already sit on the
    edge), and a line continued past where the pen stopped would describe an
    edge nobody drew, well up the rear edge and down along the bottom one.
    """
    y = line[:, 1]
    x = ndimage.median_filter(line[:, 0], 21)
    if max_turn is None:
        end = len(y)
    else:
        xs = ndimage.uniform_filter1d(x, 21, mode="nearest")
        ys = ndimage.uniform_filter1d(y, 21, mode="nearest")
        turn = np.degrees(np.arctan2(np.gradient(xs), np.gradient(ys)))

        held = np.abs(turn) < max_turn
        if not held[0]:                  # no seam at all: nothing to estimate
            return line, float(y.min()), float(y.max())
        end = int(np.argmin(held)) if not held.all() else len(y)
    span = slice(0, max(end, degree + 2))

    # The polynomial is in the stroke's own length; its outliers are the branch
    # and nothing else, so a couple of passes settle it.
    t = (y[span] - y[span].min()) / (y[span].max() - y[span].min())
    keep = np.ones(len(t), bool)
    coeffs = np.polyfit(t, x[span], degree)
    for _ in range(8):
        residual = x[span] - np.polyval(coeffs, t)
        sigma = np.std(residual[keep])
        keep = np.abs(residual) < clip * sigma
        coeffs = np.polyfit(t[keep], x[span][keep], degree)

    fine = np.linspace(t[0], t[-1], max(64, end))
    curve = np.stack([np.polyval(coeffs, fine),
                      y[span].min() + fine * (y[span].max() - y[span].min())], 1)
    if max_turn is None:
        polyline = curve
    else:
        steps = np.arange(1.0, SEAM_FADE + 1.0)[:, None]
        head, tail = curve[0] - curve[1], curve[-1] - curve[-2]
        polyline = np.concatenate([curve[0] + head / np.hypot(*head) * steps[::-1],
                                   curve,
                                   curve[-1] + tail / np.hypot(*tail) * steps])
    return polyline, float(y[span].min()), float(y[span].max())


def nearest_on(line, p):
    """Closest point of a polyline, that segment's direction, and whether the
    closest point is one of the line's ends.

    The end flag matters because the estimate is finite: a vertex past the far
    end has nowhere on the line to go, and must not be dragged to the last knot
    as if that knot were the edge.
    """
    best = (float("inf"), None, None, False)
    last = len(line) - 2
    for k, (a, b) in enumerate(zip(line[:-1], line[1:])):
        ab = b - a
        length2 = float(ab @ ab)
        t = float(np.clip(((p - a) @ ab) / length2, 0.0, 1.0)) if length2 else 0.0
        q = a + t * ab
        d = float(np.hypot(*(p - q)))
        if d < best[0]:
            at_end = (t == 0.0 and k == 0) or (t == 1.0 and k == last)
            best = (d, q, ab / math.sqrt(length2) if length2 else ab, at_end)
    return best


def ease(ay, y_start, y_end, fade=SEAM_FADE):
    """1 along the seam, falling to 0 within *fade* px of either end of it."""
    return max(0.0, min(1.0, min((ay - y_start) / fade + 1.0,
                                 (y_end - ay) / fade + 1.0)))


def seated_jacobian(view, point, lift=0.006, step=0.01):
    """2x2 map from a *crown-plane* (x, y) step at *point* to a screen step.

    The cap is not a flat plate.  The page lays each outline vertex on the
    shell, ``z = top_surface_z(x, y) + lift``, so the map the solve in
    :func:`seam_field` has to invert is ``(x, y) -> project(x, y, seat(x, y))``,
    and its Jacobian folds in the surface slope ``dz/d(x, y)``.  Differencing
    the bare projection while holding z fixed omits that term; where the crown
    falls away -- the rear-lower corner of the click panel -- the missing piece
    is up to 2.3x the y-column, which sends the solve the wrong way.  Perturb x
    and y and let the seating recompute z.
    """
    base = np.array(view.project(
        mouse3d.Point3D(*mouse3d._snap_to_shell(point.x, point.y, lift))), float)
    cols = []
    for dx, dy in ((step, 0.0), (0.0, step)):
        seated = mouse3d._snap_to_shell(point.x + dx, point.y + dy, lift)
        cols.append((np.array(view.project(mouse3d.Point3D(*seated)), float) - base) / step)
    return np.stack(cols, 1)


def cyclic_smooth(field, radius):
    """Box-smooth an ``(n, 2)`` array around a closed loop."""
    n = len(field)
    span = 2 * radius + 1
    tiled = np.concatenate([field[-radius:], field, field[:radius]])
    out = np.empty_like(field)
    for a in range(2):
        out[:, a] = np.convolve(tiled[:, a], np.ones(span) / span, "valid")
    return out


def on_flank(view, target, lift=0.004, start=None, iters=30):
    """Model ``(y, z)``, in mm, of the flank point drawn at *target*.

    The stroke was drawn over the cap as the page *seats* it, so the map being
    inverted is the whole seating -- ``x = side_surface_x(y, z) - lift`` -- and
    not a fixed plane through the cap: holding ``x`` at the extracted outline's
    own value solves to a point up to 2 mm off the part, which slants the
    station.  Two unknowns, two screen components, and the surface is smooth,
    so Newton with a differenced Jacobian lands on it from any nearby start.
    """
    target = np.asarray(target, float)

    def screen(y, z):
        return np.array(view.project(
            mouse3d.Point3D(mouse3d.side_surface_x(y, z) - lift, y, z)), float)

    y, z = start
    h = 1e-4
    for _ in range(iters):
        base = screen(y, z)
        jac = np.stack([(screen(y + h, z) - base) / h,
                        (screen(y, z + h) - base) / h], 1)
        try:
            step = np.linalg.solve(jac, target - base)
        except np.linalg.LinAlgError:
            break
        y += float(step[0])
        z += float(step[1])
        if float(np.hypot(step[0], step[1])) < 1e-11:
            break
    return y * mouse3d._MM, z * mouse3d._MM


def stroke_stations(view, rows, poly, name):
    """Each row of an end stroke, read back onto its paddle: ``(y, z)`` in mm.

    Rows that landed off the drawn height are dropped here rather than by the
    caller: the pen overshoots the paddle at both ends, and a station from past
    the end is the table's, not the part's.  The start for each solve is the
    nearest drawn vertex, which is *cut* when this is re-run over its own
    output -- but only as a start: the answer is where the surface sits under
    the stroke, so it does not chase itself.
    """
    zlo = min(p.z for p in poly) * mouse3d._MM
    zhi = max(p.z for p in poly) * mouse3d._MM
    screen = [view.project(p) for p in poly]
    stations = []
    for x, y in rows:
        i = int(np.argmin(np.hypot(*(np.array(screen) - (x, y)).T)))
        mm = on_flank(view, (x, y), start=(poly[i].y, poly[i].z))
        if zlo + CUT_MARGIN_MM < mm[1] < zhi - CUT_MARGIN_MM:
            stations.append(mm[0])
    return stations


def tail_cut(view, rows, poly, name):
    """The station the bracket cuts its paddle back to, in mm.

    The pen starts on the paddle's top edge, runs along it for a few rows
    before turning down -- one run far wider than the nib -- and carries on
    past the bottom edge, so those rows are dropped and what is left is the
    crossing itself.  Each surviving row is read back onto the flank and the
    station is the median of them: that shrugs off the hand's slant and the bow
    it drew into the line alike.
    """
    stations = stroke_stations(view, rows, poly, name)
    if not stations:
        raise SystemExit("the bracket in %s does not cross its paddle" % name)
    return float(np.median(stations)), len(stations)


def lead_cut(view, rows, poly, name):
    """The station the arc carries its paddle's leading end back to, in mm.

    The bracket crosses its paddle, so what it names is an end *face*, and the
    median of the rows that crossed is the station.  The arc does not cross: it
    is drawn *across* the paddle where its end should be, bowed forward, so the
    station is the line it reaches to -- its most forward point.  Only the rows
    within :data:`APEX_BAND_MM` of that extreme are read, and the median of them
    taken, so one row where the pen doubled back cannot carry the cut.
    """
    stations = stroke_stations(view, rows, poly, name)
    if not stations:
        raise SystemExit("the arc in %s does not reach its paddle" % name)
    front = min(stations)
    apex = [s for s in stations if s < front + APEX_BAND_MM]
    return float(np.median(apex)), len(stations)


def segments(field, stride=STRIDE, floor=5e-5):
    """Split a displacement field into ``[(start_index, [knots]), ...]``.

    Each segment covers one run of the outline the correction touches,
    padded by a stride at either end so the table begins and ends where the
    displacement has already decayed to nothing -- which keeps the
    interpolation flat outside the segment, and lets a run that crosses the
    wrap-around join be emitted as two ordinary tables.

    Below *floor* the fit has nothing left to say: the displacement is some
    fifty times narrower than the pen that drew the line it came from.  Such a
    stretch is read as nothing and emits zeros, so the padding knots land on a
    flat tail rather than on whatever ripple the smoothing leaves behind a
    decay.  Only a dip *narrower than the knot spacing* is kept as part of the
    run it interrupts: the interpolation steps straight across something that
    short, so it is not a break in the run, and zeroing it would punch a hole
    in the correction instead of trimming a tail.
    """
    live = np.hypot(field[:, 0], field[:, 1]) > floor
    n = len(live)
    if not live.any():
        return []
    pivot = next((k for k in range(n) if not live[k]), None)
    if pivot is None:                      # the whole loop moves
        pivot = 0
    order = [(pivot + k) % n for k in range(n + 1)]
    keep = live.copy()
    k = 1                                  # order[0] is dead, and a gap that
    while k < n:                           # long is the tail, not a dip
        if live[order[k]]:
            k += 1
            continue
        j = k
        while j < n and not live[order[j]]:
            j += 1
        if j < n and j - k < stride:
            for t in range(k, j):
                keep[order[t]] = True
        k = j
    field = np.where(keep[:, None], field, 0.0)

    out = []
    k = 0
    while k < n:
        if not keep[order[k]]:
            k += 1
            continue
        j = k
        while j < n and keep[order[j]]:
            j += 1
        first = order[max(0, k - 1)] if k else order[n - 1]
        last = order[min(n, j + 1)]
        span = (last - first) % n or n
        knots = []
        for t in range(0, span // stride + 3):
            du, dv = field[(first + t * stride) % n]
            knots.append((round(float(du), 6), round(float(dv), 6)))
        out.append((first, knots))
        k = j
    return out


def seam_field(view, page, line, y_start, y_end):
    """Model-plane displacements that pull *page*'s outline onto *line*.

    *view* is the annotation's own, so an outlined vertex and the stroke under
    it are both in that image's pixels.  *page* is the outline the stroke was
    drawn over -- the seated cap for the first stroke, and the seated cap
    displaced by the strokes before it for the later ones -- so the field is
    always measured against the curve the user was actually looking at.
    """
    n = len(page)
    screen_all = np.array([view.project(p) for p in page], float)
    raw = np.zeros((n, 2))
    for i, point in enumerate(page):
        screen = screen_all[i]
        dist, target, tangent, at_end = nearest_on(line, screen)
        if dist > NEAR or at_end:
            continue
        # The stroke describes this edge only while the two run *along* each
        # other.  Where the outline turns away from the stroke the correction
        # has to fade out, however far the stroke still is: otherwise a vertex
        # at the end of the corrected stretch -- with the stroke 40 px off and
        # diverging -- is yanked 40 px sideways.
        edge = screen_all[(i + 1) % n] - screen_all[(i - 1) % n]
        norm = float(np.hypot(*edge) * np.hypot(*tangent))
        along = abs(float(edge @ tangent)) / norm if norm else 0.0
        weight = along ** 4 * ease(screen[1], y_start, y_end)
        if weight <= 1e-3:
            continue
        jac = seated_jacobian(view, point)
        try:
            raw[i] = np.linalg.solve(jac, target - screen) * weight
        except np.linalg.LinAlgError:
            pass
    return raw


def displace(poly, field):
    """*poly* moved by *field*, the way the page seats a displaced outline."""
    return [mouse3d.Point3D(*mouse3d._snap_to_shell(p.x + dx, p.y + dy, 0.006))
            for p, (dx, dy) in zip(poly, field)]


def fit():
    strokes = {name: stroke_centre_lines(name) for name in ANNOTATIONS}
    for name, comp, _key, _turn in SEAM_STROKES:
        if len(strokes[name]) < 2:
            raise SystemExit("expected two red strokes in %s" % name)

    estimated = {(name, comp): seam_estimate(strokes[name][comp], max_turn=turn)
                 for name, comp, _key, turn in SEAM_STROKES}

    tables, report = {}, {}
    for key in dict.fromkeys(spec[2] for spec in SEAM_STROKES):
        page = mouse3d._cap_on_surface(MESH[key])
        raw = np.zeros((len(page), 2))
        for name, comp, owner, _turn in SEAM_STROKES:
            if owner != key:
                continue
            line, y_start, y_end = estimated[(name, comp)]
            step = seam_field(ANNOTATIONS[name]["view"], page, line, y_start, y_end)
            raw += step
            # the next stroke was drawn over the page *this* one leaves, so it
            # has to be fitted against that curve rather than the mesh again
            page = displace(mouse3d._cap_on_surface(MESH[key]),
                            cyclic_smooth(raw, SMOOTH))
        field = cyclic_smooth(raw, SMOOTH)
        report[key] = (raw, field)
        tables[key] = segments(field)

    cuts = {}
    for name, comp, key, end, runs in CUT_STROKES:
        reader = tail_cut if end == "tail" else lead_cut
        cuts[end, key] = reader(ANNOTATIONS[name]["view"],
                                bracket_rows(name, comp, runs),
                                mouse3d._cap_on_flank(key), name) + (name,)
    return tables, report, cuts


def render_block(tables):
    out = []
    for key in ("button1", "button8", "button7", "button4"):
        # order fixed so a regenerated table is stable; keys absent from
        # SEAM_STROKES simply drop out
        entry = tables.get(key)
        if not entry:
            continue
        out.append("    #: %s: offset in %s, one knot per %d outline vertices"
                   % (key, ", ".join(PLANE[key]), STRIDE))
        out.append("    %r: (" % key)
        for start, knots in entry:
            # the run's first outline vertex is data, not a comment: it is
            # what _seam_shift indexes the knots against
            out.append("        # knot table, first entry starts at vertex %d" % start)
            out.append("        (%d, [" % start)
            for k in range(0, len(knots), 4):
                out.append("            " + " ".join("%r," % (v,) for v in knots[k:k + 4]))
            out.append("        ]),")
        out.append("    ),")
    return "\n".join(out)


def render_stations(stations):
    """One ``key: mm,`` line per station, in the order given."""
    return "".join("    %r: %s,\n" % (key, round(mm, 1)) for key, mm in stations)


def lead_stations(cuts):
    """The ``_LEAD_CUT_MM`` table: the arc's station, plus the ones the module
    already carries that no stroke names.

    Only button 5's end is drawn in an annotation.  The flick lever's halves
    were measured against the click panel instead -- carried back to the first
    station where neither half crosses it -- so their stations come from the
    module and are neither derived nor clobbered here.
    """
    stations = dict(mouse3d._LEAD_CUT_MM)
    for (end, key), (mm, _read, _name) in cuts.items():
        if end == "lead":
            stations[key] = mm
    return list(stations.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="rewrite the _SEAM_FIX, _TAIL_CUT_MM and "
                         "_LEAD_CUT_MM tables in mouse3d.py")
    args = ap.parse_args()

    tables, report, cuts = fit()

    for key, (raw, field) in report.items():
        n = len(raw)
        read = int((np.hypot(raw[:, 0], raw[:, 1]) > 0).sum())
        runs = len(segments(field))
        peak = float(np.hypot(field[:, 0], field[:, 1]).max()) * mouse3d._MM
        print("%-9s %3d/%3d vertices read a stroke, %d run(s), peak %.2f mm"
              % (key, read, n, runs, peak))
    for (end, key), (mm, read, name) in sorted(cuts.items()):
        print("%-9s %s cut to %.2f mm (from %d stations on %s's stroke)"
              % (key, end, mm, read, name))

    seam = render_block(tables)
    tail = render_stations([(key, mm) for (end, key), (mm, _r, _n) in cuts.items()
                            if end == "tail"])
    lead = render_stations(lead_stations(cuts))
    if not args.write:
        print("\n--- _SEAM_FIX ---\n%s" % seam)
        print("\n--- _TAIL_CUT_MM ---\n%s" % tail)
        print("\n--- _LEAD_CUT_MM ---\n%s" % lead)
        return

    path = os.path.join(ROOT, "rivalcfg_gui", "mouse3d.py")
    with open(path) as fh:
        source = fh.read()
    for name, block in (("_SEAM_FIX", seam), ("_TAIL_CUT_MM", tail),
                        ("_LEAD_CUT_MM", lead)):
        pattern = re.compile(r"(%s = \{\n)(.*?)(^\}\n)" % name, re.S | re.M)
        if not pattern.search(source):
            raise SystemExit("no %s = { ... } block found in mouse3d.py" % name)
        source = pattern.sub(
            lambda m: m.group(1) + block.rstrip("\n") + "\n" + m.group(3),
            source)
    with open(path, "w") as fh:
        fh.write(source)
    print("\nrewrote _SEAM_FIX, _TAIL_CUT_MM and _LEAD_CUT_MM in %s" % path)


if __name__ == "__main__":
    main()
