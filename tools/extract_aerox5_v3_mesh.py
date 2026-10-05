#!/usr/bin/env python3
"""Extract the compact Aerox 5 dataset from the v3 reconstruction.

Reads ``aerox5_3d_files/aerox5_v3.obj`` (the detailed, dimensioned model
built by ``aerox5_3d_files/model.py``) and regenerates ``aerox5_mesh.py``,
the pure-data module the GUI renderer imports as ``_mesh``.

The v3 model differs from the older procedural one the dataset used to come
from (``tools/extract_aerox5_mesh.py`` reads ``build/aerox5_detailed.obj``):

* the honeycomb is *cut into* a watertight shell, so the hole rims have to
  be recovered from sharp edges rather than from a ``honeycomb`` group;
* the keycaps and side buttons are baked into the shell and carry no
  separate ordered-outline groups, so a click-panel outline is its group's
  own free-edge rim -- the part line, but rasterised onto the lattice, so it
  is de-staircased before use (see CAP_RIM_*); the side-button caps are their
  group's rim too, decimated to SIDE_CAP_PTS;
* the whole model is mirrored (thumb side at +X), so X is negated once here
  to match the module convention (thumb side at -X).

Coordinates written to ``aerox5_mesh.py`` are normalised: mouse length 1.0,
origin at the body centre, x right / y back / z up.

    python3 tools/extract_aerox5_v3_mesh.py [path-to-aerox5_v3.obj]

Requires numpy (build-time only; the generated module is pure data).
"""

from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJ_DEFAULT = os.path.join(REPO_ROOT, "aerox5_3d_files", "aerox5_v3.obj")
OBJ_PATH = sys.argv[1] if len(sys.argv) > 1 else OBJ_DEFAULT
OUT_PATH = os.path.join(REPO_ROOT, "aerox5_mesh.py")

RING_COUNT = 26          # cross-section slices along the length
RING_STEPS = 48          # uniform arc-length resample per ring
GRID_STEP = 2.0          # mm, for both lookup grids
#: Repair of the top-surface grid (see the note where GRID_TOP is built).
GRID_FILL = 1            # nodes, grey-scale closing radius (single-cell notches)
GRID_SMOOTH = 1          # nodes, box mean over the repaired grid
SEAM_MAX_PTS = 256       # decimation of the shell/base rim loop
CAP_MAX_PTS = 160        # decimation of a keycap outline
SIDE_CAP_PTS = 64        # decimation of a side-button outline
RIM_PTS = 24             # decimation of one honeycomb hole

#: Superseded click-keycap outline tracing: each panel's own free-edge rim off
#: the OBJ.  It is unused, because the rim is *per panel* and this model breaks
#: its flank asymmetrically -- the right cap's rim runs out to 31.8 mm against
#: the left's 27.6 mm -- so the pair comes out asymmetric (the right keycap
#: 4.2 mm wider through the shoulder).  The rim is also a lattice staircase
#: and had to be de-staircased.  Kept for reference.
CAP_RIM_RESAMPLE = 200   # points, first arc-length resample of the raw rim
CAP_RIM_WIN = 5          # points per side, circular mean (de-staircase)
CAP_RIM_PTS = 128        # points, final keycap outline

#: Click-keycap part line: the cap's *upward* faces masked in plan view, the
#: row extremes swept, and both panels drawn from the LEFT one's curve,
#: mirrored about the centreline (see the block in :func:`main`).  That is what
#: makes the pair symmetric and what keeps the centre channel -- the centred
#: wheel well -> spine -> CPI pocket -- shared and centred.
CAP_RASTER = 0.08        # mm, raster cell of the cap-top mask
CAP_MODE_WIN = 0.9       # mm, majority filter over the mask (feather comb)
CAP_NZ = 0.25            # min |nz| for a face to count as "cap top"
CAP_GAP = 5.0            # mm, widest row gap bridged (honeycomb bites)
#: Median then mean window on the row extremes.  The median has to clear the
#: ~1.5 mm feather comb the mask keeps along the inner edge, or the outline
#: comes back with a sawtooth; the mean then takes the raster steps out.
CAP_SMOOTH = (1.6, 1.2)
#: |x| band that sees the centre spine (see spine_halfwidth) without catching
#: the cap bodies, whose inner edge runs ~5 mm out along the wheel well.
SPINE_WIN = 3.0

#: Outer-skin classification for the watertight shell (see outer_skin_free_edges).
SKIN_BAND = 2.0          # mm, width of the per-y spine bins
SKIN_COS = 0.55          # min dot(normal, radial) for a face to count as skin
RIM_MAX_EXT = 8.0        # mm, largest bbox dimension of a honeycomb hole
RIM_MIN_EXT = 0.3        # mm, smallest bbox dimension (drops degenerate slivers)
RIM_MIN_Z = 6.0          # mm, drops the base plate's underside diamond lattice
RIM_Y_RANGE = (20.0, 125.0)   # mm, drops nose / tail end-cap loops

