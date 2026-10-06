#!/usr/bin/env python3
"""Extract a compact, runtime-friendly dataset from the detailed Aerox 5
OBJ model into ``aerox5_mesh.py`` (checked into the repo).

The GUI renderer works with normalised coordinates (mouse length = 1.0,
origin at the body centre, x right / y back / z up).  This script slices
the source mesh into cross-section rings, collects the boundary loops of
the real honeycomb holes and cut-outs, extracts the physical button caps
that exist as separate OBJ groups, and rasterises top/side surface lookup
grids used to snap the remaining (parametric) buttons onto the real
surface.

The whole pipeline lives in the repo and regenerates ``aerox5_mesh.py``
byte for byte:

    python3 tools/aerox5_model.py build          # -> build/aerox5_detailed.obj
    python3 tools/extract_aerox5_mesh.py         # -> aerox5_mesh.py

Usage:  python3 tools/extract_aerox5_mesh.py [path-to-aerox5.obj]
Requires numpy (build-time only; the generated module is pure data).
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

#: Repo root, and the OBJ that ``tools/aerox5_model.py build`` writes.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJ_DEFAULT = os.path.join(REPO_ROOT, "build", "aerox5_detailed.obj")

OBJ_PATH = sys.argv[1] if len(sys.argv) > 1 else OBJ_DEFAULT
OUT_PATH = os.path.join(REPO_ROOT, "rivalcfg_gui", "aerox5_mesh.py")

RING_COUNT = 26          # cross-section slices along the length
RING_STEPS = 48          # uniform angular resample per ring
GRID_STEP = 2.0          # mm, for both lookup grids
SEAM_MAX_PTS = 256       # decimation of the base/plate rim loop

# OBJ groups that form the visible upper surface (for the height grid).
TOP_GROUPS = ("shell", "honeycomb", "base", "main_button_left",
              "main_button_right", "wheel", "side_button_front",
              "side_button_rear", "rocker_up", "rocker_down",
              "trigger", "dpi_button")
# Groups that form the left flank (for the side lookup grid).
SIDE_GROUPS = ("shell", "honeycomb", "base", "side_button_front",
               "side_button_rear", "rocker_up", "rocker_down",
               "trigger")
# Groups that are holes (removed from the silhouette / used for rims).
HOLE_GROUPS = ("honeycomb",)
# The solid shell for slicing: everything except the hole cells.
SHELL_GROUPS = ("shell",)


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
# Cross-section rings (plane-triangle intersection + polar resample)
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
    """XZ convex hull (Andrew's monotone chain), CCW, no repeated start.

    The cross-section of the shell is essentially convex; the hull both
    discards interior geometry caught by the slice (sensor cone, hole
    walls) and bridges the small honeycomb notches with clean chords.
    """
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


def extract_rings(verts, faces, ymin, ymax):
    tris = np.asarray(triangles(faces), dtype=np.int64)
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    ys = np.linspace(ymin + 3.5, ymax - 2.5, RING_COUNT)
    rings = []
    start_idx = None   # keep index 0 at the same relative spot per ring
    for y in ys:
        xz = slice_ring(v0, v1, v2, y)
        if len(xz) < RING_STEPS:
            continue
        hull = convex_hull(xz)
        if start_idx is None:
            start_idx = int(np.argmin(hull[:, 0]))  # leftmost point first
        hull = np.roll(hull, -start_idx, axis=0)
        ring = arclength_resample(hull, RING_STEPS)
        rings.append((float(y), ring))
    return rings


# ---------------------------------------------------------------------------
# Boundary loops (honeycomb rims, wheel slot, centre groove, base rim)
# ---------------------------------------------------------------------------

def hole_rims(verts, faces):
    """Rims of the honeycomb holes / cut-outs.

    The holes are welded into the (solid) shell, so their rims are the
    edges where a *wall* face (|nz| < 0.45, i.e. the hole walls) meets a
    skin face.  Returns a list of loops as vertex-index lists.
    """
    tris = np.asarray(faces)
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    n = n / np.linalg.norm(n, axis=1)[:, None]
    cen = (a + b + c) / 3
    walls = (np.abs(n[:, 2]) < 0.45) & (cen[:, 2] > 6) & (cen[:, 2] < 41)

    wall_edges = set()
    for i in np.where(walls)[0]:
        f = tris[i]
        for k in range(3):
            wall_edges.add(tuple(sorted((f[k], f[(k + 1) % 3]))))
    nonwall_edges = set()
    for i in np.where(~walls)[0]:
        f = tris[i]
        for k in range(3):
            nonwall_edges.add(tuple(sorted((f[k], f[(k + 1) % 3]))))
    rim_edges = wall_edges & nonwall_edges

    adj = defaultdict(list)
    for a2, b2 in rim_edges:
        adj[a2].append(b2)
        adj[b2].append(a2)
    loops, seen = [], set()
    for s in list(adj):
        if s in seen:
            continue
        loop = [s]
        seen.add(s)
        prev, cur = None, s
        while True:
            nxt = [q for q in adj[cur] if q != prev and q not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            if cur == s:
                break
            loop.append(cur)
            seen.add(cur)
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def decimate(loop_pts, max_pts):
    if len(loop_pts) <= max_pts:
        return loop_pts
    idx = np.linspace(0, len(loop_pts) - 1, max_pts).astype(int)
    return loop_pts[idx]


# ---------------------------------------------------------------------------
# Lookup grids (bilinear; stored as dense arrays, -1 = unknown at runtime)
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
    # Fill holes iteratively from neighbours, then a global fallback.
    for _ in range(8):
        missing = np.isnan(grid)
        if not missing.any():
            break
        padded = np.pad(grid, 1, mode="edge")
        neigh = np.stack([
            padded[:-2, 1:-1], padded[2:, 1:-1],
            padded[1:-1, :-2], padded[1:-1, 2:],
        ])
        # Average over the neighbours that have a value: a cell whose four
        # neighbours are all still empty has nothing to average, and taking a
        # plain nanmean of that slice warns ("Mean of empty slice").  Leave it
        # NaN -- a later pass, or the global fallback below, fills it.
        seen = np.isfinite(neigh).sum(axis=0)
        neigh = np.where(seen, np.nansum(neigh, axis=0) / np.maximum(seen, 1),
                         np.nan)
        grid = np.where(missing, neigh, grid)
    fallback = np.nanmedian(grid)
    grid = np.where(np.isnan(grid), fallback, grid)
    return grid


# ---------------------------------------------------------------------------
# Button caps present as OBJ groups
# ---------------------------------------------------------------------------

def explicit_outline(faces, verts):
    """An ordered outline emitted as a triangle fan (preferred when present).

    The fan's boundary is the original ordered loop, so it keeps concavities
    (the keycap centre channel around the wheel and CPI button) that a convex
    hull would swallow.
    """
    if len(faces) < 4:
        return None
    # the fan hub is the vertex shared by every triangle
    counts = defaultdict(int)
    for f in faces:
        for v in f:
            counts[v] += 1
    hub = max(counts, key=counts.get)
    if counts[hub] < len(faces) - 1:
        return None
    loop = [v for f in faces for v in f if v != hub]
    # de-duplicate consecutive repeats (each rim vertex appears twice)
    dedup = []
    for v in loop:
        if not dedup or dedup[-1] != v:
            dedup.append(v)
    if len(dedup) > 1 and dedup[0] == dedup[-1]:
        dedup.pop()
    if len(dedup) < 3:
        return None
    return verts[np.asarray(dedup)]


def hull_xy_surface(verts, faces, lift=0.55):
    """Top keycap outline: convex hull in (x, y), lifted along the surface.

    The keycap is a curved grid patch that wraps down the nose.  We take the
    convex hull of its footprint in plan view (clean, no self-intersections)
    and give each hull point the real shell height, so the cap still wraps
    down the low nose instead of being a flat slab.
    """
    idx = sorted({i for f in faces for i in f})
    P = verts[np.asarray(idx)]
    if len(P) < 3:
        return np.empty((0, 3))
    hull_xy = convex_hull(P[:, [0, 1]])
    d = np.linalg.norm(P[:, None, :2] - hull_xy[None, :, :], axis=2)
    out = []
    for k in range(len(hull_xy)):
        j = int(np.argmin(d[:, k]))
        p = P[j].copy()
        out.append((p[0], p[1], p[2] + lift))
    return np.asarray(out)


def cap_outline(verts, faces, axis, lift=0.6):
    """Outline of a physical button group (flat projection fallback).

    *axis* is the outward normal of the button (x for the left-flank
    buttons, z for the top CPI button).  The outline is the convex hull of
    the group's vertices projected onto the two in-plane axes, lifted clear
    of the shell along *axis* so it reads over the wireframe.
    """
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    verts, faces_by_group = load_obj(OBJ_PATH)
    shell = list(faces_by_group["shell"]) + list(faces_by_group["honeycomb"])
    shell_v = np.unique(np.asarray(triangles(shell)).ravel())

    # Normalisation: length = mouse length (shell Y span), nose at y=-0.5.
    ymin = float(verts[shell_v, 1].min())
    ymax = float(verts[shell_v, 1].max())
    length = ymax - ymin
    print(f"mouse length {length:.2f} mm "
          f"(width span {np.ptp(verts[shell_v, 0]):.2f}, "
          f"height span {np.ptp(verts[shell_v, 2]):.2f})")

    def nrm(pts):
        pts = np.asarray(pts, dtype=np.float64).copy()
        pts[:, 0] /= length
        pts[:, 1] = (pts[:, 1] - ymin) / length - 0.5
        pts[:, 2] /= length
        return pts

    # -- rings ---------------------------------------------------------------
    rings_mm = extract_rings(verts, shell, ymin, ymax)
    print(f"rings: {len(rings_mm)} x {RING_STEPS}")

    # -- boundary loops: the explicit honeycomb hole outlines ----------------
    rims = []
    for f in faces_by_group.get("hole_rims", []):
        pts = verts[np.asarray(f)]
        if len(pts) >= 3:
            rims.append(pts)
    if not rims:                       # fallback: derive from hole cells
        loops = hole_rims(verts, faces_by_group.get("honeycomb", []))
        rims = [decimate(verts[np.asarray(loop)], 24) for loop in loops
                if verts[np.asarray(loop)][:, 2].mean() > 5.0]
    print(f"rims: {len(rims)}")

    # Base seam: the outer boundary of the base plate group (the shell is
    # closed at both ends by its end-cap fans, so its own free boundary is
    # two open bottom chains, not a loop).
    ecount = defaultdict(int)
    base_faces = faces_by_group.get("base", [])
    for f in base_faces:
        for i in range(len(f)):
            ecount[tuple(sorted((f[i], f[(i + 1) % len(f)])))] += 1
    free = [e for e, c in ecount.items() if c == 1]
    adj = defaultdict(list)
    for a2, b2 in free:
        adj[a2].append(b2)
        adj[b2].append(a2)
    seam, seen = None, set()
    for start in list(adj):
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        prev, cur = None, start
        while True:
            nxts = [n for n in adj[cur] if n != prev and n not in seen]
            if not nxts:
                break
            prev, cur = cur, nxts[0]
            if cur == start:
                break
            loop.append(cur)
            seen.add(cur)
        pts = verts[np.asarray(loop)]
        # keep the largest loop (the plate outline), not tiny fragments
        if len(loop) >= 8 and (seam is None or len(loop) > len(seam)):
            seam = pts
    if seam is not None:
        seam = decimate(seam, SEAM_MAX_PTS)
    print(f"base seam: {'yes' if seam is not None else 'no'}")

    # -- lookup grids --------------------------------------------------------
    top_sel = np.concatenate([
        np.unique(np.asarray(triangles(faces_by_group[g])).ravel())
        for g in TOP_GROUPS if g in faces_by_group
    ])
    tv = verts[top_sel]
    gx = np.arange(-36, 36.01, GRID_STEP)
    gy = np.arange(ymin, ymax + 0.01, GRID_STEP)
    top_grid = splat_grid(tv[:, 0], tv[:, 1], tv[:, 2], gx, gy, "max")

    side_sel = np.concatenate([
        np.unique(np.asarray(triangles(faces_by_group[g])).ravel())
        for g in SIDE_GROUPS if g in faces_by_group
    ])
    sv = verts[side_sel]
    sv = sv[sv[:, 0] < -15.0]
    gz = np.arange(2.0, 42.01, GRID_STEP)
    side_grid = splat_grid(sv[:, 1], sv[:, 2], sv[:, 0], gy, gz, "min")

    # -- physical button caps --------------------------------------------------
    def merged(group_names, axis, lift=0.6):
        faces = [f for g in group_names for f in faces_by_group.get(g, [])]
        return cap_outline(verts, faces, axis=axis, lift=lift)

    def top_cap(group_names, lift=0.55):
        """Real ordered keycap outline if the model emitted one, else hull."""
        outline = explicit_outline(faces_by_group.get(group_names[0] + "_outline",
                                                      []), verts)
        if outline is not None:
            P = outline.copy()
            P[:, 2] += lift
            return P
        faces = [f for g in group_names for f in faces_by_group.get(g, [])]
        return hull_xy_surface(verts, faces, lift=lift)

    caps = {
        "button4": merged(("side_button_rear",), axis=0),
        "button5": merged(("side_button_front",), axis=0),
        "button9": merged(("trigger",), axis=0),
        "button6": merged(("dpi_button",), axis=2),
        "button7": merged(("rocker_up",), axis=0),
        "button8": merged(("rocker_down",), axis=0),
        "main_button_right": top_cap(("main_button_right",)),
        "main_button_left": top_cap(("main_button_left",)),
    }
    for key, pts in caps.items():
        print(f"{key}: cap y {pts[:,1].min():.1f}..{pts[:,1].max():.1f} "
              f"z {pts[:,2].min():.1f}..{pts[:,2].max():.1f}")

    # -- wheel -----------------------------------------------------------------
    wv = verts[np.unique(np.asarray(faces_by_group["wheel"]).ravel())]
    wheel = {
        "cy": (wv[:, 1].min() + wv[:, 1].max()) / 2,
        "cz": (wv[:, 2].min() + wv[:, 2].max()) / 2,
        "r": (wv[:, 1].max() - wv[:, 1].min()) / 2,
        "hw": (wv[:, 0].max() - wv[:, 0].min()) / 2,
    }
    print(f"wheel: {wheel}")

    # -- emit ------------------------------------------------------------------
    def fmt3(arr):
        return [[round(float(v), 4) for v in row] for row in arr]

    def fmt2(arr):
        return [[round(float(v), 4) for v in row] for row in arr]

    with open(OUT_PATH, "w") as out:
        out.write('"""Geometry extracted from the detailed Aerox 5 OBJ model\n')
        out.write("(tools/extract_aerox5_mesh.py -- regenerate, do not edit).\n")
        out.write("\nCoordinates are normalised: mouse length = 1.0, origin at the\n")
        out.write("body centre, x right / y back / z up.\n")
        out.write('"""\n\n')
        out.write("#: (y, [[x, z], ...]) cross-section rings, nose -> tail.\n")
        out.write("RINGS = [\n")
        for y, ring in rings_mm:
            pts = [[round(float(x) / length, 4), round(float(z) / length, 4)]
                   for x, z in ring]
            yn = round((y - ymin) / length - 0.5, 4)
            out.write(f"    ({yn}, {pts}),\n")
        out.write("]\n\n")
        out.write("#: boundary loops of the real holes / cut-outs (closed).\n")
        out.write("RIMS = [\n")
        for rim in rims:
            out.write(f"    {fmt3(nrm(rim))},\n")
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
        out.write(f"GRID_Y = {[round(((float(v) - ymin) / length - 0.5), 4) for v in gy]}\n")
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
    print("wrote", OUT_PATH, f"({os.path.getsize(OUT_PATH) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
