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

The real Aerox 5 is ~127.6 mm long, ~68.4 mm wide, ~42.1 mm tall.
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
    """Seat a normalised outline point *on* the shell, following its curve.

    :func:`_cap_on_surface` only corrects height: a point that belongs on
    the shoulder keeps its flat chord there, which is what makes the click
    panels read as plates bolted over the shell.  This walks the cross-
    section to the arc position with the same ``x`` and takes that point --
    so the outline bends with the shell, over the crown and down the flank.
    """
    x_mm, y_mm = x * _MM, y * _MM + _Y0_MM
    sign = -1.0 if x_mm < 0 else 1.0
    i, t = _ring_span(y_mm)
    # Both flanks are snapped against the same profile so the two keycaps
    # stay exact mirrors of each other; the shell is symmetric to well
    # under a millimetre, and the two sampled profiles are not identical,
    # which otherwise showed up as a ~0.5 mm left/right keycap mismatch.
    sides = _HONEY_POS
    px = [sides[i][j][0] + (sides[i + 1][j][0] - sides[i][j][0]) * t
          for j in range(_HONEY_PROFILE)]
    pz = [sides[i][j][1] + (sides[i + 1][j][1] - sides[i][j][1]) * t
          for j in range(_HONEY_PROFILE)]
    # The profile runs crown -> widest point -> base rim, so ``|x|`` only
    # grows up to the widest sample and curls back in after it.  Searching
    # the whole profile would snap a shoulder point onto the underside; a
    # simple "stop when |x| shrinks" walk instead fires on the very first
    # step of the left flank, whose |x| falls from the off-centre crown
    # sample through zero before it grows again.  Cut at the widest sample.
    widest = max(range(_HONEY_PROFILE), key=lambda j: abs(px[j]))
    best = min(range(widest + 1), key=lambda j: abs(px[j] - abs(x_mm)))
    return sign * px[best] / _MM, y, pz[best] / _MM + lift


def _cap_on_surface(key, lift=0.006):
    """Re-lift a top-button cap outline onto the curved top surface.

    Uses the explicit ordered outline (``CAP_OUTLINES``) when the mesh has
    one, so the keycap's concave centre channel -- which clears the scroll
    wheel and the CPI button -- is preserved instead of being hulled flat.
    """
    source = getattr(_mesh, "CAP_OUTLINES", {}).get(key) or _mesh.CAPS[key]
    pts = []
    for p in _smooth_closed([Point3D(*q) for q in source]):
        pts.append(Point3D(*_snap_to_shell(p.x, p.y, lift)))
    return pts


def _cap_on_flank(key, lift=0.004):
    """Re-lift a side-button cap outline onto the curved left flank.

    The extracted caps are stored as *planar* outlines at a constant ``x``
    (the plane the source OBJ built them on), but the real flank swells
    outward underneath them, so drawn as-is they read as flat slabs slicing
    through the shell.  Every point is re-seated at the flank surface for its
    own ``(y, z)`` -- the mirror of :func:`_cap_on_surface` for the top.
    """
    pts = []
    for p in _smooth_closed([Point3D(*p) for p in _mesh.CAPS[key]]):
        fx = side_surface_x(p.y, p.z)
        if fx is None or fx >= 0.0:
            fx = p.x
        pts.append(Point3D(fx - lift, p.y, p.z))
    return pts


def build_buttons():
    """Return ``{button_key: (label, [Point3D, ...])}``.

    Mapping matches the official rivalcfg Aerox 5 schema / the SteelSeries
    manual's side-button legend.  All physical outlines now come from the
    corrected detailed model:

    * buttons 1/2 (left/right click) are the real keycap outlines, lifted
      onto the curved top surface so they wrap down the low nose;
    * button 6 (CPI) and 9 (forward trigger) are the physical cap outlines;
    * buttons 4/5 are the real thumb buttons;
    * buttons 7/8 are the two halves of the real up/down flick rocker;
    * button 3 is the scroll wheel itself (middle click), with two arrow
      regions for scroll up / scroll down instead of overlaying the wheel.
    """
    buttons = {}

    # Left / right click keycaps (real outlines, wrapped onto the top).
    buttons["button1"] = ("Left click", _cap_on_surface("main_button_left"))
    buttons["button2"] = ("Right click", _cap_on_surface("main_button_right"))

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