#: The 19 groups the v3 model emits.  A mismatch means the source changed.
EXPECTED_GROUPS = {
    "base", "click_left", "click_right", "dpi_button", "rgb_strip",
    "sensor_lens", "sensor_ring", "shell", "side_button_1", "side_button_2",
    "side_button_silver", "side_lever", "skate_front", "skate_rear",
    "switch_knob", "switch_slot", "thumb_pad", "usb_c_port", "wheel",
}

# Groups whose vertices lie on the visible top surface (height grid).
TOP_GROUPS = ("shell", "click_left", "click_right", "rgb_strip")
# Groups whose vertices lie on the (thumb-side) flank (side lookup grid).
SIDE_GROUPS = ("shell", "side_button_1", "side_button_2",
               "side_button_silver", "side_lever", "thumb_pad")


# ---------------------------------------------------------------------------
# OBJ loading
# ---------------------------------------------------------------------------

def load_obj(path):
    verts = []
    faces_by_group = defaultdict(list)
    group = None
    with open(path) as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("g "):
                group = line.split()[1]
            elif line.startswith("f "):
                faces_by_group[group].append(
                    [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
                )
    return np.array(verts, dtype=np.float64), faces_by_group


def triangles(faces):
    """Fan-triangulate a mixed list of triangle / quad / n-gon faces."""
    tris = []
    for f in faces:
        for k in range(1, len(f) - 1):
            tris.append((f[0], f[k], f[k + 1]))
    return tris


# ---------------------------------------------------------------------------
# Cross-section rings (plane-triangle intersection + hull + resample)
# ---------------------------------------------------------------------------

def slice_ring(v0, v1, v2, y):
    """Intersect triangles with the plane Y=y; collect the XZ points."""
    d0, d1, d2 = v0[:, 1] - y, v1[:, 1] - y, v2[:, 1] - y
    pts = []
    for da, db, a, b in ((d0, d1, v0, v1), (d1, d2, v1, v2),
                         (d2, d0, v2, v0)):
        mask = (da * db) < 0.0          # strict crossing -> no duplicates
        if not np.any(mask):
            continue
        t = da[mask] / (da[mask] - db[mask])
        seg = a[mask] + (b[mask] - a[mask]) * t[:, None]
        pts.append(seg[:, [0, 2]])
    if not pts:
        return np.empty((0, 2))
    return np.vstack(pts)


def convex_hull(xz):
    """XZ convex hull (Andrew's monotone chain), CCW, no repeated start."""
    pts = sorted({(float(x), float(z)) for x, z in xz})
    if len(pts) < 3:
        return np.asarray(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1])


def arclength_resample(xz, steps):
    """Uniformly resample a closed XZ loop by arc length."""
    n = len(xz)
    d = np.linalg.norm(np.roll(xz, -1, axis=0) - xz, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    total = cum[-1]
    tgt = np.linspace(0.0, total, steps, endpoint=False)
    pts = []
    seg = 0
    for t in tgt:
        while seg < n - 1 and cum[seg + 1] < t:
            seg += 1
        a, b = xz[seg], xz[(seg + 1) % n]
        f = 0.0 if d[seg] <= 0 else (t - cum[seg]) / d[seg]
        pts.append(a + (b - a) * f)
    return np.asarray(pts)


def resample_closed3d(pts, steps):
    """Uniformly resample a closed 3D loop by arc length to *steps* points."""
    pts = np.asarray(pts, dtype=np.float64)
    n = len(pts)
    if n <= steps:
        return pts
    d = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    total = cum[-1]
    if total <= 0:
        return pts
    tgt = np.linspace(0.0, total, steps, endpoint=False)
    out = []
    seg = 0
    for t in tgt:
        while seg < n - 1 and cum[seg + 1] < t:
            seg += 1
        a, b = pts[seg], pts[(seg + 1) % n]
        f = 0.0 if d[seg] <= 0 else (t - cum[seg]) / d[seg]
        out.append(a + (b - a) * f)
    return np.asarray(out)


def extract_rings(verts, faces, ymin, ymax):
    tris = np.asarray(triangles(faces), dtype=np.int64)
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    ys = np.linspace(ymin + 3.5, ymax - 2.5, RING_COUNT)
    rings = []
    start_idx = None
    for y in ys:
        xz = slice_ring(v0, v1, v2, y)
        if len(xz) < RING_STEPS:
            continue
        hull = convex_hull(xz)
        if start_idx is None:
            start_idx = int(np.argmin(hull[:, 0]))
        hull = np.roll(hull, -start_idx, axis=0)
        rings.append((float(y), arclength_resample(hull, RING_STEPS)))
    return rings


# ---------------------------------------------------------------------------
# Edge / loop machinery
# ---------------------------------------------------------------------------

def build_edge_map(tris, nverts):
    """Return manifold edge pairs (two triangles per edge) and free edges.

    Returns ``(epairs, tri_pairs, free_edges)`` where ``epairs[k]`` is the
    (sorted) vertex pair of a manifold edge and ``tri_pairs[k]`` the two
    triangle indices that share it; ``free_edges`` is an (F, 2) array of
    boundary edges belonging to a single triangle.
    """
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.sort(e, axis=1)
    owner = np.tile(np.arange(len(tris)), 3)
    uniq, inv, counts = np.unique(e, axis=0, return_inverse=True,
                                  return_counts=True)
    order = np.argsort(inv, kind="stable")
    inv_s, owner_s, e_s = inv[order], owner[order], e[order]
    man = counts[inv_s] == 2
    sel = np.where(man)[0]
    pairs = sel.reshape(-1, 2)
    epairs = e_s[sel].reshape(-1, 2, 2)[:, 0, :]
    tri_pairs = np.stack([owner_s[pairs[:, 0]], owner_s[pairs[:, 1]]], axis=1)
    free = e_s[counts[inv_s] == 1]
    return epairs, tri_pairs, free


def tri_normals(verts, tris):
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1)
    return n / np.maximum(ln, 1e-12)[:, None]


