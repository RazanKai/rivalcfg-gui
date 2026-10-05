"""3D wireframe model of the SteelSeries Aerox 5.

The geometry is no longer procedural: it comes from :mod:`aerox5_mesh`,
a compact dataset extracted from a detailed, dimensioned OBJ model of the
mouse (see ``tools/extract_aerox5_mesh.py``):

* ``RINGS`` — cross-section slices of the shell (the loft wireframe);
* ``RIMS`` — boundary loops of the *real* honeycomb holes and cut-outs;
* ``CAPS`` — outlines of the buttons that exist as separate mesh groups
  (side buttons 4/5, the forward trigger bar and the DPI button);
* ``WHEEL`` — the wheel cylinder (centre, radius, half-width);
* ``GRID_TOP`` / ``GRID_SIDE`` — bilinear lookup grids for the top and
  left-flank surfaces, used to snap the remaining (parametric) buttons
  onto the real surface: click panels, scroll zones and the 7/8 rocker.

Everything is GTK/Cairo-free here (pure trigonometry) so it can be
unit-tested and rendered by any backend; :func:`render` only needs a
cairo-like context.

Coordinates (normalised so the length is 1.0):
    x : right (+) / left (-)      width
    y : back (+) / front (-)      length
    z : up (+) / down (-)         height

The real Aerox 5 is ~128.2 mm long, ~68.4 mm wide, ~42.0 mm tall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import aerox5_mesh as _mesh


# ---------------------------------------------------------------------------
# Shell definition (from the extracted rings)
# ---------------------------------------------------------------------------

#: (y, half_width, z_bottom, z_top) per ring, front -> back.
_STATIONS = []
for _y, _ring in _mesh.RINGS:
    _xs = [p[0] for p in _ring]
    _zs = [p[1] for p in _ring]
    _STATIONS.append((_y, max(abs(v) for v in _xs), min(_zs), max(_zs)))

#: Number of points around each ring (wireframe resolution).
_RING_STEPS = len(_mesh.RINGS[0][1])

#: Mid-height of the body.  A hole rim's normal only tells us which way the
#: loop happens to be wound, so it is flipped to point away from this axis.
_Z_AXIS = 0.5 * (min(_s[2] for _s in _STATIONS)
                 + max(_s[3] for _s in _STATIONS))


def _lerp_lookup(xs, ys, v):
    """Piecewise linear interpolation of y(v) over monotonic *xs*."""
    if v <= xs[0]:
        return ys[0]
    if v >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= v <= xs[i + 1]:
            f = (v - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + (ys[i + 1] - ys[i]) * f
    return ys[-1]


def _interp_station(y):
    """Interpolate (half_width, z_bottom, z_top) at length position *y*."""
    if y <= _STATIONS[0][0]:
        return _STATIONS[0][1:]
    if y >= _STATIONS[-1][0]:
        return _STATIONS[-1][1:]
    for i in range(len(_STATIONS) - 1):
        y0, a0, b0, t0 = _STATIONS[i]
        y1, a1, b1, t1 = _STATIONS[i + 1]
        if y0 <= y <= y1:
            f = (y - y0) / (y1 - y0)
            return (a0 + (a1 - a0) * f, b0 + (b1 - b0) * f, t0 + (t1 - t0) * f)
    return _STATIONS[-1][1:]


def _bilinear(xs, ys, grid, x, y):
    """Bilinear interpolation of grid[j][i] = f(xs[i], ys[j]), clamped."""
    fx = min(max(x, xs[0]), xs[-1])
    fy = min(max(y, ys[0]), ys[-1])
    i = j = 0
    while i < len(xs) - 2 and xs[i + 1] < fx:
        i += 1
    while j < len(ys) - 2 and ys[j + 1] < fy:
        j += 1
    tx = 0.0 if xs[i + 1] == xs[i] else (fx - xs[i]) / (xs[i + 1] - xs[i])
    ty = 0.0 if ys[j + 1] == ys[j] else (fy - ys[j]) / (ys[j + 1] - ys[j])
    g00, g01 = grid[j][i], grid[j][i + 1]
    g10, g11 = grid[j + 1][i], grid[j + 1][i + 1]
    return (g00 * (1 - tx) * (1 - ty) + g01 * tx * (1 - ty)
            + g10 * (1 - tx) * ty + g11 * tx * ty)


# Cell-centre axes of the lookup grids (cells lie between the edge values).
_TOP_XS = [(_mesh.GRID_X[i] + _mesh.GRID_X[i + 1]) / 2
           for i in range(len(_mesh.GRID_TOP[0]))]
_TOP_YS = [(_mesh.GRID_Y[j] + _mesh.GRID_Y[j + 1]) / 2
           for j in range(len(_mesh.GRID_TOP))]
_SIDE_YS = [(_mesh.GRID_Y[j] + _mesh.GRID_Y[j + 1]) / 2
            for j in range(len(_mesh.GRID_SIDE[0]))]
_SIDE_ZS = [(_mesh.GRID_Z[j] + _mesh.GRID_Z[j + 1]) / 2
            for j in range(len(_mesh.GRID_SIDE))]


def top_surface_z(x, y):
    """Height of the real top surface at (x, y)."""
    return _bilinear(_TOP_XS, _TOP_YS, _mesh.GRID_TOP, x, y)


def side_surface_x(y, z):
    """Left-flank surface x at (y, z) (a negative value)."""
    return _bilinear(_SIDE_YS, _SIDE_ZS, _mesh.GRID_SIDE, y, z)


# ---------------------------------------------------------------------------
# Wireframe
# ---------------------------------------------------------------------------

@dataclass
class Point3D:
    x: float
    y: float
    z: float


#: Draw one longitudinal every N ring points.  A line per ring point is a
#: featureless grid that reads as a capsule; the sparser loft reads as a
#: surface.
_LOFT_EVERY = 3


def build_wireframe():
    """Return ``(rings, longitudinals)`` as lists of 3D polylines.

    * ``rings``: cross-section outlines (one per extracted slice).
    * ``longitudinals``: lines running front-to-back every ``_LOFT_EVERY``
      ring points.
    """
    rings = [[Point3D(x, y, z) for x, z in ring] for y, ring in _mesh.RINGS]
    longitudinals = []
    for k in range(0, len(rings[0]), _LOFT_EVERY):
        longitudinals.append([rings[i][k] for i in range(len(rings))])
    return rings, longitudinals


# ---------------------------------------------------------------------------
# Buttons (real cap outlines from the mesh)
# ---------------------------------------------------------------------------

def _smooth_closed(points, per_seg=6):
    """Densify a closed loop of :class:`Point3D` (Catmull-Rom).

    The extracted outlines have a point every ~4 mm, so their corners are
    chords: stroked straight they read as flat plates bolted to the shell.

    The result is clamped to the source outline's bounding box.  Catmull-Rom
    overshoots at a sharp corner, and a side button was bulging ~0.9 mm up
    the flank past the top of its own outline -- enough to read as a cap
    climbing the crown.
    """
    lo = {c: min(getattr(p, c) for p in points) for c in ("x", "y", "z")}
    hi = {c: max(getattr(p, c) for p in points) for c in ("x", "y", "z")}
    out = []
    n = len(points)
    for i in range(n):
        p0, p1 = points[i - 1], points[i]
        p2, p3 = points[(i + 1) % n], points[(i + 2) % n]
        for k in range(per_seg):
            t = k / per_seg
            t2, t3 = t * t, t * t * t
            out.append(Point3D(*(
                min(hi[c], max(lo[c],
                    0.5 * (2 * getattr(p1, c) + (-getattr(p0, c) + getattr(p2, c)) * t
                           + (2 * getattr(p0, c) - 5 * getattr(p1, c)
                              + 4 * getattr(p2, c) - getattr(p3, c)) * t2
                           + (-getattr(p0, c) + 3 * getattr(p1, c)
                              - 3 * getattr(p2, c) + getattr(p3, c)) * t3)))
                for c in ("x", "y", "z"))))
    return out


def _snap_to_shell(x, y, lift=0.006):
    """Seat a normalised outline point on the shell's surface height.

    The extracted cap outlines are planar -- they come off the plane the
    source OBJ built them on -- while the real crown rises and falls beneath
    them, so drawn as-is the click panels read as plates bolted over the
    shell.  This gives each point the surface height at its own ``(x, y)``,
    so the outline bends with the shell, over the crown and down the flank.

    ``x`` and ``y`` are passed through untouched: the outline's plan-view
    geometry is the extracted boundary, and the fork the keycaps leave
    around the scroll wheel is part of it.

    The height comes from :func:`top_surface_z`.  It used to invert the
    per-station arc-length profiles instead, walking the cross-section to
    the sample whose ``|x|`` matched.  That inversion is ill-conditioned
    exactly where the keycap wraps the shoulder: near the widest line the
    surface runs almost vertical, so a fraction of a millimetre of ``|x|``
    spans several millimetres of ``z``, and the "nearest sample" lookup
    hopped between brackets.
    """
    return x, y, top_surface_z(x, y) + lift


#: Displacements read off the user's marked-up screenshots in
#: ``aerox5_3d_files``.
#:
#: Both images are screenshots of this page with red strokes drawn over them by
#: hand.  In ``error_shape.png`` the long stroke traces the seam the left click
#: panel leaves for the two top paddles -- the shape that edge should have --
#: and in ``error_shape2.png``, drawn later over the corner that first fit
#: leaves, an arc corrects it again.
#: :mod:`tools.fit_seam_correction` registers each screenshot against a
#: headless render of the same view, reads the strokes, and records where the
#: drawn edges sit relative to the ones we draw.  The entries below, in the
#: plane each outline is built in (``x, y`` for the top panels, ``y, z`` for
#: the flank buttons), are runs of ``(first_vertex, knots)``: the knots are
#: offsets, one per :data:`_SEAM_STRIDE` outline vertices starting at
#: ``first_vertex`` and wrapping around the closed loop, in model units
#: (``_MM`` mm each).
#:
#: The second stroke is fitted against the page the first one draws, not
#: against the mesh's own outline: it was drawn over the correction, so its
#: displacements are measured from the corrected edge and the two fields are
#: summed.  The shorter strokes that name an *end* rather than an edge -- the
#: bracket across button 4, the arc across button 5 -- are not here at all;
#: they land in :data:`_TAIL_CUT_MM` and :data:`_LEAD_CUT_MM`.  Regenerate all
#: three with ``python3 tools/fit_seam_correction.py --write``.
_SEAM_STRIDE = 6
_SEAM_FIX = {
    #: button1: offset in x, y, one knot per 6 outline vertices
    'button1': (
        # knot table, first entry starts at vertex 345
        (345, [
            (0.0, 0.0), (-7.1e-05, 0.000208), (-8.3e-05, 0.000251), (-8.3e-05, 0.000251),
            (-8.3e-05, 0.000251), (-0.000125, 0.00041), (-0.000214, 0.000798), (-0.000332, 0.00125),
            (-0.000533, 0.001971), (-0.000802, 0.002884), (-0.001147, 0.003989), (-0.001484, 0.00489),
            (-0.00191, 0.005949), (-0.002376, 0.007049), (-0.002913, 0.008217), (-0.003486, 0.00938),
            (-0.004094, 0.01056), (-0.00462, 0.011485), (-0.005263, 0.012586), (-0.006, 0.013816),
            (-0.006774, 0.015138), (-0.007504, 0.016294), (-0.008227, 0.017389), (-0.008931, 0.018466),
            (-0.009562, 0.019373), (-0.010169, 0.020184), (-0.010828, 0.021034), (-0.011669, 0.022257),
            (-0.012586, 0.023648), (-0.013392, 0.024868), (-0.014106, 0.025853), (-0.014719, 0.026645),
            (-0.015135, 0.027136), (-0.015465, 0.027475), (-0.015796, 0.027724), (-0.016133, 0.028016),
            (-0.01648, 0.028372), (-0.016943, 0.028853), (-0.017448, 0.029319), (-0.017935, 0.029759),
            (-0.018231, 0.030013), (-0.018388, 0.030208), (-0.018237, 0.030151), (-0.017753, 0.02986),
            (-0.017166, 0.029546), (-0.016447, 0.029173), (-0.015143, 0.028087), (-0.013731, 0.026628),
            (-0.011978, 0.024352), (-0.009726, 0.020792), (-0.006713, 0.015426), (-0.004336, 0.01061),
            (-0.002558, 0.006498), (-0.001084, 0.003025), (-0.000102, 0.000523), (-2e-06, -7e-06),
            (-8.8e-05, 3.5e-05), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0),
        ]),
    ),
}


def _seam_shift(key, points, axes):
    """Move an outline onto the edges drawn in ``error_shape.png``.

    *points* are in the plane the cap is built in and *axes* names its two
    components, so the outline keeps its seating: the top panels move in
    ``(x, y)`` and are re-seated on the crown afterwards, the flank buttons
    move in ``(y, z)`` and are re-seated on the flank.  Vertices outside the
    fitted runs come back untouched.
    """
    runs = _SEAM_FIX.get(key)
    if not runs:
        return points
    count = len(points)
    shift = [None] * count
    for first, knots in runs:
        for k in range((len(knots) - 1) * _SEAM_STRIDE):
            j, t = divmod(k, _SEAM_STRIDE)
            a, b = knots[j], knots[j + 1]
            shift[(first + k) % count] = (
                a[0] + (b[0] - a[0]) * t / _SEAM_STRIDE,
                a[1] + (b[1] - a[1]) * t / _SEAM_STRIDE)
    out = []
    for i, p in enumerate(points):
        d = shift[i]
        if d is None:
            out.append(p)
            continue
        values = {"x": p.x, "y": p.y, "z": p.z}
        values[axes[0]] += d[0]
        values[axes[1]] += d[1]
        out.append(Point3D(values["x"], values["y"], values["z"]))
    return out


def _cap_on_surface(key, lift=0.006, fix=None):
    """Re-lift a top-button cap outline onto the curved top surface.

    Both click panels are traced from the *left* panel's part line in
    :mod:`tools.extract_aerox5_v3_mesh` -- the model breaks its flank
    asymmetrically, so per-panel tracing draws the right cap 4.2 mm wider --
    which leaves this side of the job as the seating: every point is put on
    the surface height for its own ``(x, y)``, so the panel bends with the
    crown instead of lying over it as a plate.

    *fix* names the :data:`_SEAM_FIX` run to apply to the plan outline before
    it is seated, which is where the panel's drawn edge is corrected.
    """
    source = getattr(_mesh, "CAP_OUTLINES", {}).get(key) or _mesh.CAPS[key]
    loop = _smooth_closed([Point3D(*q) for q in source])
    if fix:
        loop = _seam_shift(fix, loop, ("x", "y"))
    return [Point3D(*_snap_to_shell(p.x, p.y, lift)) for p in loop]


def _mirror_panel(points, lift=0.006):
    """The right click panel: a corrected left panel reflected and re-seated.

    The two keycaps are one mirrored pair -- the mesh traces both from the
    left panel's part line -- so a correction drawn on the left edge belongs
    on both.  Reflecting the *plan* and seating it again puts the right panel
    on its own side's crown height, which is where the pair legitimately
    differs (``z`` by up to 4.8 mm).
    """
    return [Point3D(*_snap_to_shell(-p.x, p.y, lift)) for p in points]


#: Corner radius the *model* gives each side button, in mm, taken from the
#: ``side_button`` outlines in ``aerox5_3d_files/model.py``.
#:
#: The patch the mesh carries is not that outline: ``side_button`` keeps a
#: cell when *any* of its four corners is inside the polygon, so the part
#: line it emits is the outline dilated by one 0.45 mm grid cell, and the
#: extractor then decimates the resulting staircase to 64 points.  Both
#: rounded ends lose their arc that way and come back as flat faces with
#: two shallow chamfers: the paddles read as cut square across their
#: tails, which is the end the error_shape annotation brackets.  The
#: drawn bbox still holds the model's corners, so :func:`_round_ends`
#: rebuilds the rounded rectangle inside it at this radius.
_SIDE_FILLET_MM = {
    "button4": 2.4,     # side_button_2, rrect(75.3, 25.4, 23.5, 4.8, 2.4)
    "button5": 2.4,     # side_button_1, rrect(51.8, 25.4, 21.5, 5.0, 2.4)
    "button9": 2.6,     # side_button_silver, round_poly(..., 2.6)
}


#: Where a flank button's tail now ends, in mm along the model's ``y`` axis
#: (``_MM`` normalised units each).
#:
#: The same annotation that corrects the left keycap's seam carries a short
#: stroke across button 4: a line entering along its top edge, crossing to its
#: bottom edge, and drawn -- with the hooks that meet the two edges and the
#: bulge between them -- as the end profile itself, roughly 7 mm before the
#: drawn tip.  Each of its rows is read back into the model's own frame onto
#: the surface the pen was over (see ``tools/fit_seam_correction.py``); the
#: hand's slant aside they all land at ``y`` ~16 mm, and the median is
#: 16.07 mm, so the paddle is cut about 7.1 mm shorter than the mesh draws it.
#: The profile it is cut off at is the one it already draws there -- a flat
#: face with the corners rolled into the long edges -- rather than a new one
#: invented here.  Only the button the line was drawn across is cut.
_TAIL_CUT_MM = {
    'button4': 16.1,
}


#: Where a flank button's *leading* end now begins, in mm on the model's ``y``
#: axis -- the same axis :data:`_TAIL_CUT_MM` names a station on, so the
#: stations are negative here and positive there.
#:
#: Both rocker halves and both thumb buttons are drawn from the same leading
#: station (``y`` ~-23 mm) because the mesh's lever and its two thumb buttons
#: all start at ``BTN_Y0 + 50``.  Button 5's end is the one the second
#: annotation draws: a bowed arc across the paddle, with an X on the material
#: forward of it, which lands at -20.34 mm -- a 2.71 mm cut.  Button 4, the
#: other thumb button, keeps the end the mesh drew.
#:
#: The rocker is cut far harder, and not from an annotation at all.  It sits
#: nearly 7 mm higher up the flank than the thumb buttons do, and the panel's
#: rear edge -- the seam the annotations draw -- cuts across the same screen
#: ``x`` a good 6 px *inside* where the rocker's leading corner lands.
#: Nothing occludes it on the way (the renderer draws every outline, back
#: faces included), so the rocker's leading end is drawn over the keycap as a
#: sliver aimed up and to the left: it reads as a paddle that is too long and
#: that trends up toward the nose.
#:
#: So the rocker is carried back to the first station where neither half's
#: outline crosses the seam line at all, which is -19 mm -- a little over 4 mm
#: off a 39 mm paddle.  Measured on the seated outlines in the page view, both
#: halves keep a 6 px gap to the panel there, and the rocker's corner lands
#: behind button 5's, so the three ends still read as a staircase.
_LEAD_CUT_MM = {
    'button7': -19.0,
    'button8': -19.0,
    'button5': -20.3,
}


def _resample_closed(loop, count):
    """Resample a closed polyline to *count* points, even in arc length."""
    n = len(loop)
    cum = [0.0]
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    if total <= 0.0:
        return list(loop)
    out = []
    j = 0
    for k in range(count):
        d = total * k / count
        while j < n - 1 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        t = (d - cum[j]) / seg if seg > 0.0 else 0.0
        a, b = loop[j], loop[(j + 1) % n]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _round_ends(loop, radius):
    """Rebuild a rounded-rectangle outline with corner radius *radius*.

    These three side buttons *are* rounded rectangles in the model -- see
    ``_SIDE_FILLET_MM`` -- but the rounded end an outline is cut off at the
    mask's rows, so it lands on the mesh as a flat face one grid row in from
    the tip.  The drawn flat edges are right and the drawn bbox is where the
    model puts the part, so the rectangle is rebuilt from the bbox and the
    corners are cut to the model's radius again.

    A loop that is *not* that rectangle -- nothing else in the mesh is --
    comes back untouched: any drawn point further out than *outside* marks
    the shape as something else, and the drawn part line then wins.

    *loop* is a list of ``(y, z)``; the return is in the same frame.
    """
    y0 = min(p[0] for p in loop)
    y1 = max(p[0] for p in loop)
    z0 = min(p[1] for p in loop)
    z1 = max(p[1] for p in loop)
    # Two rounded corners share the short edges; at r = half the height they
    # meet, which is the semicircular end the paddles have.
    r = min(radius, 0.49 * min(y1 - y0, z1 - z0))
    if r <= 1e-9:
        return list(loop)

    out = []
    quads = ((y1 - r, z1 - r, 0.0), (y0 + r, z1 - r, math.pi / 2),
             (y0 + r, z0 + r, math.pi), (y1 - r, z0 + r, 3 * math.pi / 2))
    steps = max(2, len(loop) // 8)
    for cy, cz, a0 in quads:
        for k in range(steps):
            ang = a0 + math.pi / 2 * k / steps
            out.append((cy + r * math.cos(ang), cz + r * math.sin(ang)))
    for py, pz in loop:
        qy = abs(py - 0.5 * (y0 + y1)) - (0.5 * (y1 - y0) - r)
        qz = abs(pz - 0.5 * (z0 + z1)) - (0.5 * (z1 - z0) - r)
        if min(max(qy, qz), 0.0) + math.hypot(max(qy, 0.0), max(qz, 0.0)) > r + 0.5:
            return list(loop)
    return _resample_closed(out, len(loop))


def _cut_tail(points, cut_mm, tol_mm=0.15):
    """Carry a flank outline's end profile back to ``cut_mm`` on model ``y``.

    *points* is the closed outline in the planar ``(y, z)`` frame the
    extracted caps are stored in.  ``y`` runs along the paddle and the two
    long edges sit at a constant ``z`` apiece, so the end is the one run of
    the loop that has left both of them: the two corners and the face
    between.  That run is moved back rigidly -- the paddle keeps the end it
    draws today, at the station the drawn line marks -- and the long edges are
    shortened to meet it.

    Nothing is interpolated: every point that comes back is a point that went
    in, moved or dropped.  That keeps the seating's ``x`` fallback meaningful,
    which an evenly-resampled loop would not (a synthetic point has no
    extracted ``x`` to fall back to).  A loop the plane does not cross exactly
    twice -- including one that already ends at or in front of ``cut_mm`` --
    comes back untouched.
    """
    cut = cut_mm / _MM
    tol = tol_mm / _MM
    tail = max(p.y for p in points)
    if cut >= tail - 1e-9:
        return list(points)
    zlo = min(p.z for p in points)
    zhi = max(p.z for p in points)
    count = len(points)
    hits = [i for i in range(count)
            if (points[i].y - cut) * (points[(i + 1) % count].y - cut) < 0.0]
    if len(hits) != 2:
        return list(points)

    def forward(i, j):
        out, k = [], (i + 1) % count
        while k != (j + 1) % count:
            out.append(points[k])
            k = (k + 1) % count
        return out

    sides = [forward(hits[0], hits[1]), forward(hits[1], hits[0])]
    if max(p.y for p in sides[0]) < max(p.y for p in sides[1]):
        sides.reverse()
    tail_side, front = sides

    def on_edge(p):
        return abs(p.z - zlo) < tol or abs(p.z - zhi) < tol

    live = [k for k, p in enumerate(tail_side) if not on_edge(p)]
    if not live:
        return list(points)
    moved = [Point3D(p.x, p.y + cut - tail, p.z)
             for p in tail_side[live[0]:live[-1] + 1]]

    # The front run is walked from the bottom edge to the top one, so its
    # first point belongs to the edge the moved profile now ends on and its
    # last to the edge the profile now begins on.
    while front and front[0].y > moved[-1].y:
        front.pop(0)
    while front and front[-1].y > moved[0].y:
        front.pop()
    if not front:
        return list(points)
    return front + moved


def _cut_lead(points, cut_mm, tol_mm=0.15):
    """The leading end's counterpart of :func:`_cut_tail`.

    ``_cut_tail`` works on a loop's *far* end, which it finds as the run past
    both long edges near the loop's largest ``y``.  A leading end is the same
    run at the loop's smallest ``y`` and nothing else about the shape changes
    when the axis is turned round, so the loop is handed to it mirrored in
    ``y`` -- station and all -- and mirrored back.  No second copy of the
    geometry is kept in step that way, and the two ends cannot drift apart.
    """
    mirrored = [Point3D(p.x, -p.y, p.z) for p in points]
    out = _cut_tail(mirrored, -cut_mm, tol_mm)
    return [Point3D(p.x, -p.y, p.z) for p in out]


def _cap_on_flank(key, lift=0.004):
    """Re-lift a side-button cap outline onto the curved left flank.

    The extracted caps are stored as *planar* outlines at a constant ``x``
    (the plane the source OBJ built them on), but the real flank swells
    outward underneath them, so drawn as-is they read as flat slabs slicing
    through the shell.  Every point is re-seated at the flank surface for its
    own ``(y, z)`` -- the mirror of :func:`_cap_on_surface` for the top.

    A key named in :data:`_TAIL_CUT_MM` or :data:`_LEAD_CUT_MM` has that end
    carried back first, on the planar loop, so the seating then follows the
    shortened part.

    :func:`_round_ends` above is unused: it rebuilt the corner radius of an
    extracted per-panel loop, which is no longer what the caps are.
    """
    loop = _smooth_closed([Point3D(*p) for p in _mesh.CAPS[key]])
    cut = _TAIL_CUT_MM.get(key)
    if cut is not None:
        loop = _cut_tail(loop, cut)
    cut = _LEAD_CUT_MM.get(key)
    if cut is not None:
        loop = _cut_lead(loop, cut)
    pts = []
    for p in loop:
        fx = side_surface_x(p.y, p.z)
        if fx is None or fx >= 0.0:
            fx = p.x
        pts.append(Point3D(fx - lift, p.y, p.z))
    return pts


def _nearest_x(source, y, z):
    """The ``x`` of the *source* outline point closest to ``(y, z)``."""
    return min(source, key=lambda p: (p[1] - y) ** 2 + (p[2] - z) ** 2)[0]


def build_buttons():
    """Return ``{button_key: (label, [Point3D, ...])}``.

    Mapping matches the official rivalcfg Aerox 5 schema / the SteelSeries
    manual's side-button legend.  All physical outlines now come from the
    corrected detailed model:

    * buttons 1/2 (left/right click) are the real keycap outlines, lifted
      onto the curved top surface so they wrap down the low nose -- one
      mirrored pair, traced from the *left* panel's part line in the mesh;
    * button 6 (CPI) and 9 (forward trigger) are the physical cap outlines;
    * buttons 4/5 are the real thumb buttons;
    * buttons 7/8 are the two halves of the real up/down flick rocker;
    * button 3 is the scroll wheel itself (middle click), with two arrow
      regions for scroll up / scroll down instead of overlaying the wheel.
    """
    buttons = {}

    # Left / right click keycaps (real outlines, wrapped onto the top).  The
    # mirroring is in the mesh, not here: both panels are traced from the left
    # panel's part line by tools/extract_aerox5_v3_mesh.py.
    buttons["button1"] = ("Left click",
                          _cap_on_surface("main_button_left", fix="button1"))
    # Built from button 1 rather than the mesh's own right outline, so the
    # pair stays an exact mirror once the left one carries a correction.
    buttons["button2"] = ("Right click", _mirror_panel(buttons["button1"][1]))

    # Scroll wheel (button 3): the exposed band of the real wheel cylinder.
    cy, cz, r, hw = (_mesh.WHEEL["cy"], _mesh.WHEEL["cz"],
                     _mesh.WHEEL["r"], _mesh.WHEEL["hw"])
    rim_z = max(top_surface_z(-(hw + 0.018), cy),
                top_surface_z(hw + 0.018, cy))
    tmax = max(38.0, math.degrees(
        math.acos(min(1.0, max(-1.0, (rim_z - cz) / r)))))
    tf = tmax + 14.0
    # The middle-click region is the exposed *band* of the cylinder: forward
    # along the near face, back along the far one, so the loop closes into a
    # ribbon instead of a bow-tie.
    wheel_poly = []
    for i in range(15):
        t = -tf + 2 * tf * i / 14
        wheel_poly.append(Point3D(
            -hw, cy + r * math.sin(math.radians(t)),
            cz + r * math.cos(math.radians(t))))
    for i in range(15):
        t = tf - 2 * tf * i / 14
        wheel_poly.append(Point3D(
            hw, cy + r * math.sin(math.radians(t)),
            cz + r * math.cos(math.radians(t))))
    buttons["button3"] = ("Middle click", wheel_poly)

    # Middle click (button 3) is the wheel itself.  Scroll up / down are
    # wheel *directions*, not separate physical buttons, so they are offered
    # as assignments in the popover rather than drawn as overlapping facets.

    # Physical caps straight from the mesh (side buttons re-seated on the
    # flank so they wrap the shell instead of cutting through it).
    buttons["button6"] = ("DPI", _smooth_closed(
        [Point3D(*p) for p in _mesh.CAPS["button6"]]))
    buttons["button9"] = ("Button 9", _cap_on_flank("button9"))
    buttons["button4"] = ("Button 4", _cap_on_flank("button4"))
    buttons["button5"] = ("Button 5", _cap_on_flank("button5"))
    buttons["button7"] = ("Button 7 (\u2193)", _cap_on_flank("button8"))
    buttons["button8"] = ("Button 8 (\u2191)", _cap_on_flank("button7"))

    return buttons




#: Mouse length in mm; normalised coordinates are ``mm / _MM``.
_MM = _mesh.LENGTH_MM
#: Station of normalised y = 0.  The mesh normalises y as ``y/MM - 0.5`` with
#: its own origin at ``Y_MIN_MM``, so the half-length has to be shifted by that
#: offset -- dropping it silently slid every station by 0.3 mm.
_Y0_MM = _MM / 2.0 + _mesh.Y_MIN_MM


def _rim_normal(pts):
    """Outward normal of a closed hole loop, as ``(nx, 0, nz)``.

    Newell's area vector for the loop, projected onto XZ (the renderer's
    whole-cell facing test reads ``nx``/``nz`` only, so the Y component is
    dropped rather than carried to no effect).  The loop winding out of the
    extractor is arbitrary, so the sign is fixed by flipping it to point away
    from the body's vertical axis.
    """
    nx = nz = 0.0
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        nx += (p.y - q.y) * (p.z + q.z)
        nz += (p.x - q.x) * (p.y + q.y)
    cx = sum(p.x for p in pts) / len(pts)
    cz = sum(p.z for p in pts) / len(pts)
    if nx * cx + nz * (cz - _Z_AXIS) < 0.0:
        nx, nz = -nx, -nz
    d = math.hypot(nx, nz) or 1.0
    return nx / d, 0.0, nz / d


def build_honeycomb():
    """Honeycomb openings as loops on the shell (normalised coordinates).

    The loops are the real perforation rims extracted from the v3 mesh
    (``aerox5_mesh.RIMS``): the pattern, its pitch, its run from the rear of
    the keycaps to the tail and the way it wraps the shoulder all come from
    the model, not from a lattice generated here.  Each entry keeps the shape
    the renderer expects, ``{"pts", "center", "normal"}``.
    """
    loops = []
    for rim in _mesh.RIMS:
        pts = [Point3D(*p) for p in rim]
        if len(pts) < 3:
            continue
        n = len(pts)
        loops.append({
            "pts": pts,
            "center": Point3D(sum(p.x for p in pts) / n,
                              sum(p.y for p in pts) / n,
                              sum(p.z for p in pts) / n),
            "normal": _rim_normal(pts),
        })
    return loops


# ---------------------------------------------------------------------------
# Surface details: seams, wheel, real honeycomb rims
# ---------------------------------------------------------------------------

def _top_line(points_xy, lift=0.004):
    return [Point3D(x, y, top_surface_z(x, y) + lift) for x, y in points_xy]


def _wheel_arc(x, cy, cz, r, t0, t1, steps=14):
    return [Point3D(x,
                    cy + r * math.sin(math.radians(t0 + (t1 - t0) * k / steps)),
                    cz + r * math.cos(math.radians(t0 + (t1 - t0) * k / steps)))
            for k in range(steps + 1)]


def _build_wheel():
    """The scroll wheel as a proper cylinder wireframe.

    Two full end circles (the wheel faces) plus longitudinal tread ridges
    parallel to the axis.  The lower halves sit inside the shell, which is
    what seats the wheel in its slot instead of floating above it.
    """
    cy, cz, r, hw = (_mesh.WHEEL["cy"], _mesh.WHEEL["cz"],
                     _mesh.WHEEL["r"], _mesh.WHEEL["hw"])
    # Exposed arc: t where the wheel top clears the shell beside its slot.
    # The ends are tucked a little past the rim so they vanish into the shell
    # slot -- sweeping further (as before) drew the sunken half of the wheel
    # straight through the body as a big circle.
    rim = max(top_surface_z(-(hw + 0.016), cy),
              top_surface_z(hw + 0.016, cy))
    cos_t = min(1.0, max(-1.0, (rim - cz) / r))
    tmax = max(58.0, math.degrees(math.acos(cos_t)))
    tuck = 14.0
    tf = tmax + tuck
    near = _wheel_arc(-hw, cy, cz, r, -tf, tf, steps=26)
    far = _wheel_arc(hw, cy, cz, r, -tf, tf, steps=26)
    hub = _wheel_arc(-hw, cy, cz, 0.55 * r, -tf * 0.88, tf * 0.88,
                     steps=20)
    treads = []
    for f in (0.13, 0.30, 0.48, 0.66, 0.85, 1.0):
        t = -tf + 2 * tf * f
        tr = math.radians(t)
        dy, dz = r * math.sin(tr), r * math.cos(tr)
        treads.append([Point3D(-hw, cy + dy, cz + dz),
                       Point3D(hw, cy + dy, cz + dz)])
    return {"near": [near], "far": [far], "hubs": [hub], "treads": treads}


def build_details():
    """Surface decoration polylines (seams / real hole rims / wheel)."""
    seams = []

    # Rim where the top shell meets the bottom plate (real boundary loop;
    # the nose stretch curls around the USB-C opening and just adds noise,
    # so the loop is opened up there).
    if _mesh.BASE_SEAM:
        pts = [Point3D(*p) for p in _mesh.BASE_SEAM]
        pts = [p for p in pts if p.y > -0.43]
        if len(pts) > 8:
            seams.append(pts)
    # Centre groove between the click buttons, nose -> wheel slot.
    seams.append(_top_line([(0.0, -0.494), (0.0, -0.390)]))

    return {"seams": seams, "holes": build_honeycomb(),
            "wheel": _build_wheel()}




# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

@dataclass
class View:
    """Orthographic camera: yaw around the vertical axis, pitch tilt."""

    yaw: float = math.radians(-120.0)
    pitch: float = math.radians(42.0)
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    #: Screen-space rotation about the drawing origin, applied after the
    #: orthographic projection and before the offset.  0.0 skips the branch
    #: entirely, so every existing view is bit-for-bit unchanged.
    roll: float = 0.0

    def project(self, p: Point3D):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        x1 = p.x * cy - p.y * sy
        y1 = p.x * sy + p.y * cy
        z2 = -y1 * sp + p.z * cp
        u = x1 * self.scale
        v = -z2 * self.scale
        if self.roll:
            crl, srl = math.cos(self.roll), math.sin(self.roll)
            u, v = u * crl - v * srl, u * srl + v * crl
        return (self.offset_x + u, self.offset_y + v)

    def project_many(self, points):
        return [self.project(p) for p in points]

    def camera_dir(self):
        """Unit vector pointing from the scene towards the viewer."""
        cp = math.cos(self.pitch)
        return (math.sin(self.yaw) * cp, math.cos(self.yaw) * cp,
                math.sin(self.pitch))


def fit_view(area_w, area_h, margin=40, view=None, fill=1.0):
    """Return a :class:`View` scaled/centred to fit an area.

    *fill* is the fraction of the available area (after *margin*) the
    wireframe is allowed to occupy; values below 1.0 "zoom out" so that
    screen-space labels keep a sane size relative to the drawing.
    """
    if view is None:
        view = View()
    # Sample the wireframe to find the projected bounds, then rescale.
    rings, _longs = build_wireframe()
    pts = [p for ring in rings for p in ring]
    probe = View(yaw=view.yaw, pitch=view.pitch, scale=1.0, roll=view.roll)
    coords = [probe.project(p) for p in pts]
    min_x = min(c[0] for c in coords)
    max_x = max(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    max_y = max(c[1] for c in coords)
    w = max(1e-6, max_x - min_x)
    h = max(1e-6, max_y - min_y)
    scale = min((area_w - 2 * margin) / w, (area_h - 2 * margin) / h) * fill
    view.scale = scale
    view.offset_x = area_w / 2.0 - (min_x + max_x) / 2.0 * scale
    view.offset_y = area_h / 2.0 - (min_y + max_y) / 2.0 * scale
    return view


def upright_roll(yaw, pitch):
    """Roll (radians) that stands the body's long axis up, nose at the top.

    A flat 90 deg is not enough: at the default yaw/pitch the length axis
    already projects ~21 deg off horizontal, so a quarter turn leaves the
    mouse visibly tilted.  The length axis runs along
    ``(cos(yaw), sin(yaw)*sin(pitch))`` in projection, so the roll that
    stands it upright is the turn from that direction to straight down.
    """
    return (math.pi / 2.0
            - math.atan2(math.cos(yaw) * math.sin(pitch), -math.sin(yaw)))


def point_in_polygon(px, py, poly):
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Rendering (any cairo-compatible context)
# ---------------------------------------------------------------------------

#: Fixed chip column per button.  Centroid-based assignment sends middle
#: buttons across the whole body in the 3/4 view (long crossing leaders);
#: a semantic split keeps leaders short in every view.
_CHIP_SIDE = {
    "button1": "left",
    "button4": "left",
    "button5": "left",
    "button7": "left",
    "button8": "left",
    "button9": "left",
    "button2": "right",
    "button3": "right",
    "button6": "right",
    "scrollup": "right",
    "scrolldown": "right",
}


def _stroke_polyline(cr, view, line, closed=False):
    pts = view.project_many(line)
    if not pts:
        return
    cr.move_to(*pts[0])
    for pt in pts[1:]:
        cr.line_to(*pt)
    if closed:
        cr.close_path()
    cr.stroke()


def _seg_facing(view, p0, p1, bias):
    """True when the shell segment p0->p1 faces the camera.

    The outward normal is approximated radially in the ring plane (the
    shell is convex, so a front-facing test is enough to hide the far
    side and the underside).
    """
    mx = (p0.x + p1.x) / 2.0
    my = (p0.y + p1.y) / 2.0
    mz = (p0.z + p1.z) / 2.0
    a, zb, zt = _interp_station(min(0.5, max(-0.5, my)))
    zc = (zb + zt) / 2.0
    hh = max(1e-6, (zt - zb) / 2.0)
    nx = mx / max(a, 1e-6)
    nz = (mz - zc) / hh
    n = math.hypot(nx, nz) or 1.0
    nx, nz = nx / n, nz / n
    dx, _dy, dz = view.camera_dir()
    return (nx * dx + nz * dz) > bias


def _stroke_facing(cr, view, line, closed=False, bias=-0.10):
    """Stroke only the camera-facing runs of a 3D polyline."""
    n = len(line)
    if n < 2:
        return
    pts = view.project_many(line)
    nseg = n if closed else n - 1
    run = []
    for i in range(nseg):
        j = (i + 1) % n
        if _seg_facing(view, line[i], line[j], bias):
            if not run:
                run = [pts[i]]
            run.append(pts[j])
        elif len(run) >= 2:
            cr.move_to(*run[0])
            for pt in run[1:]:
                cr.line_to(*pt)
            cr.stroke()
            run = []
        else:
            run = []
    if len(run) >= 2:
        cr.move_to(*run[0])
        for pt in run[1:]:
            cr.line_to(*pt)
        cr.stroke()


def render(cr, w, h, view, *, visible=None, active=frozenset(), hover=None,
           label_fn=None, font_size=12.5, chip_h=26):
    """Draw the whole scene (shell, details, buttons, labels).

    Only button keys in *visible* are drawn (``None`` draws all).
    *active* / *hover* control highlight intensity.  *label_fn(key,
    label) -> str* provides the chip text.

    Returns ``{"view": view, "chips": {key: (x, y, w, h, label)},
    "anchors": {key: (x, y)}}`` for hit-testing.
    """
    cr.save()
    cr.set_line_join(0)  # round-ish

    # Background.
    cr.set_source_rgba(0.035, 0.035, 0.06, 1.0)
    cr.paint()

    rings, longs = build_wireframe()

    # Honeycomb hole rims (real cut-outs; under the shell lines, dim;
    # far side culled).
    cr.set_source_rgba(0.55, 0.65, 0.95, 0.50)
    cr.set_line_width(1.0)
    cdx, _cdy, cdz = view.camera_dir()
    for hole in DETAILS["holes"]:
        # Whole-cell facing test: a cell straddling the silhouette must not
        # be torn in half the way a per-edge test would tear it.
        nx, _ny, nz = hole["normal"]
        if nx * cdx + nz * cdz > -0.04:
            _stroke_polyline(cr, view, hole["pts"], closed=True)

    # Longitudinals (front-to-back lines; far side + underside culled).
    cr.set_source_rgba(0.30, 0.36, 0.56, 0.50)
    cr.set_line_width(0.7)
    for line in longs:
        _stroke_facing(cr, view, line)

    # Cross-section rings.
    cr.set_source_rgba(0.45, 0.58, 0.92, 0.60)
    cr.set_line_width(0.8)
    for ring in rings:
        _stroke_facing(cr, view, ring, closed=True)

    # Seams (real shell/base rim, centre groove, thumb panel; polylines
    # repeat their first point when meant to be closed).
    cr.set_source_rgba(0.62, 0.75, 1.0, 0.60)
    cr.set_line_width(1.2)
    for seam in DETAILS["seams"]:
        _stroke_facing(cr, view, seam, bias=-0.04)

    # Buttons (filled translucent facets).
    entries = []
    for key, (label, poly) in BUTTONS.items():
        if visible is not None and key not in visible:
            continue
        pts = view.project_many(poly)
        cr.move_to(*pts[0])
        for pt in pts[1:]:
            cr.line_to(*pt)
        cr.close_path()
        # Kept light: a heavy fill turns every cap into a flat plate floating
        # over the shell, and hides the honeycomb the official views show
        # through the translucent keycaps.
        lit = key == hover or key in active
        cr.set_source_rgba(0.32, 0.55, 1.0, 0.24 if lit else 0.07)
        cr.fill_preserve()
        cr.set_source_rgba(0.65, 0.80, 1.0, 1.0 if key == hover else 0.95)
        cr.set_line_width(1.8 if key == hover else 1.4)
        cr.stroke()
        ax = sum(p[0] for p in pts) / len(pts)
        ay = sum(p[1] for p in pts) / len(pts)
        entries.append((key, label, ax, ay))

    # Scroll wheel cylinder: drawn last so it is not buried under the
    # middle-click fill.  Near rim bright, far rim dim (depth cue), hub and
    # tread ridges give it roundness.
    wheel = DETAILS["wheel"]
    cr.set_source_rgba(0.50, 0.63, 0.92, 0.60)
    cr.set_line_width(1.0)
    for far in wheel["far"]:
        _stroke_polyline(cr, view, far)
    for hub in wheel["hubs"]:
        _stroke_polyline(cr, view, hub)
    cr.set_source_rgba(0.55, 0.68, 0.95, 0.70)
    cr.set_line_width(1.0)
    for tread in wheel["treads"]:
        _stroke_polyline(cr, view, tread)
    cr.set_source_rgba(0.85, 0.92, 1.0, 0.98)
    cr.set_line_width(1.7)
    for near in wheel["near"]:
        _stroke_polyline(cr, view, near)

    # Label chips: left/right columns (semantic split, see _CHIP_SIDE).
    # Chips track their anchor height so leader lines stay short; forward /
    # backward passes enforce the vertical gap while keeping the column
    # order (so the leaders never cross).
    cr.select_font_face("sans", 0, 0)
    cr.set_font_size(font_size)
    columns = {"left": [], "right": []}
    for key, label, ax, ay in entries:
        columns[_CHIP_SIDE.get(key, "left")].append((key, label, ax, ay))

    chips = {}
    anchors = {key: (ax, ay) for key, _label, ax, ay in entries}
    pad, gap = 12, 6
    for side in ("left", "right"):
        col = sorted(columns[side], key=lambda e: e[3])
        cys = [min(max(ay - chip_h / 2, pad), h - chip_h - pad)
               for _key, _label, _ax, ay in col]
        for i in range(1, len(cys)):
            cys[i] = max(cys[i], cys[i - 1] + chip_h + gap)
        if cys:
            over = cys[-1] + chip_h + pad - h
            if over > 0:
                cys[-1] -= over
                for i in range(len(cys) - 2, -1, -1):
                    cys[i] = min(cys[i], cys[i + 1] - chip_h - gap)
        for (key, label, ax, ay), cy in zip(col, cys):
            text = label_fn(key, label) if label_fn else label
            ext = cr.text_extents(text)
            cw = ext.width + 18
            cx = 12 if side == "left" else w - cw - 12
            chips[key] = (cx, cy, cw, chip_h, label)

            # Leader line + anchor dot.
            ex = cx + cw if side == "left" else cx
            ey = cy + chip_h / 2
            cr.set_source_rgba(0.4, 0.6, 1.0, 0.45)
            cr.set_line_width(1)
            cr.move_to(ax, ay)
            cr.line_to(ex, ey)
            cr.stroke()
            cr.set_source_rgba(0.55, 0.72, 1.0, 0.9)
            cr.arc(ax, ay, 2.2, 0, 2 * math.pi)
            cr.fill()

            # Chip.
            cr.set_source_rgba(0.08, 0.14, 0.26, 0.95)
            cr.rectangle(cx, cy, cw, chip_h)
            cr.fill_preserve()
            cr.set_source_rgba(0.30, 0.45, 0.75, 1.0)
            cr.set_line_width(1)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            baseline = cy + (chip_h - ext.height) / 2 - ext.y_bearing
            cr.move_to(cx + 9, baseline)
            cr.show_text(text)

    cr.restore()
    return {"view": view, "chips": chips, "anchors": anchors}


# ---------------------------------------------------------------------------
# Built once, here, because both builders need everything above them.
# ---------------------------------------------------------------------------

#: ``{button_key: (label, [Point3D, ...])}`` -- physical outlines of the mouse.
BUTTONS = build_buttons()

#: ``{"seams": [...], "holes": [...], "wheel": {...}}`` -- surface decoration.
DETAILS = build_details()


# ---------------------------------------------------------------------------
# Lighting preview (the RGB page)
# ---------------------------------------------------------------------------
#
# The RGB page draws the same mouse as the Buttons page, but *lit*: the shell
# is shaded slab by slab, each slab tinted by the device zone its length
# position falls in, with an additive pass for the glow, the honeycomb
# perforations picked out in the same colour and a brighter ribbon where the
# real light strip runs along the lower flank.
#
# This is deliberately a separate path from :func:`render`.  The Buttons page
# is a line drawing with translucent keycap fills and a return contract the
# tests pin; nothing here touches it.

#: Zone boundaries, in mm from the nose.  50 mm is where the click panels end;
#: 88 mm leaves the tail as the zone that carries the visible strip.
_ZONE_EDGES_MM = (50.0, 88.0)
#: Crossfade half-width.  Wide on purpose: at 5 mm the bands met in a hard
#: edge that read as three flat blocks again, which is what this display was
#: meant to replace.
_ZONE_FADE_MM = 26.0
_ZONE_EDGES = tuple((b - _Y0_MM) / _MM for b in _ZONE_EDGES_MM)
_ZONE_HALF = 0.5 * _ZONE_FADE_MM / _MM

#: The real strip: low on the flanks, from mid-body to the tail.
_STRIP_Z = 6.0 / _MM
_STRIP_Y = ((45.0 - _Y0_MM) / _MM, (122.0 - _Y0_MM) / _MM)
#: The camera looks *down* at the body, so the strip -- at z ~1-5 mm, right at
#: the silhouette -- fails the ordinary facing test and would be culled.  A
#: permissive bias draws it as the bright rim a diffuser shows edge-on.
_STRIP_BIAS = -0.25

#: Cairo's ``OPERATOR_ADD``.  Named here rather than imported because this
#: module stays cairo-free (like ``set_line_join(0)`` below) so any cairo-like
#: context can render it.
_OPERATOR_ADD = 12

#: Ring-point indices per slab.  A slab takes its normal from its own
#: polygon, so a slab that spans half the circumference averages out to
#: pointing straight at the camera and every slab shades the same -- which is
#: how the first version of this ended up a flat blob.  Capping the arc keeps
#: each normal local, so the shading actually varies across the body.
_MAX_SLAB_ARC = 6

#: Shell shading: an ambient floor plus what facing the camera adds.  The body
#: deliberately never reaches the full zone colour -- the glow belongs to the
#: strip and the perforations -- so the form stays readable instead of washing
#: out.
_SHADE_FLOOR = 0.13
_SHADE_RANGE = 0.48
#: The additive under-pass.  Subtle: past about a tenth it stops reading as
#: glow and starts lifting the whole shell off the background.
_BLOOM = 0.06


def _lerp_p(p, q, t):
    return Point3D(p.x + (q.x - p.x) * t, p.y + (q.y - p.y) * t,
                   p.z + (q.z - p.z) * t)


def _newell(poly):
    """Newell area vector of a 3D polygon (unnormalised)."""
    nx = ny = nz = 0.0
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        nx += (p.y - q.y) * (p.z + q.z)
        ny += (p.z - q.z) * (p.x + q.x)
        nz += (p.x - q.x) * (p.y + q.y)
    return nx, ny, nz


def _shoelace(pts):
    """Signed area of a projected polygon (screen px^2)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def _quad_facing(cdir, a0, a1, b1, b0):
    """How squarely the quad a0-a1-b1-b0 faces the camera (cos-ish).

    A Newell normal, not the renderer's pure-radial one: that has no ``y``
    component and misclassifies the strongly y-facing nose and tail surface.
    The sign is fixed radially, because the ring winding is arbitrary.
    """
    nx, ny, nz = _newell((a0, a1, b1, b0))
    mx = (a0.x + a1.x + b0.x + b1.x) / 4.0
    my = (a0.y + a1.y + b0.y + b1.y) / 4.0
    mz = (a0.z + a1.z + b0.z + b1.z) / 4.0
    hw, zb, zt = _interp_station(min(0.5, max(-0.5, my)))
    zc = (zb + zt) / 2.0
    hh = max(1e-6, (zt - zb) / 2.0)
    if nx * (mx / max(hw, 1e-6)) + nz * ((mz - zc) / hh) < 0.0:
        nx, ny, nz = -nx, -ny, -nz
    d = math.hypot(math.hypot(nx, ny), nz) or 1.0
    return (nx * cdir[0] + ny * cdir[1] + nz * cdir[2]) / d