# ---------------------------------------------------------------------------
# Honeycomb: a parametric lattice mapped onto the real shell surface
# ---------------------------------------------------------------------------
#
# The extracted ``RIMS`` turned out to be thin slivers (~2 mm x 6 mm) rather
# than the round openings of the real mouse, so the pattern is generated
# instead.  Measured off the official 1:1 views (top view: 0.3554 mm/px):
# the openings are *rounded diamonds* on a staggered lattice whose nearest
# neighbours sit at 45 deg, 4.9 mm apart -- i.e. a checkerboard of 3.45 mm
# -- with ~1.8 mm ribs, and the pattern runs from just behind the click caps
# to the tail, wrapping the crown and down the flanks.
#
# The lattice is laid out in ``(u, v)``: ``v`` is the station along the
# mouse, ``u`` the arc distance along the cross-section away from the crown
# apex, so the pattern stays uniform where it rolls over the shoulder.  Both
# are sampled from the real rings, which is what keeps the cells on the
# surface instead of floating beside it.

_HONEY_CELL = 3.45           # mm, checkerboard node spacing
_HONEY_HALF = 2.2            # mm, half-diagonal (vertex radius) of one cell
_HONEY_ROUND = 0.55          # mm, corner radius of one cell
_HONEY_REAR_Y = 41.5         # mm from the nose: rear field starts behind the caps
_HONEY_WRAP_Y = 119.4        # mm: station of the field's last row
# Flank reach, as a fraction of the cross-section's arc, apex -> base rim.
# Measured off the official top view: the outermost opening in each row sits
# at ~0.91 of the silhouette half-width, and a cell's outer vertex reaches a
# further 1.95 mm, so the field stops about a millimetre inside the outline.
# The reach grows only slowly towards the tail, where the section's arc is
# short and most of it is the rolled shoulder.
_HONEY_WRAP0 = 0.533         # flank reach at _HONEY_REAR_Y, apex -> base rim
_HONEY_WRAP1 = 0.588         # flank reach at _HONEY_WRAP_Y
# The centre channel has no perforation: it is the scroll wheel's slot and the
# CPI housing.  A capsule down the centreline covers both with a margin, and
# leaves the two patches flanking the wheel to run back and merge with the
# rest of the field behind it -- the top view shows one continuous perforation
# from the caps' rear edge to the tail, parted only by that channel.
_HONEY_CENTRE = (6.4, 36.0, 72.0)      # radius, y0, y1 (mm)
_HONEY_PROFILE = 192         # samples per side, crown apex -> base rim
_HONEY_RIM = 3.0             # mm of bare shell left at the tail's rim
_HONEY_RIM_Z = 1.5           # mm: below this a ring point is base, not shell

_MM = _mesh.LENGTH_MM
_Y0_MM = 63.8                # station of normalised y = 0


def _cr_closed(pts, per_seg=6):
    """Densify a closed polygon of ``(x, z)`` pairs (Catmull-Rom)."""
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        for k in range(per_seg):
            t = k / per_seg
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * t
                       + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2
                       + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3)
                for c in (0, 1)))
    return out