def components(edges):
    """Connected components of an undirected edge list -> list of edge arrays."""
    adj = defaultdict(list)
    for a, b in edges:
        adj[int(a)].append(int(b))
        adj[int(b)].append(int(a))
    seen, comps = set(), []
    for start in list(adj):
        if start in seen:
            continue
        stack, nodes = [start], set([start])
        seen.add(start)
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    nodes.add(nb)
                    stack.append(nb)
        local = [e for e in edges if int(e[0]) in nodes and int(e[1]) in nodes]
        comps.append((nodes, np.asarray(local)))
    return adj, comps


def order_chain(nodes, adj):
    """Order a path/cycle's vertices (start at a degree-1 end when there is one)."""
    ends = [v for v in nodes if len(adj[v]) == 1]
    start = ends[0] if ends else min(nodes)
    visited = {start}
    order = [start]
    cur = start
    while True:
        nxt = [q for q in adj[cur] if q not in visited]
        if not nxt:
            break
        cur = nxt[0]
        visited.add(cur)
        order.append(cur)
    return order


def circ_mean(pts, win):
    """Wrap-around moving mean over a closed loop, *win* points per side."""
    pts = np.asarray(pts, dtype=np.float64)
    if win <= 0 or len(pts) < 2 * win + 1:
        return pts
    out = np.zeros_like(pts)
    for k in range(-win, win + 1):
        out += np.roll(pts, k, axis=0)
    return out / float(2 * win + 1)


def loops_from_edges(edges, min_len=4):
    """All ordered loops/patches in an edge set, longest first."""
    adj, comps = components(edges)
    out = []
    for nodes, local in comps:
        order = order_chain(nodes, adj)
        if len(order) >= min_len:
            out.append(order)
    out.sort(key=len, reverse=True)
    return out


def outer_skin_free_edges(verts, tris, shell_v, band=SKIN_BAND,
                          cos_min=SKIN_COS):
    """Boundary edges between the shell's outer skin and its hole walls.

    The v3 shell is watertight -- every perforation is closed by wall faces --
    so plain free-edge extraction finds no rims at all.  A triangle counts as
    *skin* when its normal points away from the body's local spine (the
    midpoint of the shell's z span at that y); the free edges of that set are
    then exactly the hole outlines.  Returns ``(edges, n_skin_faces)``.
    """
    sv = verts[shell_v]
    yb = np.floor(sv[:, 1] / band).astype(np.int64)
    zc = np.full(int(yb.max()) + 2, np.nan)
    for b in np.unique(yb):
        z = sv[yb == b, 2]
        zc[b] = 0.5 * (z.min() + z.max())
    zc = np.where(np.isnan(zc), np.nanmedian(zc), zc)

    cen = verts[tris].mean(axis=1)
    n = tri_normals(verts, tris)
    bi = np.clip(np.floor(cen[:, 1] / band).astype(np.int64), 0, len(zc) - 1)
    radial = np.stack([cen[:, 0], np.zeros(len(cen)), cen[:, 2] - zc[bi]], axis=1)
    radial /= np.maximum(np.linalg.norm(radial, axis=1), 1e-9)[:, None]
    skin = np.einsum("ij,ij->i", n, radial) > cos_min

    sk = tris[skin]
    e = np.concatenate([sk[:, [0, 1]], sk[:, [1, 2]], sk[:, [2, 0]]])
    e = np.sort(e, axis=1)
    uniq, _, counts = np.unique(e, axis=0, return_inverse=True,
                                return_counts=True)
    return uniq[counts == 1], int(skin.sum())


# ---------------------------------------------------------------------------
# Lookup grids (bilinear; dense arrays)
# ---------------------------------------------------------------------------