def _run_ranges(mask):
    """Contiguous true runs of a cyclic mask, as ``(start, length)`` pairs."""
    n = len(mask)
    if not any(mask):
        return []
    if all(mask):
        return [(0, n)]
    out = []
    k = 0
    while k < n:
        if mask[k] and not mask[k - 1]:
            length = 0
            while length < n and mask[(k + length) % n]:
                length += 1
            out.append((k, length))
            k += length
        else:
            k += 1
    return out


def _ribbon_points(a_ring, b_ring, start, length):
    """Closed ribbon polygon over a run of ring-point indices."""
    n = len(a_ring)
    idx = [(start + i) % n for i in range(length + 1)]
    return ([a_ring[i] for i in idx]
            + [b_ring[i] for i in reversed(idx)])


def _smoothstep(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def zone_weights(y):
    """How much each of the three zones lights a station at *y*.

    A sequential crossfade over the two boundaries, so the three weights
    always sum to 1 -- the naive ``w2 = t2`` does not.
    """
    b1, b2 = _ZONE_EDGES
    h = _ZONE_HALF
    t1 = _smoothstep((y - (b1 - h)) / (2.0 * h))
    t2 = _smoothstep((y - (b2 - h)) / (2.0 * h))
    return (1.0 - t1, t1 * (1.0 - t2), t1 * t2)


def zone_index(y):
    """The zone a station belongs to, as an index into ``(z1, z2, z3)``."""
    w = zone_weights(y)
    return max(range(3), key=lambda i: w[i])


def zone_colour(colors, y):
    """The zone colours blended for a station at *y*."""
    w = zone_weights(y)
    return tuple(sum(c[i] * wi for c, wi in zip(colors, w)) for i in range(3))


#: How much of the hue circle the rainbow effect lays along the body.  Narrow on
#: purpose.  The effect is mostly *one colour* that drifts, so the zones have to
#: sit close together in hue -- at 0.2 the nose and tail are ~72 deg apart and
#: neighbouring zones ~23 deg, close enough that two of them usually read as the
#: same colour, the way the device does.  A wide sweep (most of a turn) paints
#: three visibly different colours on one mouse, which is a different effect;
#: that is what this was, and it was wrong.  The width also sets how fast the
#: roll *looks*: see ``_RAINBOW_PERIOD_MS`` in rivalcfg_gui.py.
_RAINBOW_TURNS = 0.2


def _hsv(h, s, v):
    """HSV -> RGB, each 0..1.  Six-segment ramp; cairo wants RGB triplets."""
    h = h % 1.0
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    return ((v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q))[i % 6]


def rainbow_colour(y, phase=0.0):
    """The rainbow effect's colour for a station at *y* (-0.5 nose .. 0.5 tail).

    Fully saturated, so the sweep stays a spectrum instead of washing out
    against the dark shell.  ``0.5 - y`` puts the nose *ahead* of the tail in
    the sweep, so advancing *phase* -- the motion a shift animates -- carries
    each colour from the nose down towards the tail: the effect rolls top to
    bottom.  The span is only :data:`_RAINBOW_TURNS` wide, so at any instant the
    body holds a slice of the circle, not the whole of it.
    """
    return _hsv((0.5 - y) * _RAINBOW_TURNS + phase, 1.0, 1.0)


def _colour_at(colors, y):
    """Colour for a station: three zone colours, or a ``y -> rgb`` callback.

    The callback is how the caller lights the body with something other than
    three flat zone colours -- the rainbow effect samples a hue sweep -- while
    this module stays innocent of effects.  It is also why the geometry never
    carries colour: both forms are drawn through the same cached projection.
    """
    if callable(colors):
        return colors(y)
    return zone_colour(colors, y)


def _visible_runs(view, line, closed=False, bias=-0.10):
    """Projected polylines of the camera-facing runs of a 3D polyline."""
    n = len(line)
    if n < 2:
        return []
    pts = view.project_many(line)
    nseg = n if closed else n - 1
    out, run = [], []
    for i in range(nseg):
        j = (i + 1) % n
        if _seg_facing(view, line[i], line[j], bias):
            if not run:
                run = [pts[i]]
            run.append(pts[j])
        else:
            if len(run) >= 2:
                out.append(run)
            run = []
    if len(run) >= 2:
        out.append(run)
    return out


def _slab(view, poly, y, *, shade=True):
    """Projected slab entry, or None if the polygon is degenerate."""
    pts = view.project_many(poly)
    if len(pts) < 3 or abs(_shoelace(pts)) < 1e-6:
        return None
    cd = view.camera_dir()
    nx, ny, nz = _newell(poly)
    d = math.hypot(math.hypot(nx, ny), nz) or 1.0
    # The slab survived the facing test, so it is front-facing by
    # construction; the raw Newell sign just reflects the ring winding, which
    # is arbitrary -- take the magnitude.  Grazing slabs land near zero and
    # shade dark, which is what the silhouette should do.
    lam = abs((nx * cd[0] + ny * cd[1] + nz * cd[2]) / d)
    depth = (sum(p.x * cd[0] + p.y * cd[1] + p.z * cd[2] for p in poly)
             / len(poly))
    return {"pts": pts, "y": y, "zone": zone_index(y), "depth": depth,
            "shade": _SHADE_FLOOR + _SHADE_RANGE * lam if shade else 1.0}


def build_lighting_geometry(area_w, area_h, view=None, *, margin=14, fill=0.94,
                            subdiv=2):
    """Project the shaded body once, so colour changes only refill.

    Returns ``(view, geometry)``; ``geometry`` holds the already-projected
    slabs, strip ribbons, honeycomb rims and structural lines, plus the slab
    polygons :func:`zone_at` hit-tests against.  Building this is the
    expensive half (a few thousand Newell loops); the RGB page caches it and
    only calls :func:`render_lighting` again on a colour change, because the
    colour editor fires on every pointer motion while dragging.
    """
    if view is None:
        view = View()
    view = fit_view(area_w, area_h, margin=margin, view=view, fill=fill)
    rings, longs = build_wireframe()
    cdir = view.camera_dir()
    n = len(rings[0])

    slabs, strip = [], []
    for i in range(len(rings) - 1):
        y0 = _STATIONS[i][0]
        y1 = _STATIONS[i + 1][0]
        for s in range(subdiv):
            t0, t1 = s / subdiv, (s + 1) / subdiv
            a_ring = [_lerp_p(rings[i][k], rings[i + 1][k], t0) for k in range(n)]
            b_ring = [_lerp_p(rings[i][k], rings[i + 1][k], t1) for k in range(n)]
            y = y0 + (y1 - y0) * 0.5 * (t0 + t1)
            in_strip_y = _STRIP_Y[0] <= y <= _STRIP_Y[1]

            face, low = [], []
            for k in range(n):
                a0, a1 = a_ring[k], a_ring[(k + 1) % n]
                b1, b0 = b_ring[(k + 1) % n], b_ring[k]
                facing = _quad_facing(cdir, a0, a1, b1, b0)
                face.append(facing > 0.0)
                low.append(in_strip_y and facing > _STRIP_BIAS
                           and 0.5 * (a0.z + b0.z) < _STRIP_Z)

            for start, length in _run_ranges(face):
                for off in range(0, length, _MAX_SLAB_ARC):
                    seg = min(_MAX_SLAB_ARC, length - off)
                    entry = _slab(view, _ribbon_points(a_ring, b_ring,
                                                       start + off, seg), y)
                    if entry is not None:
                        slabs.append(entry)
            for start, length in _run_ranges(low):
                entry = _slab(view, _ribbon_points(a_ring, b_ring, start, length),
                              y, shade=False)
                if entry is not None:
                    strip.append(entry)

    # Painter order: far slabs first, so a nearer one always wins an overlap.
    slabs.sort(key=lambda s: s["depth"])

    holes = []
    for hole in DETAILS["holes"]:
        nx, _ny, nz = hole["normal"]
        if nx * cdir[0] + nz * cdir[2] <= -0.04:
            continue  # far side, same cull the Buttons page uses
        cy = sum(p.y for p in hole["pts"]) / len(hole["pts"])
        holes.append({"pts": view.project_many(hole["pts"]), "y": cy})

    # Rings sit at one station each, so a ring takes a single zone colour;
    # the longitudinals, seams and button outlines are plain point lists --
    # they span zones, so they stay neutral structure.
    ring_lines = []
    for ring in rings:
        for run in _visible_runs(view, ring, closed=True):
            ring_lines.append({"pts": run, "y": ring[0].y})
    long_lines = []
    for line in longs:
        long_lines.extend(_visible_runs(view, line))
    seam_lines = []
    for seam in DETAILS["seams"]:
        seam_lines.extend(_visible_runs(view, seam, bias=-0.04))

    button_lines = [view.project_many(poly) for _label, poly in BUTTONS.values()]

    geometry = {
        "view": view,
        "slabs": slabs,
        "strip": strip,
        "holes": holes,
        "rings": ring_lines,
        "longitudinals": long_lines,
        "seams": seam_lines,
        "buttons": button_lines,
    }
    return view, geometry


def _path(cr, pts):
    cr.move_to(*pts[0])
    for pt in pts[1:]:
        cr.line_to(*pt)
    cr.close_path()


def _tint(c, lit):
    """Pull a zone colour towards white by *lit* (0..1), for the lit edges."""
    return (c[0] + (1.0 - c[0]) * lit, c[1] + (1.0 - c[1]) * lit,
            c[2] + (1.0 - c[2]) * lit)


def render_lighting(cr, w, h, geometry, colors):
    """Draw the lit mouse.  *colors* is three ``(r, g, b)`` triples in 0..1, or
    a ``y -> (r, g, b)`` callback when the body is not lit by the zone colours
    alone (:func:`rainbow_colour` is that form).

    Geometry comes from :func:`build_lighting_geometry`, so this only fills:
    it is cheap enough to run on every colour change.  Returns the *geometry*
    unchanged, for hit-testing a click with :func:`zone_at`.
    """
    cr.save()
    cr.set_line_join(0)

    # Opaque, like the Buttons page -- the RGB page's ``.color-preview`` class
    # is border-only, and additive bloom over a transparent surface is wrong.
    cr.set_source_rgba(0.035, 0.035, 0.06, 1.0)
    cr.paint()

    slabs = geometry["slabs"]

    # Glow first, underneath: one additive pass, not several -- repeated
    # passes only muddy the body and multiply the cost.
    cr.save()
    cr.set_operator(_OPERATOR_ADD)
    for slab in slabs:
        c = _colour_at(colors, slab["y"])
        cr.set_source_rgba(c[0] * _BLOOM, c[1] * _BLOOM, c[2] * _BLOOM, 1.0)
        _path(cr, slab["pts"])
        cr.fill()
    cr.restore()

    # The shaded shell.  Each slab is stroked in its own fill colour so the
    # shared edges do not show as hairline cracks.  The fill stays well under
    # the full zone colour: this is a shell with light behind it, not a lamp.
    for slab in slabs:
        c = _colour_at(colors, slab["y"])
        s = slab["shade"]
        cr.set_source_rgb(c[0] * s, c[1] * s, c[2] * s)
        _path(cr, slab["pts"])
        cr.fill_preserve()
        cr.set_line_width(1.0)
        cr.stroke()

    # The strip: a bright rim drawn last so it reads as the one emissive part.
    for slab in geometry["strip"]:
        cr.set_source_rgb(*_tint(_colour_at(colors, slab["y"]), 0.30))
        _path(cr, slab["pts"])
        cr.fill_preserve()
        cr.set_line_width(1.0)
        cr.stroke()

    # Honeycomb rims, in the zone colour of each cell: the perforations are
    # what makes the light read as coming from inside the shell.
    for hole in geometry["holes"]:
        cr.set_source_rgba(*_tint(_colour_at(colors, hole["y"]), 0.45), 0.70)
        cr.set_line_width(1.0)
        _path(cr, hole["pts"])
        cr.stroke()

    # Structure on top, to keep it reading as the mesh.  The rings carry a
    # station each, so they take the zone tint; the longitudinals, seams and
    # button outlines run across zones and stay a neutral light grey.
    cr.set_line_width(0.8)
    for line in geometry["rings"]:
        cr.set_source_rgba(*_tint(_colour_at(colors, line["y"]), 0.55), 0.28)
        _stroke_pts(cr, line["pts"], closed=True)
    cr.set_source_rgba(0.80, 0.86, 0.96, 0.16)
    cr.set_line_width(0.7)
    for line in geometry["longitudinals"]:
        _stroke_pts(cr, line)
    cr.set_source_rgba(0.80, 0.86, 0.96, 0.50)
    cr.set_line_width(1.1)
    for line in geometry["seams"]:
        _stroke_pts(cr, line)
    cr.set_source_rgba(0.88, 0.92, 1.0, 0.60)
    cr.set_line_width(1.3)
    for line in geometry["buttons"]:
        _stroke_pts(cr, line, closed=True)

    cr.restore()
    return geometry


def _stroke_pts(cr, pts, closed=False):
    if not pts:
        return
    cr.move_to(*pts[0])
    for pt in pts[1:]:
        cr.line_to(*pt)
    if closed:
        cr.close_path()
    cr.stroke()


def zone_at(geometry, x, y):
    """Zone index under a screen point, or None if it misses the body.

    The slabs are back-face culled, so the facing ones tile the silhouette;
    scanning back-to-front and taking the first hit gives the nearest slab.
    """
    for slab in reversed(geometry["slabs"]):
        if point_in_polygon(x, y, slab["pts"]):
            return slab["zone"]
    return None