def _ring_profiles():
    """Split every ring into two crown-apex -> base-rim profiles.

    Returns ``(ys, pos, neg, len_pos, len_neg)``.  ``pos[i][j]`` is the
    ``(x, z)`` (mm) at ``j / (_HONEY_PROFILE - 1)`` of the way from the
    crown apex to the right base rim of ring ``i``; ``neg`` mirrors it on
    the left.  Both sides of every ring are sampled at the *same* fractions
    of their own length, so the profiles line up station to station.
    """
    ys, pos, neg, lp, ln = [], [], [], [], []
    for y_norm, ring in _mesh.RINGS:
        dense = _cr_closed([(x * _MM, z * _MM) for x, z in ring])
        n = len(dense)
        segs = [math.dist(dense[i], dense[(i + 1) % n]) for i in range(n)]
        apex = max(range(n), key=lambda i: dense[i][1])
        sides = []
        # Ring points run anticlockwise, so stepping *backwards* from the
        # apex walks the right flank: keep that one first, to match the
        # signed-u convention (positive = right).
        for step in (-1, 1):
            walk, dist = [], []
            k, d = apex, 0.0
            for _ in range(n):
                walk.append(dense[k])
                dist.append(d)
                if step > 0:
                    d += segs[k]
                    k = (k + 1) % n
                else:
                    k = (k - 1) % n
                    d += segs[k]
            # Trim where the shell meets the base plate.  The low rings near
            # the nose never drop below _HONEY_RIM_Z, so for them there is no
            # such crossing: trim at the ring's own lowest point instead.
            # Without that fallback the walk keeps the *whole* closed loop
            # (crown -> flank -> underside -> flank -> crown), and because
            # stations are sampled at matching fractions of their own length,
            # interpolating such a profile against a properly trimmed one
            # pairs an underside sample with a flank sample -- which pulled
            # the keycap outlines metres-deep down the flank and, further
            # back, shrank the profile to a fraction of its real width.
            cut = min(range(1, len(walk)), key=lambda i: walk[i][1])
            for i in range(1, cut + 1):
                if walk[i][1] < _HONEY_RIM_Z:
                    # Trim to where the shell meets the base plate.
                    z0, z1 = walk[i - 1][1], walk[i][1]
                    f = (z0 - _HONEY_RIM_Z) / max(1e-6, z0 - z1)
                    walk[i] = (walk[i - 1][0] + (walk[i][0] - walk[i - 1][0]) * f,
                               _HONEY_RIM_Z)
                    dist[i] = dist[i - 1] + (dist[i] - dist[i - 1]) * f
                    cut = i
                    break
            length = dist[cut]
            prof = []
            j = 0
            for s in range(_HONEY_PROFILE):
                want = length * s / (_HONEY_PROFILE - 1)
                while j < cut - 1 and dist[j + 1] < want:
                    j += 1
                span = dist[j + 1] - dist[j]
                f = 0.0 if span <= 0 else (want - dist[j]) / span
                prof.append((walk[j][0] + (walk[j + 1][0] - walk[j][0]) * f,
                             walk[j][1] + (walk[j + 1][1] - walk[j][1]) * f))
            sides.append((prof, length))
        ys.append(y_norm * _MM + _Y0_MM)
        pos.append(sides[0][0])
        neg.append(sides[1][0])
        lp.append(sides[0][1])
        ln.append(sides[1][1])
    return ys, pos, neg, lp, ln


_HONEY_YS, _HONEY_POS, _HONEY_NEG, _HONEY_LPOS, _HONEY_LNEG = _ring_profiles()


def _ring_span(y_mm):
    """Index/t of the two rings bracketing station *y_mm*."""
    ys = _HONEY_YS
    if y_mm <= ys[0]:
        return 0, 0.0
    if y_mm >= ys[-1]:
        return len(ys) - 2, 1.0
    for i in range(len(ys) - 1):
        if ys[i] <= y_mm <= ys[i + 1]:
            return i, (y_mm - ys[i]) / (ys[i + 1] - ys[i])
    return len(ys) - 2, 1.0


def _surface_pt(y_mm, u_mm):
    """``(x, z)`` on the shell at station *y_mm*, arc offset *u_mm* (mm).

    *u_mm* is measured from the crown apex; positive rolls down the right
    flank, negative the left.  Returns ``(x, z, side_length)``.
    """
    i, t = _ring_span(y_mm)
    sides = _HONEY_POS if u_mm >= 0 else _HONEY_NEG
    lens = _HONEY_LPOS if u_mm >= 0 else _HONEY_LNEG
    length = lens[i] + (lens[i + 1] - lens[i]) * t
    frac = min(1.0, abs(u_mm) / length if length else 0.0) * (_HONEY_PROFILE - 1)
    j = min(_HONEY_PROFILE - 2, int(frac))
    f = frac - j
    out = []
    for c in (0, 1):
        near = sides[i][j][c] + (sides[i][j + 1][c] - sides[i][j][c]) * f
        far = sides[i + 1][j][c] + (sides[i + 1][j + 1][c] - sides[i + 1][j][c]) * f
        out.append(near + (far - near) * t)
    return out[0], out[1], length


def _cell_template():
    """One cell as a rounded diamond in ``(u, v)`` mm, centred on the origin."""
    a, r = _HONEY_HALF, _HONEY_ROUND
    corners = [(a, 0.0), (0.0, a), (-a, 0.0), (0.0, -a)]
    out = []
    for k in range(4):
        cx, cy = corners[k]
        corners_in = []
        for other in (corners[k - 1], corners[(k + 1) % 4]):
            dx, dy = other[0] - cx, other[1] - cy
            d = math.hypot(dx, dy) or 1.0
            corners_in.append((dx / d, dy / d))
        ex, ey = corners_in[0][0] + corners_in[1][0], corners_in[0][1] + corners_in[1][1]
        ax, ay = cx + ex * r, cy + ey * r          # arc centre
        t0 = (cx + corners_in[0][0] * r, cy + corners_in[0][1] * r)
        t1 = (cx + corners_in[1][0] * r, cy + corners_in[1][1] * r)
        a0 = math.atan2(t0[1] - ay, t0[0] - ax)
        a1 = math.atan2(t1[1] - ay, t1[0] - ax)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for s in range(4):
            ang = a0 + da * s / 3
            out.append((ax + r * math.cos(ang), ay + r * math.sin(ang)))
    return out