def splat_grid(xs, ys, vs, x_edges, y_edges, mode):
    """Assign each sample to its nearest grid node; combine per node."""
    nx, ny = len(x_edges) - 1, len(y_edges) - 1
    grid = np.full((ny, nx), np.nan)
    ix = np.clip(np.digitize(xs, x_edges) - 1, 0, nx - 1)
    iy = np.clip(np.digitize(ys, y_edges) - 1, 0, ny - 1)
    sums = defaultdict(list)
    for a, b, v in zip(iy, ix, vs):
        sums[(a, b)].append(v)
    for (a, b), vals in sums.items():
        grid[a, b] = np.max(vals) if mode == "max" else np.min(vals)
    for _ in range(8):
        missing = np.isnan(grid)
        if not missing.any():
            break
        padded = np.pad(grid, 1, mode="edge")
        neigh = np.stack([padded[:-2, 1:-1], padded[2:, 1:-1],
                          padded[1:-1, :-2], padded[1:-1, 2:]])
        seen = np.isfinite(neigh).sum(axis=0)
        neigh = np.where(seen, np.nansum(neigh, axis=0) / np.maximum(seen, 1),
                         np.nan)
        grid = np.where(missing, neigh, grid)
    fallback = np.nanmedian(grid)
    return np.where(np.isnan(grid), fallback, grid)


def extreme_filter(field, r, mode):
    """Grey-scale max/min over a ``(2r+1)``-square window (edge-clamped)."""
    out = field
    for _ in range(r):
        p = np.pad(out, 1, mode="edge")
        stack = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        out = stack.max(axis=0) if mode == "max" else stack.min(axis=0)
    return out


def box_mean_field(field, r):
    """Separable ``(2r+1)`` box mean over a float field (edge-clamped)."""
    out = field
    for axis in (0, 1):
        pad = [(r, r), (0, 0)] if axis == 0 else [(0, 0), (r, r)]
        ker = np.ones(2 * r + 1) / float(2 * r + 1)
        out = np.apply_along_axis(
            lambda v: np.convolve(v, ker, mode="valid"), axis,
            np.pad(out, pad, mode="edge"))
    return out


def cap_outline(verts, faces, axis, lift=0.6):
    """Ordered convex outline of a physical button group (flat projection)."""
    idx = sorted({i for f in faces for i in f})
    P = verts[np.asarray(idx)]
    if len(P) < 3:
        return P
    o1, o2 = [a for a in (0, 1, 2) if a != axis]
    hull = convex_hull(P[:, [o1, o2]])
    center = P.mean(0)
    sign = -1.0 if center[axis] < 0 else 1.0
    out = np.zeros((len(hull), 3))
    out[:, o1] = hull[:, 0]
    out[:, o2] = hull[:, 1]
    out[:, axis] = center[axis] + sign * lift
    return out


def box_mean(mask, r):
    """Fraction of each (2r+1)^2 window that is set, via an integral image."""
    k = 2 * r + 1
    s = np.pad(mask.astype(np.float32), k, mode="constant").cumsum(0).cumsum(1)
    h, w = mask.shape
    return (s[k:k + h, k:k + w] - s[:h, k:k + w]
            - s[k:k + h, :w] + s[:h, :w]) / float(k * k)


def row_runs(row, x0, step, gap):
    """Merged x intervals set in one mask row, bridging gaps up to *gap* mm."""
    c = np.where(row)[0]
    if c.size == 0:
        return []
    br = np.where(np.diff(c) * step > gap)[0]
    starts = np.concatenate([[0], br + 1])
    ends = np.concatenate([br, [c.size - 1]])
    return [(x0 + (c[a] + 0.5) * step, x0 + (c[b] + 0.5) * step)
            for a, b in zip(starts, ends)]


