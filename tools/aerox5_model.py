"""Parametric, *detailed* SteelSeries Aerox 5 model (Wireless & wired).

Regenerates the source mesh used by ``rivalcfg-gui``.  The shell silhouette
is captured from the dimensioned reference model; the parts that were
previously wrong are rebuilt:

* **main click buttons** -- raised keycaps with a split between them and a
  proper wrap down the low nose (groups ``main_button_left/right``);
* **side buttons** -- on the *left* flank only, in the real layout: two
  thumb buttons (front/back), the up/down flick rocker above them and the
  front thumb trigger (groups ``side_button_front/rear``, ``rocker_up``,
  ``rocker_down``, ``trigger``);
* **skates** -- the three real PTFE pieces: a wide curved front skate, a
  wide curved rear skate and the ring around the sensor (groups
  ``skate_front``, ``skate_rear``, ``skate_sensor``).

The honeycomb perforation is emitted both as shell geometry (holes removed)
and as an explicit ``honeycomb`` group (the hole cells), so the extractor
can recover the rim loops without guessing from face normals.

Axes (mm): X = width (right +), Y = length (nose ~0 -> tail), Z = height.
Outputs ``aerox5_detailed.obj`` and ``aerox5_detailed.stl``.

Usage:  python3 aerox5_model.py [outdir]
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

LENGTH_MM = 127.6
Y_NOSE_MM = 0.6
WIDTH_MM = 68.2
HEIGHT_MM = 42.1

NU = 320                   # points around each ring
NT = 180                   # slices along the length


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _lerp_table(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            denom = xs[i + 1] - xs[i]
            f = (x - xs[i]) / denom if denom else 0.0
            return ys[i] + (ys[i + 1] - ys[i]) * f
    return ys[-1]


# ---------------------------------------------------------------------------
# Shell profile: station table (mm) + unit cross-section (u in [-1,1],
# v in [0,1]); captured from the dimensioned reference model.
# ---------------------------------------------------------------------------

STATIONS = [  # (y_mm, half_width_mm, z_bottom_mm, z_top_mm)
    (0.60, 2.00, 16.50, 19.50),
    (3.50, 18.604, 12.352, 21.947),
    (8.36, 26.770, 5.704, 24.129),
    (13.23, 30.433, 1.365, 26.286),
    (18.09, 31.900, 0.740, 28.812),
    (22.96, 32.142, 0.740, 32.793),
    (27.82, 32.130, 0.370, 34.299),
    (32.68, 32.130, 0.370, 34.592),
    (37.55, 32.142, 0.370, 36.379),
    (42.41, 32.219, 0.370, 37.987),
    (47.28, 32.359, 0.370, 39.454),
    (52.14, 32.576, 0.370, 40.054),
    (57.00, 32.564, 0.217, 40.832),
    (61.87, 32.219, 0.370, 41.687),
    (66.73, 32.155, 0.370, 42.070),
    (71.60, 32.691, 0.179, 41.534),
    (76.46, 33.304, 0.013, 41.521),
    (81.32, 33.967, 0.370, 40.960),
    (86.19, 34.465, 0.000, 39.684),
    (91.06, 34.490, 0.000, 38.254),
    (95.92, 33.482, 0.000, 36.392),
    (100.78, 32.219, 0.000, 33.929),
    (105.64, 31.760, 0.357, 30.854),
    (110.51, 29.986, 0.370, 27.026),
    (115.38, 27.268, 0.370, 22.955),
    (120.24, 23.070, 0.370, 17.915),
    (125.10, 16.422, 0.842, 11.012),
    (128.20, 0.800, 5.000, 7.000),
]

UNIT_SECTION = [
    (-0.91597, 0.44851), (-0.92253, 0.42721), (-0.93686, 0.37512),
    (-0.95133, 0.31008), (-0.96261, 0.24121), (-0.97082, 0.16948),
    (-0.97665, 0.09458), (-0.97929, 0.01676), (-0.86662, 0.00030),
    (-0.74957, 0.00000), (-0.65287, 0.00000), (-0.57259, 0.00000),
    (-0.50211, 0.00000), (-0.44047, 0.00000), (-0.38567, 0.00000),
    (-0.33618, 0.00000), (-0.29023, 0.00000), (-0.24823, 0.00000),
    (-0.20872, 0.00000), (-0.17081, 0.00000), (-0.13494, 0.00000),
    (-0.10035, 0.00000), (-0.06633, 0.00000), (-0.03300, 0.00000),
    (0.00002, 0.00000), (0.03301, 0.00000), (0.06630, 0.00000),
    (0.10025, 0.00000), (0.13511, 0.00000), (0.17087, 0.00000),
    (0.20855, 0.00000), (0.24837, 0.00000), (0.29028, 0.00000),
    (0.33611, 0.00000), (0.38561, 0.00000), (0.44069, 0.00000),
    (0.50202, 0.00000), (0.57212, 0.00000), (0.65370, 0.00000),
    (0.75037, 0.00000), (0.86740, 0.00030), (0.97192, 0.02091),
    (0.99128, 0.08859), (0.99304, 0.16234), (0.99298, 0.23335),
    (0.99077, 0.30239), (0.98589, 0.36975), (0.97734, 0.43544),
    (0.96489, 0.49954), (0.94789, 0.56162), (0.92579, 0.62127),
    (0.89869, 0.67816), (0.86573, 0.73119), (0.82724, 0.77990),
    (0.78375, 0.82376), (0.73577, 0.86146), (0.68503, 0.89451),
    (0.63178, 0.92042), (0.57801, 0.94219), (0.52502, 0.95838),
    (0.47310, 0.97137), (0.42344, 0.98057), (0.37582, 0.98723),
    (0.33037, 0.99216), (0.28782, 0.99519), (0.24675, 0.99737),
    (0.20774, 0.99868), (0.17085, 0.99927), (0.13485, 0.99959),
    (0.10009, 0.99957), (0.06631, 0.99933), (0.03304, 0.99899),
    (0.00008, 0.99852), (-0.03279, 0.99778), (-0.06582, 0.99679),
    (-0.09920, 0.99555), (-0.13312, 0.99378), (-0.16770, 0.99135),
    (-0.20325, 0.98799), (-0.23978, 0.98347), (-0.27721, 0.97795),
    (-0.31599, 0.97071), (-0.35601, 0.96164), (-0.39698, 0.95080),
    (-0.43918, 0.93725), (-0.48225, 0.92129), (-0.52591, 0.90205),
    (-0.56989, 0.87925), (-0.61372, 0.85314), (-0.65686, 0.82259),
    (-0.69877, 0.78842), (-0.73865, 0.74962), (-0.77658, 0.70718),
    (-0.81154, 0.66050), (-0.84339, 0.61019), (-0.86719, 0.56683),
]

_U = np.array([p[0] for p in UNIT_SECTION])
_V = np.array([p[1] for p in UNIT_SECTION])

# upper branch (v >= 0.5) sorted by u, for top-surface lookup
_upper = sorted(((u, v) for u, v in UNIT_SECTION if v >= 0.5))
_UP_U = [p[0] for p in _upper]
_UP_V = [p[1] for p in _upper]
# left flank: at each height v take the leftmost (min u) surface point, so
# x = f(v) is single-valued.
_lf = {}
for _u, _v in UNIT_SECTION:
    if _u < 0.0:
        _lf[_v] = min(_lf.get(_v, 1.0), _u)
_lf_items = sorted(_lf.items())
_LF_V = [p[0] for p in _lf_items]
_LF_U = [p[1] for p in _lf_items]
# ring-angle lookup around (0, 0.5): phi 0=right, 90=top, 180=left, 270=bottom
_pa = np.degrees(np.arctan2(_V - 0.5, _U)) % 360.0
_o = np.argsort(_pa)
_PA = _pa[_o]
_PU = _U[_o]
_PV = (_V - 0.5)[_o]

#: monotonic v -> "degrees from the top centre (0..90)" lookup, built from
#: the *upper* section only so the mapping is single-valued.
_TOP = sorted((_vv - 0.5, math.degrees(math.atan2(abs(_uu), _vv - 0.5)))
              for _uu, _vv in UNIT_SECTION if _vv >= 0.5)
_V_LOOKUP = np.array([v for v, _a in _TOP])
_TH_LOOKUP = np.array([a for _v, a in _TOP])


def _v_to_th(v):
    """Degrees from the top centre (0=crown, 90=flank) for a unit height *v*."""
    return float(np.interp(v - 0.5, _V_LOOKUP, _TH_LOOKUP))


def station_at(y):
    ys = [s[0] for s in STATIONS]
    if y <= ys[0]:
        return STATIONS[0][1:]
    if y >= ys[-1]:
        return STATIONS[-1][1:]
    for i in range(len(STATIONS) - 1):
        y0, a0, b0, t0 = STATIONS[i]
        y1, a1, b1, t1 = STATIONS[i + 1]
        if y0 <= y <= y1:
            f = (y - y0) / (y1 - y0)
            return (a0 + (a1 - a0) * f, b0 + (b1 - b0) * f,
                    t0 + (t1 - t0) * f)
    return STATIONS[-1][1:]


def half_width(y):
    return station_at(y)[0]


def top_z(x, y):
    """Shell top height at (x, y) (mm); None if x is off the body."""
    a, zb, zt = station_at(y)
    if a <= 0:
        return None
    u = x / a
    if u < _UP_U[0] or u > _UP_U[-1]:
        return None
    v = _lerp_table(_UP_U, _UP_V, u)
    return zb + (zt - zb) * v


def left_x(y, z):
    """Left-flank x (negative) at (y, z) (mm)."""
    a, zb, zt = station_at(y)
    v = _clamp01((z - zb) / (zt - zb)) if zt > zb else 0.0
    return a * _lerp_table(_LF_V, _LF_U, v)


def ring_point(y, phi):
    """Point on the ring at length *y* and ring angle *phi* (radians,
    0 = right flank, pi/2 = top centre, pi = left, 3pi/2 = bottom)."""
    a, zb, zt = station_at(y)
    pdeg = math.degrees(phi) % 360.0
    u = float(np.interp(pdeg, _PA, _PU, period=360.0))
    v = float(np.interp(pdeg, _PA, _PV, period=360.0)) + 0.5
    return (a * u, y, zb + (zt - zb) * v)


def unit_v(phi):
    """Unit-section height v in [0,1] at ring angle *phi* (radians)."""
    pdeg = math.degrees(phi) % 360.0
    return float(np.interp(pdeg, _PA, _PV, period=360.0)) + 0.5


# ---------------------------------------------------------------------------
# Mesh container
# ---------------------------------------------------------------------------

class Mesh:
    def __init__(self):
        self.verts = []
        self.groups = []
        self._faces = []

    def start(self, name):
        self._faces = []
        self.groups.append((name, self._faces))

    def add(self, p):
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts)

    def tri(self, a, b, c):
        self._faces.append((a, b, c))

    def quad(self, a, b, c, d):
        self._faces.append((a, b, c, d))

    def write_obj(self, path):
        with open(path, "w") as f:
            f.write("# SteelSeries Aerox 5 detailed model (mm), nose at y=0.6\n")
            for v in self.verts:
                f.write("v %.4f %.4f %.4f\n" % v)
            for name, faces in self.groups:
                f.write("g %s\n" % name)
                for face in faces:
                    f.write("f " + " ".join(str(i) for i in face) + "\n")

    def write_stl(self, path):
        with open(path, "w") as f:
            f.write("solid aerox5\n")
            for _name, faces in self.groups:
                for face in faces:
                    for k in range(1, len(face) - 1):
                        tri = (face[0], face[k], face[k + 1])
                        a, b, c = (np.array(self.verts[i - 1]) for i in tri)
                        nrm = np.cross(b - a, c - a)
                        ln = np.linalg.norm(nrm)
                        nrm = nrm / ln if ln else nrm
                        f.write("facet normal %f %f %f\n outer loop\n"
                                % tuple(nrm))
                        for p in (a, b, c):
                            f.write("  vertex %f %f %f\n" % tuple(p))
                        f.write(" endloop\nendfacet\n")
            f.write("endsolid aerox5\n")


# ---------------------------------------------------------------------------
# Honeycomb perforation
# ---------------------------------------------------------------------------

#: Honeycomb perforation, parameterised by the *top angle* ``th`` (0 deg at
#: the crown, 90 deg at the flank).  The lattice is **uniform** (so the holes
#: stay evenly placed), but each diamond's *size* grows toward the flank and
#: toward the tail: smallest on the crown, largest low on the sides.
HOLE_Y0 = 56.0              # nose end of the perforation (behind the keycap)
HOLE_Y1 = 124.0             # tail end of the perforation
HOLE_PITCH_Y = 9.5          # uniform row pitch along the length (mm)
HOLE_PITCH_T = 9.0          # uniform column pitch around the ring (deg)
HOLE_FLANK = 88.0           # holes go right down to the base plate
HOLE_TOP_GAP = 1.0          # deg: rib at the centre ridge
HOLE_GROW_MIN = 0.52        # smallest diamond fraction of the pitch
HOLE_GROW_MAX = 0.74        # largest diamond fraction of the pitch

HOLE_ROWS = int(HOLE_Y1 - HOLE_Y0) // int(HOLE_PITCH_Y) + 1
HOLE_COLS = int(HOLE_FLANK) // int(HOLE_PITCH_T) + 1


def _hole_diamond(row, col):
    """(yc, dy, dth) of the diamond at (row, col): uniform centres, growing
    size.  Returns the centre plus the *half* extents."""
    yc = HOLE_Y0 + row * HOLE_PITCH_Y
    grow = (row / max(1, HOLE_ROWS - 1)) * 0.55 \
        + (col / max(1, HOLE_COLS - 1)) * 0.45
    grow = _clamp01(grow)
    frac = HOLE_GROW_MIN + (HOLE_GROW_MAX - HOLE_GROW_MIN) * grow
    return yc, 0.5 * HOLE_PITCH_Y * frac, 0.5 * HOLE_PITCH_T * frac


def _hole_stagger(row, col):
    """Half-column offset on alternate rows (honeycomb)."""
    return (0.5 * HOLE_PITCH_T) if (row % 2) else 0.0


def _in_hole(y, th):
    """True when (y, |th|) falls inside a honeycomb diamond."""
    import bisect as _bisect
    ath = abs(th)
    if ath > HOLE_FLANK:
        return False
    r0 = int(round((y - HOLE_Y0) / HOLE_PITCH_Y))
    for r in (r0 - 1, r0, r0 + 1):
        if r < 0 or r >= HOLE_ROWS:
            continue
        c0 = int(round((ath - _hole_stagger(r, 0)) / HOLE_PITCH_T))
        for c in (c0 - 1, c0, c0 + 1):
            if c < 0 or c >= HOLE_COLS:
                continue
            yc, dy, dth = _hole_diamond(r, c)
            tc = c * HOLE_PITCH_T + _hole_stagger(r, c)
            if tc - dth < HOLE_TOP_GAP:
                continue
            if abs(y - yc) / dy + abs(ath - tc) / dth <= 1.0:
                return True
    return False


def inside_hole(y, th):
    """*th* is the angle from the top centre (deg); holes are diamonds on an
    evenly-placed staggered (honeycomb) lattice that grows in size toward the
    flank and the tail."""
    return _in_hole(y, th)


def _hole_centres():
    """Centres (y_mm, th_deg) of the honeycomb holes (both sides)."""
    out = []
    for r in range(HOLE_ROWS):
        for c in range(HOLE_COLS):
            yc, _dy, dth = _hole_diamond(r, c)
            tc = c * HOLE_PITCH_T + _hole_stagger(r, c)
            if tc - dth < HOLE_TOP_GAP or tc - dth > HOLE_FLANK:
                continue
            out.append((yc, tc))
            out.append((yc, -tc))
    return out


# ---------------------------------------------------------------------------
# Shell + base
# ---------------------------------------------------------------------------

def build_shell(mesh):
    ys = np.linspace(STATIONS[0][0], STATIONS[-1][0], NT + 1)
    phis = np.array([-math.pi + 2 * math.pi * k / NU for k in range(NU)])
    pts = [[ring_point(y, p) for p in phis] for y in ys]
    idx = [[mesh.add(p) for p in row] for row in pts]

    def th_top(i):
        ph = math.degrees(phis[i]) % 360.0
        d = (ph - 90.0 + 180.0) % 360.0 - 180.0
        if d <= 0.0:
            return -d
        return 180.0 - d

    def cell(j, i):
        """(is_hole, is_top) for the quad starting at (j, i)."""
        yc = 0.5 * (ys[j] + ys[j + 1])
        a0, a1 = th_top(i), th_top((i + 1) % NU)
        if abs(a0 - a1) > 90.0:
            return False, False
        tc = 0.5 * (a0 + a1)
        # holes wrap the whole upper shell, from the crown down the flanks to
        # the base plate (and under the keycap / lever edges).
        on_top = unit_v(phis[i]) > 0.06 and \
            unit_v(phis[(i + 1) % NU]) > 0.06
        return (on_top and inside_hole(yc, tc)), on_top

    mesh.start("shell")
    holes = []
    for j in range(NT):
        for i in range(NU):
            is_hole, on_top = cell(j, i)
            if is_hole:
                holes.append((j, i))
                continue
            mesh.quad(idx[j][i], idx[j][(i + 1) % NU],
                      idx[j + 1][(i + 1) % NU], idx[j + 1][i])

    # Explicit hole cells (recoverable rim loops) + clean outline quads.
    mesh.start("honeycomb")
    for j, i in holes:
        mesh.quad(idx[j][i], idx[j][(i + 1) % NU],
                  idx[j + 1][(i + 1) % NU], idx[j + 1][i])

    mesh.start("hole_rims")
    for r in range(HOLE_ROWS):
        for c in range(HOLE_COLS):
            for loop in _hole_rims(r, c):
                ids = [mesh.add((p[0], p[1], p[2] + 0.05)) for p in loop]
                mesh.quad(ids[0], ids[1], ids[2], ids[3])

    # end caps -- closed with the shell group so they never leak into the
    # hole-rim group (they used to be appended to "hole_rims" and showed up
    # as hundreds of stray "triangles" in the honeycomb render).
    mesh.start("shell")
    for row in (0, -1):
        y_end = ys[row]
        a, zb, zt = station_at(y_end)
        c = mesh.add((0.0, y_end, 0.5 * (zb + zt)))
        for i in range(NU):
            a_i = idx[row][i]
            b_i = idx[row][(i + 1) % NU]
            if row == 0:
                mesh.tri(c, a_i, b_i)
            else:
                mesh.tri(c, b_i, a_i)


def _hole_rims(row, col):
    """The two (left/right) diamond rim outlines for one honeycomb hole."""
    yc, dy, dth = _hole_diamond(row, col)
    tc0 = col * HOLE_PITCH_T + _hole_stagger(row, col)
    rims = []
    for sign in (-1.0, 1.0):
        tc = tc0
        pts = []
        ok = True
        for ddy, ddth in ((dy, 0.0), (0.0, dth), (-dy, 0.0), (0.0, -dth)):
            th = tc + ddth
            if th < HOLE_TOP_GAP or th > HOLE_FLANK:
                ok = False
                break
            p = ring_point(yc + ddy, math.radians(90.0 + sign * th))
            pts.append(p)
        if ok:
            rims.append(pts)
    return rims


def build_base(mesh):
    ys = np.linspace(STATIONS[0][0] + 1.0, STATIONS[-1][0] - 1.0, NT)
    mesh.start("base")
    ncol = 15
    rows = []
    for y in ys:
        a = half_width(y) * 0.985
        rows.append([mesh.add((x, y, 0.35))
                     for x in np.linspace(-a, a, ncol)])
    for j in range(len(ys) - 1):
        for k in range(ncol - 1):
            mesh.quad(rows[j][k], rows[j][k + 1],
                      rows[j + 1][k + 1], rows[j + 1][k])


# ---------------------------------------------------------------------------
# Main click buttons
# ---------------------------------------------------------------------------

BTN_Y0, BTN_Y1 = 3.5, 55.0

# Centre-split channel.  In front of the wheel slot the two caps meet at the
# centreline; from the wheel slot back they part into a channel that clears
# the scroll wheel and the CPI button, so the caps never overlap them.
SPLIT_Y0 = 9.0             # just before the wheel's front tip
SPLIT_WHEEL_U = 0.15       # centre half-gap just clearing the wheel flank
SPLIT_DPI_U = 0.12         # centre half-gap just clearing the CPI button
SPLIT_RAMP = 5.0           # mm to ease into the clearance
SPLIT_WHEEL_BACK = 38.0    # wheel slot ends (wheel top rear)
SPLIT_DPI_START = 45.0     # CPI channel becomes current


def _split_u(y):
    """Centre half-gap (fraction of the local half-width) at length *y*."""
    # nose ramp: the caps nearly meet at the centre at the very front
    nose = 0.02 + 0.10 * (1.0 - _clamp01((y - BTN_Y0) / 11.0))
    if y <= SPLIT_Y0:
        return nose
    # wheel ramp: part to clear the wheel slot
    t = _clamp01((y - SPLIT_Y0) / SPLIT_RAMP)
    wheel = nose + (SPLIT_WHEEL_U - nose) * t
    if y < SPLIT_WHEEL_BACK:
        return max(nose, wheel)
    # CPI ramp: close in a little to clear the CPI button
    t2 = _clamp01((y - SPLIT_WHEEL_BACK) / (SPLIT_DPI_START - SPLIT_WHEEL_BACK))
    return SPLIT_WHEEL_U + (SPLIT_DPI_U - SPLIT_WHEEL_U) * t2


def _btn_edges(y):
    """Inner/outer u bounds of the keycap at length *y* (rounded petal)."""
    ty = _clamp01((y - BTN_Y0) / 11.0)          # 0 at nose, 1 behind
    # outer edge: narrow at the nose, bulging out over the body
    outer = (0.30 + 0.56 * (1.0 - (1.0 - ty) ** 2)) * _clamp01(
        min(1.0, (BTN_Y1 - y) / 4.0))
    # inner edge: the centre split / wheel-and-CPI channel
    return _split_u(y), outer


def _btn_lift(y, u):
    inner, outer = _btn_edges(y)
    eu = min((u - inner) / 0.05, (outer - u) / 0.05, 1.0)
    ey = min((y - BTN_Y0) / 4.0, (BTN_Y1 - y) / 6.0, 1.0)
    return 0.85 * _clamp01(eu) ** 0.5 * _clamp01(ey) ** 0.5


def _emit_outline(mesh, name, loop):
    """Emit an ordered polygon as a triangle fan (clean boundary loop)."""
    mesh.start(name)
    ids = [mesh.add(p) for p in loop]
    c = mesh.add((sum(p[0] for p in loop) / len(loop),
                  sum(p[1] for p in loop) / len(loop),
                  sum(p[2] for p in loop) / len(loop)))
    for i in range(len(ids)):
        mesh.tri(c, ids[i], ids[(i + 1) % len(ids)])


def build_main_buttons(mesh):
    ny, nu = 24, 18
    for sign, name in ((1.0, "main_button_right"),
                       (-1.0, "main_button_left")):
        mesh.start(name)
        ys = np.linspace(BTN_Y0, BTN_Y1, ny)
        rows, pts = [], []
        for y in ys:
            inner, outer = _btn_edges(y)
            if outer <= inner + 0.02:
                outer = inner + 0.02
            row, rowp = [], []
            for u in np.linspace(inner, outer, nu):
                a = half_width(y)
                z = top_z(sign * u * a, y)
                if z is None:
                    z = station_at(y)[2]
                z += _btn_lift(y, u)
                p = (sign * u * a, y, z)
                rowp.append(p)
                row.append(mesh.add(p))
            rows.append(row)
            pts.append(rowp)
        for j in range(ny - 1):
            for i in range(nu - 1):
                if sign > 0:
                    mesh.quad(rows[j][i], rows[j][i + 1],
                              rows[j + 1][i + 1], rows[j + 1][i])
                else:
                    mesh.quad(rows[j][i], rows[j + 1][i],
                              rows[j + 1][i + 1], rows[j][i + 1])
        # Explicit ordered boundary (front -> outer -> rear -> inner) so the
        # extractor can keep the concave centre channel instead of hulling it.
        loop = [pts[0][i] for i in range(nu)]
        loop += [pts[j][-1] for j in range(1, ny)]
        loop += [pts[-1][i] for i in range(nu - 2, -1, -1)]
        loop += [pts[j][0] for j in range(ny - 2, 0, -1)]
        _emit_outline(mesh, name + "_outline", loop)


# ---------------------------------------------------------------------------
# Side controls (left flank)
# ---------------------------------------------------------------------------

def _rounded_rect(y0, y1, z0, z1, ry, rz, n=4):
    pts = []
    for cy, cz, a0 in ((y1 - ry, z1 - rz, 0.0),
                       (y0 + ry, z1 - rz, 90.0),
                       (y0 + ry, z0 + rz, 180.0),
                       (y1 - ry, z0 + rz, 270.0)):
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cy + ry * math.cos(a), cz + rz * math.sin(a)))
    return pts


def _clip_half(poly, zsplit, keep_below):
    """Sutherland-Hodgman clip of a convex polygon by the line z = zsplit.

    The shared cut edge is a straight segment, so clipping one rounded lever
    outline into an upper and a lower half makes the two halves tile exactly
    (no gap, no overlap) while keeping the rounded outer corners.
    """
    def inside(p):
        return p[1] <= zsplit if keep_below else p[1] >= zsplit

    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ia, ib = inside(a), inside(b)
        if ia:
            out.append(a)
        if ia != ib:
            dz = b[1] - a[1]
            t = 0.0 if abs(dz) < 1e-12 else (zsplit - a[1]) / dz
            out.append((a[0] + (b[0] - a[0]) * t, zsplit))
    return out


def build_side_controls(mesh):
    def patch(name, outline, lift=1.0):
        mesh.start(name)
        top = [mesh.add((left_x(y, z) - lift, y, z)) for y, z in outline]
        cy = sum(p[0] for p in outline) / len(outline)
        cz = sum(p[1] for p in outline) / len(outline)
        c = mesh.add((left_x(cy, cz) - lift, cy, cz))
        for i in range(len(top)):
            mesh.tri(c, top[i], top[(i + 1) % len(top)])

    # Front thumb trigger (the silver blade, low at the nose).  It starts
    # just behind the nose so it never dips below the rising shell bottom.
    patch("trigger", _rounded_rect(12.0, 42.0, 5.2, 11.0, 3.0, 2.0, n=5),
          lift=1.2)
    # Traditional forward / back thumb buttons (8 & 9), adjacent (no gap).
    patch("side_button_front", _rounded_rect(54.0, 75.0, 22.5, 28.5,
                                             1.8, 1.4, n=5), lift=1.0)
    patch("side_button_rear", _rounded_rect(75.0, 98.0, 22.5, 28.5,
                                            1.8, 1.4, n=5), lift=1.0)
    # Up / down flick rocker (10 & 11): ONE long physical lever above the
    # thumb pair, spanning both of them.  Its outline is a single rounded
    # lever, tapered down toward the tail to follow the shell, clipped at
    # mid-height into two exactly-tiling clickable halves.
    lever = _rounded_rect(54.0, 98.0, 28.6, 34.2, 2.6, 1.9, n=6)
    lever = [(y, z - 0.13 * max(0.0, y - 74.0)) for y, z in lever]
    patch("rocker_down", _clip_half(lever, 31.4, keep_below=True), lift=1.0)
    patch("rocker_up", _clip_half(lever, 31.4, keep_below=False), lift=1.0)


# ---------------------------------------------------------------------------
# Skates
# ---------------------------------------------------------------------------

def _skate_outline(y0, y1, hw_front, hw_mid, hw_back, n=32):
    hi, lo = [], []
    for k in range(n + 1):
        t = k / n
        w = (hw_front * (1 - t) ** 2 + 2 * hw_mid * (1 - t) * t
             + hw_back * t ** 2)
        hi.append((w, y0 + (y1 - y0) * t))
    for t, y in reversed(hi):
        lo.append((-t, y))
    return hi + lo


def _emit_flat(mesh, name, outline, z):
    mesh.start(name)
    top = [mesh.add((x, y, z)) for x, y in outline]
    c = mesh.add((0.0, sum(p[1] for p in outline) / len(outline), z))
    for i in range(len(top)):
        mesh.tri(c, top[i], top[(i + 1) % len(top)])


def build_skates(mesh):
    _emit_flat(mesh, "skate_front",
               _skate_outline(9.0, 34.5, 22.0, 30.5, 22.0), -0.4)
    _emit_flat(mesh, "skate_rear",
               _skate_outline(92.5, 119.0, 22.0, 30.5, 22.0), -0.4)
    cy = 64.4
    r_in, r_out = 5.2, 8.3
    mesh.start("skate_sensor")
    inner, outer = [], []
    for k in range(33):
        a = 2 * math.pi * k / 32
        inner.append(mesh.add((r_in * math.cos(a), cy + r_in * math.sin(a), -0.4)))
        outer.append(mesh.add((r_out * math.cos(a), cy + r_out * math.sin(a), -0.4)))
    for k in range(32):
        k2 = (k + 1) % 32
        mesh.quad(inner[k], outer[k], outer[k2], inner[k2])


# ---------------------------------------------------------------------------
# Small detail parts
# ---------------------------------------------------------------------------

def build_wheel(mesh):
    cy, cz, r, hw = 27.0, 24.0, 12.6, 4.5
    steps = 28
    mesh.start("wheel")
    rings = []
    for x in (-hw, hw):
        rings.append([mesh.add((x,
                                cy + r * math.cos(2 * math.pi * k / steps),
                                cz + r * math.sin(2 * math.pi * k / steps)))
                      for k in range(steps)])
    for k in range(steps - 1):
        mesh.quad(rings[0][k], rings[0][k + 1],
                  rings[1][k + 1], rings[1][k])


def build_dpi_button(mesh):
    mesh.start("dpi_button")
    pts = []
    for x, y in ((-3.2, 46.0), (3.2, 46.0), (3.2, 62.0), (-3.2, 62.0)):
        z = top_z(x, y)
        z = (z if z is not None else 38.5) + 0.8
        pts.append(mesh.add((x, y, z)))
    mesh.quad(*pts)


def build_sensor(mesh):
    mesh.start("sensor")
    c = mesh.add((0.0, 64.4, 0.0))
    ring = [mesh.add((5.2 * math.cos(2 * math.pi * k / 16),
                      64.4 + 5.2 * math.sin(2 * math.pi * k / 16), 0.0))
            for k in range(16)]
    for k in range(16):
        mesh.tri(c, ring[k], ring[(k + 1) % 16])


def build_switch(mesh):
    mesh.start("onoff_switch")
    pts = [mesh.add((x, y, 0.0)) for x, y in
           ((6.5, 70.7), (9.5, 70.7), (9.5, 78.7), (6.5, 78.7))]
    mesh.quad(*pts)


def build_usb_c(mesh):
    mesh.start("usb_c")
    pts = [mesh.add((x, 0.6, z)) for x, z in
           ((-4.45, 7.85), (4.45, 7.85), (4.45, 11.05), (-4.45, 11.05))]
    mesh.quad(*pts)


def build_rgb_strip(mesh):
    mesh.start("rgb_strip")
    ys = np.linspace(45.0, 122.0, 26)
    for sign in (-1.0, 1.0):
        for j in range(len(ys) - 1):
            a0 = half_width(ys[j]); a1 = half_width(ys[j + 1])
            y0, y1 = ys[j], ys[j + 1]
            p0 = mesh.add((sign * a0 * 0.90, y0, 1.2))
            p1 = mesh.add((sign * a0 * 0.90, y0 + 0.8, 1.2))
            p2 = mesh.add((sign * a1 * 0.90, y1 - 0.8, 1.2))
            p3 = mesh.add((sign * a1 * 0.90, y1, 1.2))
            mesh.quad(p0, p1, p2, p3)


# ---------------------------------------------------------------------------

def build():
    m = Mesh()
    build_shell(m)
    build_base(m)
    build_main_buttons(m)
    build_side_controls(m)
    build_skates(m)
    build_wheel(m)
    build_dpi_button(m)
    for fn in (build_sensor, build_switch, build_usb_c, build_rgb_strip):
        fn(m)
    return m


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.abspath(__file__))
    os.makedirs(outdir, exist_ok=True)
    m = build()
    obj = os.path.join(outdir, "aerox5_detailed.obj")
    stl = os.path.join(outdir, "aerox5_detailed.stl")
    m.write_obj(obj)
    m.write_stl(stl)
    V = np.array(m.verts)
    print("verts %d faces %d" % (len(m.verts),
                                 sum(len(f) for _n, f in m.groups)))
    print("groups:", ", ".join(n for n, _f in m.groups))
    print("bbox mm:", V.min(0).round(2), V.max(0).round(2))
    print("wrote", obj, "and", stl)


if __name__ == "__main__":
    main()