_HONEY_TEMPLATE = _cell_template()
# How far a cell reaches from its node: the corner radius shaves the diamond
# tips, so this is a little under the 2.2 mm vertex radius, and it -- not
# ``_HONEY_HALF`` -- is what has to stay inside the sampled shell.
_HONEY_TIP = max(abs(dv) for _du, dv in _HONEY_TEMPLATE)


def _honey_accept(y_mm, u_mm, x_mm, length):
    """Is a lattice node inside the honeycomb field?

    The node must sit a whole cell radius clear of every edge: a corner that
    runs past the end of a profile would be clamped onto the base rim and
    drag the cell out into a sliver (the "fan" at the tail).
    """
    if y_mm < _HONEY_REAR_Y:
        return False
    t = min(1.0, max(0.0, (y_mm - _HONEY_REAR_Y)
                      / (_HONEY_WRAP_Y - _HONEY_REAR_Y)))
    reach = (_HONEY_WRAP0 + (_HONEY_WRAP1 - _HONEY_WRAP0) * t) * length
    if abs(u_mm) + _HONEY_TIP > reach:
        return False
    # Clear of the wheel slot and the CPI housing: the distance from the node
    # to the channel's spine (x = 0, y0 + r .. y1 - r) is what has to clear.
    cr, cy0, cy1 = _HONEY_CENTRE
    spine = min(max(y_mm, cy0 + cr), cy1 - cr)
    return x_mm ** 2 + (y_mm - spine) ** 2 >= cr * cr


def build_honeycomb():
    """Honeycomb openings as loops on the shell (normalised coordinates)."""
    cell = _HONEY_CELL
    tip = _HONEY_TIP
    # Rows run from just behind the keycaps to the tail: the first row is
    # anchored on the caps' rear edge, one cell tip clear of it so the
    # openings do not run under the keycap, and the rest follow on the
    # lattice up to the last row that still leaves the tail rim the reference
    # views show.  Anchoring on the tail instead moved the front edge by
    # whatever the phase happened to leave, and the caps' edge is the one
    # boundary the top view pins down.
    top = (_HONEY_REAR_Y + tip) + math.floor(
        (_HONEY_YS[-1] - _HONEY_RIM - 2 * tip - _HONEY_REAR_Y) / cell) * cell
    loops = []
    for row in range(math.floor((24.0 - top) / cell), 1):
        v0 = top + row * cell
        if v0 < 24.0:
            continue
        off = 0.0 if row % 2 == 0 else cell
        for i in range(-20, 21):
            u0 = off + i * 2 * cell
            x, _z, length = _surface_pt(v0, u0)
            # The cell's rear tip sits further back, where the flank is
            # shorter; cull the reach against the tighter of the two.
            _x, _z, rear = _surface_pt(v0 + tip, u0)
            length = min(length, rear)
            if not _honey_accept(v0, u0, x, length):
                continue
            pts = []
            for du, dv in _HONEY_TEMPLATE:
                x, z, _l = _surface_pt(v0 + dv, u0 + du)
                pts.append(Point3D(x / _MM, (v0 + dv - _Y0_MM) / _MM, z / _MM))
            cx, cz, _l = _surface_pt(v0, u0)
            # Outward surface normal, from the profile tangent (the lattice
            # is far more reliable than a radial guess where the shell rolls
            # over the tail and the shoulder).
            xa, za, _l = _surface_pt(v0, u0 + 1.0)
            xb, zb, _l = _surface_pt(v0, u0 - 1.0)
            nx, nz = -(za - zb), (xa - xb)
            d = math.hypot(nx, nz) or 1.0
            loops.append({"pts": pts,
                          "center": Point3D(cx / _MM, (v0 - _Y0_MM) / _MM,
                                            cz / _MM),
                          "normal": (nx / d, 0.0, nz / d)})
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

    def project(self, p: Point3D):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        x1 = p.x * cy - p.y * sy
        y1 = p.x * sy + p.y * cy
        z2 = -y1 * sp + p.z * cp
        return (self.offset_x + x1 * self.scale,
                self.offset_y - z2 * self.scale)

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
    probe = View(view.yaw, view.pitch, 1.0, 0.0, 0.0)
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