def sliding_median(v, k):
    if k <= 1:
        return v
    p = np.pad(v, k, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(p, 2 * k + 1)
    return np.median(win, axis=-1)


def sliding_mean(v, k):
    if k <= 1:
        return v
    ker = np.ones(2 * k + 1) / float(2 * k + 1)
    return np.convolve(np.pad(v, k, mode="edge"), ker, mode="valid")


def cap_top_raw(verts, tris, step=CAP_RASTER, nz=CAP_NZ):
    """Rasterise the plan-view footprint of a keycap's upward faces.

    The cap top carries the lattice feather, so the group boundary is a
    staircase comb with the honeycomb bitten out of it; the *top* faces
    alone (normal up -- the model winds the cap tops inward) recover the
    smooth part line once the comb is filtered off (see cap_top_mask).

    Returns ``(mask, origin_xy, top_vertex_array)``.
    """
    n = tri_normals(verts, tris)
    top = tris[n[:, 2] < -nz]
    top_v = verts[np.unique(top.ravel())] if len(top) else np.empty((0, 3))
    P = verts[top]
    if len(P) == 0:
        return np.zeros((0, 0), bool), np.zeros(2), top_v
    lo = P[:, :, :2].min(axis=1).min(axis=0) - step
    hi = P[:, :, :2].max(axis=1).max(axis=0) + step
    w = int(math.ceil((hi[0] - lo[0]) / step)) + 1
    h = int(math.ceil((hi[1] - lo[1]) / step)) + 1
    m = np.zeros((h, w), dtype=bool)
    for t in P:
        ax, ay = t[0, 0], t[0, 1]
        bx, by = t[1, 0], t[1, 1]
        cx, cy = t[2, 0], t[2, 1]
        det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(det) < 1e-12:
            continue
        x0 = max(int(np.floor((min(ax, bx, cx) - lo[0]) / step)), 0)
        x1 = min(int(np.ceil((max(ax, bx, cx) - lo[0]) / step)), w - 1)
        y0 = max(int(np.floor((min(ay, by, cy) - lo[1]) / step)), 0)
        y1 = min(int(np.ceil((max(ay, by, cy) - lo[1]) / step)), h - 1)
        if x1 < x0 or y1 < y0:
            continue
        xs = lo[0] + (np.arange(x0, x1 + 1) + 0.5) * step
        ys = lo[1] + (np.arange(y0, y1 + 1) + 0.5) * step
        X, Y = np.meshgrid(xs, ys)          # barycentric half-plane test
        l1 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / det
        l2 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / det
        l3 = 1.0 - l1 - l2
        m[y0:y1 + 1, x0:x1 + 1] |= (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    return m, lo, top_v


def cap_top_mask(verts, tris, step=CAP_RASTER, win=CAP_MODE_WIN, nz=CAP_NZ):
    """Cap-top footprint with the feather comb and honeycomb rims taken off.

    A majority filter over ``win`` mm, or the raw mask when ``win <= 0``.
    """
    m, lo, top_v = cap_top_raw(verts, tris, step, nz)
    if win <= 0 or m.size == 0:
        return m, lo, top_v
    r = max(1, int(round(win / step / 2)))
    return box_mean(m, r) >= 0.5, lo, top_v


def spine_halfwidth(row, x0, step, win=SPINE_WIN):
    """|x| of the cap-top cell nearest the centreline in one raw mask row.

    Between the wheel well and the CPI pocket the two click panels abut along
    a crowned strip a few tenths of a millimetre across, so each one's part
    line pinches right in to it.  That strip is narrower than the majority
    window -- which exists to take the *outer* boundary's feather comb off --
    so it is read off the unfiltered mask.  ``inf`` where the row has no
    material near the centre, i.e. where the real boundary is the outer edge.
    """
    c = np.where(row)[0]
    if c.size == 0:
        return np.inf
    x = np.abs(x0 + (c + 0.5) * step)
    x = x[x < win]
    return float(x.min()) if x.size else np.inf


def cap_plan_curves(verts, tris, step=CAP_RASTER, win=CAP_MODE_WIN,
                    nz=CAP_NZ, gap=CAP_GAP, smooth=CAP_SMOOTH):
    """Per-station inner/outer |x| of one keycap, in absolute mm.

    Both caps are described the same way (x folded to |x|) so they can feed
    a shared curve.  The outer edge is the filtered mask's boundary; the
    inner one is pulled in to the centre spine wherever the cap reaches it
    (see spine_halfwidth), so the part line follows the well wall -> spine
    -> CPI pocket the way the part does.  Returns
    ``(y, x_in, x_out, top_vertices)`` or ``None``.
    """
    m_raw, lo, top_v = cap_top_raw(verts, tris, step, nz)
    if m_raw.size == 0:
        return None
    r = max(1, int(round(win / step / 2)))
    m = box_mean(m_raw, r) >= 0.5 if win > 0 else m_raw
    rows = np.where(m.any(axis=1))[0]
    if len(rows) < 8:
        return None
    ys, xs_in, xs_out = [], [], []
    prev = None
    for r in rows:
        runs = row_runs(m[r], lo[0], step, gap)
        if not runs:
            continue
        if prev is None:                    # seed on the widest run
            a, b = max(runs, key=lambda ab: ab[1] - ab[0])
        else:                               # then follow it row to row,
            mid = 0.5 * (prev[0] + prev[1])  # so stray specks are ignored
            a, b = min(runs, key=lambda ab: abs(0.5 * (ab[0] + ab[1]) - mid))
        prev = (a, b)
        ys.append(lo[1] + (r + 0.5) * step)
        xs_in.append(min(min(abs(a), abs(b)),
                         spine_halfwidth(m_raw[r], lo[0], step)))
        xs_out.append(max(abs(a), abs(b)))
    ys = np.asarray(ys)
    full = np.arange(ys[0], ys[-1] + step * 0.5, step)
    x_in = np.interp(full, ys, np.asarray(xs_in))
    x_out = np.interp(full, ys, np.asarray(xs_out))
    k_med = max(1, int(round(smooth[0] / step / 2)))
    k_avg = max(1, int(round(smooth[1] / step / 2)))
    x_in = sliding_mean(sliding_median(x_in, k_med), k_avg)
    x_out = sliding_mean(sliding_median(x_out, k_med), k_avg)
    return full, x_in, x_out, top_v


def cap_loop(sign, y, x_in, x_out, top_v, steps=CAP_MAX_PTS):
    """Closed keycap loop from a plan curve: inner edge up, outer edge down."""
    P = np.concatenate([np.stack([sign * x_in, y], axis=1),
                        np.stack([sign * x_out, y], axis=1)[::-1]])
    if len(top_v):                          # z: nearest cap-top vertex
        z = np.empty(len(P))
        for i in range(0, len(P), 128):     # chunked: the vertex set is big
            d = ((top_v[None, :, :2] - P[i:i + 128, None, :2]) ** 2).sum(2)
            z[i:i + 128] = top_v[np.argmin(d, axis=1), 2] + 0.5
    else:
        z = np.zeros(len(P))
    return resample_closed3d(np.column_stack([P, z]), steps)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    verts, faces_by_group = load_obj(OBJ_PATH)
    verts = verts.copy()
    verts[:, 0] *= -1.0            # module convention: thumb side at -x

    got = {g for g, f in faces_by_group.items() if f}
    if got != EXPECTED_GROUPS:
        raise SystemExit("group mismatch: missing %s, extra %s"
                         % (sorted(EXPECTED_GROUPS - got),
                            sorted(got - EXPECTED_GROUPS)))

    # Global triangle array carrying each triangle's group id.
    names = sorted(faces_by_group)
    gid_of = {n: i for i, n in enumerate(names)}
    tri_arrays, tri_gid = [], []
    for n in names:
        t = triangles(faces_by_group[n])
        if t:
            tri_arrays.append(np.asarray(t, dtype=np.int64))
            tri_gid.append(np.full(len(t), gid_of[n]))
    tris_all = np.vstack(tri_arrays)
    gid_all = np.concatenate(tri_gid)

    shell_v = np.unique(np.asarray(triangles(faces_by_group["shell"])).ravel())
    ymin = float(verts[shell_v, 1].min())
    ymax = float(verts[shell_v, 1].max())
    length = ymax - ymin
    print("shell y %.2f..%.2f  length %.2f mm  width %.2f  height %.2f"
          % (ymin, ymax, length, np.ptp(verts[shell_v, 0]),
             np.ptp(verts[shell_v, 2])))

    def nrm(pts):
        pts = np.asarray(pts, dtype=np.float64).copy()
        pts[:, 0] /= length
        pts[:, 1] = (pts[:, 1] - ymin) / length - 0.5
        pts[:, 2] /= length
        return pts

    epairs, tri_pairs, free = build_edge_map(tris_all, len(verts))
    normals = tri_normals(verts, tris_all)
    print("edges %d manifold %d free %d" % (len(epairs), len(epairs), len(free)))

    def group_tris(name):
        m = gid_all == gid_of[name]
        return tris_all[m]

    def group_free_edges(name, min_len=4):
        m = gid_all == gid_of[name]
        owner_tris = set(np.where(m)[0])
        # free edges whose single triangle belongs to this group
        e = np.concatenate([tris_all[m][:, [0, 1]], tris_all[m][:, [1, 2]],
                            tris_all[m][:, [2, 0]]])
        e = np.sort(e, axis=1)
        uniq, inv, counts = np.unique(e, axis=0, return_inverse=True,
                                      return_counts=True)
        return uniq[counts == 1]

    # -- rings ---------------------------------------------------------------
    all_faces = [f for n in names for f in faces_by_group[n]]
    rings_mm = extract_rings(verts, all_faces, ymin, ymax)
    print("rings: %d x %d" % (len(rings_mm), RING_STEPS))

    # -- honeycomb rims ------------------------------------------------------
    # The v3 shell is watertight, so the rims come from the skin/wall boundary
    # (see outer_skin_free_edges), not from raw free edges.  The base plate's
    # underside diamond lattice falls out of the same construction; the
    # centroid-height cut drops it.
    skin_edges, n_skin = outer_skin_free_edges(verts, tris_all, shell_v)
    adj_all, comps_all = components(skin_edges)
    rims = []
    rejected = defaultdict(int)
    for nodes, local in comps_all:
        if len(nodes) < 6 or any(len(adj_all[v]) != 2 for v in nodes):
            rejected["not-closed"] += 1
            continue
        P = verts[order_chain(nodes, adj_all)]
        ext = P.max(0) - P.min(0)
        cen = P.mean(0)
        if not (6 <= len(nodes) <= 64):
            rejected["n"] += 1
            continue
        if ext.max() > RIM_MAX_EXT or ext.min() < RIM_MIN_EXT:
            rejected["size"] += 1
            continue
        if cen[2] < RIM_MIN_Z:
            rejected["low"] += 1
            continue
        if not (RIM_Y_RANGE[0] <= cen[1] <= RIM_Y_RANGE[1]):
            rejected["station"] += 1
            continue
        rims.append(resample_closed3d(P, RIM_PTS))
    print("rims: %d  (skin faces %d, rejected %s)"
          % (len(rims), n_skin, dict(rejected)))

    # -- base seam -----------------------------------------------------------
    base_free = group_free_edges("base")
    base_loops = loops_from_edges(base_free, min_len=8)
    seam = None
    if base_loops:
        seam = verts[np.asarray(base_loops[0])]
        seam = resample_closed3d(seam, SEAM_MAX_PTS)
    print("base seam: %s" % ("yes" if seam is not None else "no"))

    # -- caps ----------------------------------------------------------------
    caps = {}

    # Thumb buttons + trigger: ordered free-edge outlines.
    for key, grp in (("button4", "side_button_2"), ("button5", "side_button_1"),
                     ("button9", "side_button_silver")):
        loops = loops_from_edges(group_free_edges(grp), min_len=8)
        P = verts[np.asarray(loops[0])] if loops else np.empty((0, 3))
        caps[key] = resample_closed3d(P, SIDE_CAP_PTS)
        print("%s (%s): y %.1f..%.1f z %.1f..%.1f x %.1f..%.1f n=%d"
              % (key, grp, P[:, 1].min(), P[:, 1].max(), P[:, 2].min(),
                 P[:, 2].max(), P[:, 0].min(), P[:, 0].max(), len(caps[key])))

    # DPI pill (top, planar).
    caps["button6"] = cap_outline(verts, faces_by_group["dpi_button"],
                                  axis=2, lift=0.6)

    # Lever: one rocker patch split into upper/lower Z bands (each spans the
    # full lever length in Y, disjoint in side view).  button7 = upper,
    # button8 = lower -- the mapping _cap_on_flank() deliberately swaps.
    lever_loops = loops_from_edges(group_free_edges("side_lever"), min_len=8)
    P = verts[np.asarray(lever_loops[0])]
    zmid = (P[:, 2].min() + P[:, 2].max()) / 2.0
    up = P[P[:, 2] > zmid]
    lo = P[P[:, 2] <= zmid]
    caps["button7"] = resample_closed3d(up, SIDE_CAP_PTS)
    caps["button8"] = resample_closed3d(lo, SIDE_CAP_PTS)
    print("lever: y %.1f..%.1f z %.1f..%.1f  (up n=%d, lo n=%d)"
          % (P[:, 1].min(), P[:, 1].max(), P[:, 2].min(), P[:, 2].max(),
             len(up), len(lo)))

    # Click keycaps: the part line traced from a mask of the cap's *top*
    # faces.  The group boundary itself is the lattice staircase (feather
    # band + honeycomb rims), so the raw trace comes out wavy and scalloped.
    # The two panels are one symmetrical pair on the mouse, but this model's
    # right-hand cap carries a wider shoulder (the source model breaks its
    # flank asymmetrically), so both panels are drawn from the *left* one's
    # part line, mirrored about the centreline, sharing its inner edge: the
    # centre channel (wheel well -> spine -> CPI pocket) is a centred
    # feature.  Heights stay per side, from that side's own top faces.
    left = cap_plan_curves(verts, group_tris("click_left"))
    right = cap_plan_curves(verts, group_tris("click_right"))
    if left is None or right is None:
        raise SystemExit("no top-face mask for the click keycaps")
    y, x_in, x_out, ltv = left
    rtv = right[3]
    for key, sign, ctv in (("main_button_left", -1.0, ltv),
                           ("main_button_right", 1.0, rtv)):
        caps[key] = cap_loop(sign, y, x_in, x_out, ctv)
        P = caps[key]
        print("%s: x %.1f..%.1f y %.1f..%.1f z %.1f..%.1f n=%d"
              % (key, P[:, 0].min(), P[:, 0].max(), P[:, 1].min(),
                 P[:, 1].max(), P[:, 2].min(), P[:, 2].max(), len(P)))

    # -- wheel ---------------------------------------------------------------
    wv = verts[np.unique(np.asarray(faces_by_group["wheel"]).ravel())]
    hw = float(np.abs(wv[:, 0]).max())
    rim = wv[np.abs(np.abs(wv[:, 0]) - hw) < 0.3]
    cy = float(wv[:, 1].mean())
    cz = float(wv[:, 2].mean())
    r = float(np.linalg.norm(rim[:, 1:] - np.array([cy, cz]), axis=1).mean())
    wheel = {"cy": cy, "cz": cz, "r": r, "hw": hw}
    print("wheel:", wheel)

    # -- lookup grids --------------------------------------------------------
    gx = np.arange(-36, 36.01, GRID_STEP)
    gy = np.arange(0.0, 126.01, GRID_STEP)
    gz = np.arange(0.0, 40.01, GRID_STEP)
    top_sel = np.concatenate([
        np.unique(np.asarray(triangles(faces_by_group[g])).ravel())
        for g in TOP_GROUPS if g in faces_by_group])
    tv = verts[top_sel]
    top_grid = splat_grid(tv[:, 0], tv[:, 1], tv[:, 2], gx, gy, "max")
    # Repair the max splat along the shoulder.  A cell keeps only the single
    # highest vertex that lands in it, and at the widest line the surface is
    # all but vertical: the topmost vertex of the run falls in one cell while
    # the cell beside it catches only flank lower down, so the height drops
    # several millimetres from node to node.  The keycap edges are seated on
    # this grid, and bilinear interpolation across those notches handed them
    # a ~6 mm wave.  A grey-scale closing fills a notch one cell wide without
    # touching the peaks (a dilation lifts the whole surface, and the wheel's
    # rim height with it); a light mean takes the residual step out.
    top_grid = box_mean_field(
        extreme_filter(extreme_filter(top_grid, GRID_FILL, "max"),
                       GRID_FILL, "min"), GRID_SMOOTH)

    side_sel = np.concatenate([
        np.unique(np.asarray(triangles(faces_by_group[g])).ravel())
        for g in SIDE_GROUPS if g in faces_by_group])
    sv = verts[side_sel]
    sv = sv[sv[:, 0] < -15.0]
    side_grid = splat_grid(sv[:, 1], sv[:, 2], sv[:, 0], gy, gz, "min")

    # -- emit ------------------------------------------------------------------
    def fmt3(arr):
        return [[round(float(v), 4) for v in row] for row in arr]

    with open(OUT_PATH, "w") as out:
        out.write('"""Geometry extracted from the detailed Aerox 5 OBJ model\n')
        out.write("(tools/extract_aerox5_v3_mesh.py -- regenerate, do not edit).\n")
        out.write("\nCoordinates are normalised: mouse length = 1.0, origin at the\n")
        out.write("body centre, x right / y back / z up.\n")
        out.write('"""\n\n')
        out.write("#: (y, [[x, z], ...]) cross-section rings, nose -> tail.\n")
        out.write("RINGS = [\n")
        for y, ring in rings_mm:
            pts = [[round(float(x) / length, 4), round(float(z) / length, 4)]
                   for x, z in ring]
            out.write(f"    ({round((y - ymin) / length - 0.5, 4)}, {pts}),\n")
        out.write("]\n\n")
        out.write("#: boundary loops of the real honeycomb holes (closed).\n")
        out.write("RIMS = [\n")
        for rim_loop in rims:
            out.write(f"    {fmt3(nrm(rim_loop))},\n")
        out.write("]\n\n")
        out.write("#: rim where the shell meets the base plate.\n")
        out.write(f"BASE_SEAM = {fmt3(nrm(seam)) if seam is not None else []}\n\n")
        out.write("#: physical button cap outlines (OBJ groups).\n")
        out.write("CAPS = {\n")
        for key, pts in caps.items():
            out.write(f"    {key!r}: {fmt3(nrm(pts))},\n")
        out.write("}\n\n")
        out.write("#: explicit ordered outlines (concave: clear the wheel + CPI).\n")
        out.write("CAP_OUTLINES = {\n")
        for key in ("main_button_left", "main_button_right"):
            out.write(f"    {key!r}: {fmt3(nrm(caps[key]))},\n")
        out.write("}\n\n")
        out.write("#: wheel cylinder (centre y/z, radius, half-width).\n")
        out.write("WHEEL = {'cy': %s, 'cz': %s, 'r': %s, 'hw': %s}\n\n" % (
            round((wheel["cy"] - ymin) / length - 0.5, 4),
            round(wheel["cz"] / length, 4),
            round(wheel["r"] / length, 4),
            round(wheel["hw"] / length, 4)))
        out.write("#: top-surface height grid: z = f(ix, iy), bilinear.\n")
        out.write(f"GRID_X = {[round(float(v) / length, 4) for v in gx]}\n")
        out.write(f"GRID_Y = {[round((float(v) - ymin) / length - 0.5, 4) for v in gy]}\n")
        out.write("GRID_TOP = [\n")
        for row in top_grid:
            out.write(f"    {[round(float(v) / length, 4) for v in row]},\n")
        out.write("]\n\n")
        out.write("#: left-flank grid: x = f(iy, iz) (negative values).\n")
        out.write(f"GRID_Z = {[round(float(v) / length, 4) for v in gz]}\n")
        out.write("GRID_SIDE = [\n")
        for row in side_grid:
            out.write(f"    {[round(float(v) / length, 4) for v in row]},\n")
        out.write("]\n")
        out.write("\n#: normalisation constants (mm -> normalised)\n")
        out.write(f"LENGTH_MM = {round(length, 3)}\n")
        out.write(f"Y_MIN_MM = {round(ymin, 3)}\n")
        out.write(f"GRID_X_MM = {[round(float(v), 2) for v in gx]}\n")
        out.write(f"GRID_Y_MM = {[round(float(v), 2) for v in gy]}\n")
        out.write(f"GRID_Z_MM = {[round(float(v), 2) for v in gz]}\n")
    print("wrote", OUT_PATH, "(%d KiB)" % (os.path.getsize(OUT_PATH) / 1024))


if __name__ == "__main__":
    main()
