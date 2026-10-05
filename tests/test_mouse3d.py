"""Tests for the 3D wireframe model (geometry, projection, hit testing)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mouse3d  # noqa: E402


def test_stations_are_ordered_front_to_back():
    ys = [s[0] for s in mouse3d._STATIONS]
    assert ys == sorted(ys)
    assert ys[0] < 0 < ys[-1]


def test_shell_bounds_are_realistic():
    a, b, t = mouse3d._interp_station(0.0)
    # Real proportions of the Aerox 5 (the v3 mesh is 128.2 x 68.4 x 42.0
    # mm), length normalised to 1: half-width ~0.266, top height ~0.32
    # at the middle.
    assert 0.24 < a < 0.30
    assert 0.28 < t < 0.35


def test_wireframe_has_rings_and_longitudinals():
    rings, longs = mouse3d.build_wireframe()
    assert len(rings) == len(mouse3d._STATIONS)
    steps = len(range(0, mouse3d._RING_STEPS, mouse3d._LOFT_EVERY))
    assert len(longs) == steps
    assert all(len(r) == mouse3d._RING_STEPS for r in rings)


def test_top_surface_is_highest_at_centre():
    # x=0.26 stays inside the lookup grid (which ends at +-36 mm, i.e. 0.28);
    # beyond that the sample clamps to the edge and the test would pass on a
    # repeated value.
    z_center = mouse3d.top_surface_z(0.0, 0.0)
    z_edge = mouse3d.top_surface_z(0.26, 0.0)
    assert z_center > z_edge


def test_side_surface_is_negative_and_shallow_at_top():
    x_low = mouse3d.side_surface_x(0.0, 0.05)
    x_top = mouse3d.side_surface_x(0.0, 0.229)
    assert x_low < 0
    assert abs(x_top) < abs(x_low)  # near the top the side curves inward


def test_all_expected_buttons_present():
    for key in ("button1", "button2", "button3", "button4", "button5",
                "button6", "button7", "button8", "button9"):
        assert key in mouse3d.BUTTONS, key


def test_scroll_directions_are_not_separate_facets():
    """Scroll up / down are wheel directions offered in the popover; drawing
    them as facets overlaid the wheel (the "overlapping buttons" defect)."""
    assert "scrollup" not in mouse3d.BUTTONS
    assert "scrolldown" not in mouse3d.BUTTONS


def _hole_pts(hole):
    return hole["pts"]


def _hole_station_mm(hole):
    return hole["center"].y * mouse3d._MM + mouse3d._Y0_MM


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else 0.5 * (values[n // 2 - 1]
                                               + values[n // 2])


def test_honeycomb_lattice_matches_the_reference():
    """The openings are a *coarse* perforation, not a fine mesh: rounded
    holes a little over 4 mm across, whose neighbours sit ~5 mm apart.  Both
    numbers are measured off the v3 model's real rims -- the pattern is
    extracted, not generated, so this pins the extraction rather than a
    lattice constant."""
    holes = mouse3d.DETAILS["holes"]
    assert holes
    mm = mouse3d._MM
    ext = []
    for h in holes:
        pts = _hole_pts(h)
        ext.append(max(max(p.x for p in pts) - min(p.x for p in pts),
                       max(p.y for p in pts) - min(p.y for p in pts),
                       max(p.z for p in pts) - min(p.z for p in pts)) * mm)
    assert 3.5 < _median(ext) < 5.5, _median(ext)
    # Nearest-neighbour spacing between hole centres.
    centres = [(h["center"].x, h["center"].y, h["center"].z) for h in holes]
    pitch = []
    for i, c in enumerate(centres):
        pitch.append(min(math.dist(c, o) for j, o in enumerate(centres)
                         if j != i) * mm)
    assert 4.0 < _median(pitch) < 6.5, _median(pitch)


def test_honeycomb_spans_crown_to_flank():
    """Holes run from the crown all the way down to the base plate."""
    holes = mouse3d.DETAILS["holes"]
    zmean = [sum(p.z for p in _hole_pts(h)) / len(_hole_pts(h)) for h in holes]
    assert min(zmean) < 0.22      # reaches low on the flank
    assert max(zmean) > 0.30      # and is present on the crown


def test_honeycomb_holes_are_uniform_on_the_surface():
    """The openings are one family of holes, not a mix of sizes: the typical
    hole spans the same distance along the body everywhere.  What varies in a
    projected view is the surface wrapping away from the camera, not the hole
    size.  (A handful of rims are cut short where the perforation meets the
    silhouette, so this bounds the typical hole rather than all of them.)"""
    holes = mouse3d.DETAILS["holes"]
    spans = [max(p.y for p in _hole_pts(h)) - min(p.y for p in _hole_pts(h))
             for h in holes]
    assert spans
    med = _median(spans) * mouse3d._MM
    assert 3.5 < med < 5.0, med
    assert max(spans) * mouse3d._MM < 6.0


def test_honeycomb_reaches_forward_of_the_body_centre():
    """The perforation is not only the rear field: the two wedges flanking the
    scroll wheel carry it too, well forward of the body centre.

    The fields flanking the scroll wheel start where the keycaps end, ~41 mm
    from the nose, and the first rows sit forward of the body centre (64.1 mm
    after the normalisation shift)."""
    holes = mouse3d.DETAILS["holes"]
    forward = [h for h in holes if h["center"].y < 0.0]
    assert forward
    assert min(_hole_station_mm(h) for h in forward) < 45.0


def test_lever_spans_the_thumb_buttons():
    """The long up/down flick lever sits over both thumb buttons.

    In the v3 model the lever runs 41-80 mm from the nose while button5
    covers 41-62.5 mm and button4 63.5-87 mm: the lever spans all of the
    front button and the front half of the rear one, as on the real mouse.

    Neither end is drawn where the model puts it.  Button4's tail is cut back
    to the station ``error_shape.png`` draws -- see
    :func:`test_tail_cut_moves_the_end_and_carries_its_profile` -- so its rear
    edge is nearer than the model's own part line, and the lever reaches
    further into it than the shape alone would say.  The lever's own leading
    end is cut back in turn, far enough to clear the click panel, so it starts
    a few mm behind button5's leading edge instead of level with it -- the
    reason is :func:`test_lead_cut_clears_the_click_panel`.  What the lever
    still has to do is stay one long paddle over both buttons, and that is
    what this pins.
    """
    def yspan(key):
        poly = mouse3d.BUTTONS[key][1]
        return min(p.y for p in poly), max(p.y for p in poly)

    lo7, hi7 = yspan("button7")
    lo5, hi5 = yspan("button5")
    lo4, hi4 = yspan("button4")
    assert abs(hi4 * mouse3d._MM - mouse3d._TAIL_CUT_MM["button4"]) < 1e-6
    assert abs(lo7 * mouse3d._MM - mouse3d._LEAD_CUT_MM["button7"]) < 1e-6
    assert lo7 > lo5                       # cut back off the front button
    assert lo7 <= lo5 + 0.25 * (hi5 - lo5)  # but still over most of it
    assert hi7 >= hi5                      # covers the front button's rear
    assert lo7 <= lo4                      # reaches into the rear button
    assert hi7 >= lo4 + 0.5 * (hi4 - lo4)  # and past its midpoint


def test_thumb_buttons_are_adjacent():
    """Buttons 4 and 5 are adjacent (no plastic gap between them)."""
    _l, front = mouse3d.BUTTONS["button5"]
    _l, rear = mouse3d.BUTTONS["button4"]
    gap = min(p.y for p in rear) - max(p.y for p in front)
    assert abs(gap) < 0.02


def test_click_panels_are_symmetric():
    """The two keycaps are one mirrored pair in plan.

    The mirroring lives in the mesh, not in :mod:`mouse3d`:
    ``tools/extract_aerox5_v3_mesh`` traces both panels from the *left*
    panel's part line and reflects it about the centreline, because
    ``model.py`` breaks its flank asymmetrically -- the two ``click_*``
    groups are *not* mirrors, and tracing each panel from its own rim draws
    the right cap 4.2 mm wider through the shoulder.

    Plan only.  The heights are taken per side, from that side's own top
    faces, so a correct pair differs in ``z`` by up to 4.8 mm and a 3D
    mirror test would fail on it -- check ``(x, y)``.
    """
    _l, left = mouse3d.BUTTONS["button1"]
    _l, right = mouse3d.BUTTONS["button2"]
    # Every left point has a right point at its mirror image in plan.
    worst = max(min(math.hypot(q.x + p.x, q.y - p.y) for q in right)
                for p in left)
    assert worst < 0.005, worst * mouse3d._MM  # 0.64 mm
    # Both caps reach the same distance outboard through the shoulder.
    lx = max(abs(p.x) for p in left)
    rx = max(abs(p.x) for p in right)
    assert abs(lx - rx) < 0.005, (lx * mouse3d._MM, rx * mouse3d._MM)


def test_click_panels_converge_toward_the_nose():
    """The real keycaps are widest at the rear and taper continuously to a
    narrow leading edge at the nose (top view): the width grows monotonically
    from the leading edge to the widest point."""
    _label, poly = mouse3d.BUTTONS["button1"]
    # Width per band along the body.  Bands rather than exact ``y`` rows:
    # the outline is a dense curve, so several points share a station and
    # grouping on the raw float splits a single station into two rows.
    ys = [p.y for p in poly]
    lo, hi = min(ys), max(ys)
    bands = 100
    width = [0.0] * bands
    for p in poly:
        k = min(bands - 1, int((p.y - lo) / (hi - lo) * bands))
        width[k] = max(width[k], abs(p.x))
    prof = [w for w in width if w > 0.0]
    front_w = prof[0]
    widest = max(prof)
    # the leading edge is much narrower than the widest point
    assert front_w < 0.5 * widest
    # and the width grows from the nose toward the widest point.  The outline
    # is the real panel edge snapped onto the shell, so it carries the shell's
    # own sub-millimetre bumps; smooth over a few bands and the taper is
    # monotone.  A real pinch is an order of magnitude larger than the wobble
    # this smoothing leaves.
    peak = max(range(len(prof)), key=lambda i: prof[i])
    sm = []
    for i in range(peak + 1):
        lo_i, hi_i = max(0, i - 4), min(len(prof), i + 5)
        sm.append(max(prof[lo_i:hi_i]))
    assert all(sm[i] <= sm[i + 1] + 0.02 * widest
               for i in range(len(sm) - 1))


def test_click_panels_wrap_down_the_nose():
    """The front of each key drops toward the low nose instead of floating
    at the crown: the leading edge sits well below the panel's high point."""
    _label, poly = mouse3d.BUTTONS["button1"]
    y_front = min(p.y for p in poly)
    assert y_front < -0.46
    z_front = min(p.z for p in poly if p.y < y_front + 0.05)
    z_max = max(p.z for p in poly)
    assert z_front <= z_max - 0.08


def test_keycaps_meet_at_centreline_before_the_wheel():
    """In front of the wheel slot the two keycaps hug the centre channel
    rather than splaying over the shell: the v3 model leaves a ~9 mm housing
    down the middle for the wheel and the CPI button, and the caps' inner
    edges run either side of it, ~4.5 mm out from the centreline."""
    for key in ("button1", "button2"):
        _label, poly = mouse3d.BUTTONS[key]
        front = [abs(p.x) for p in poly if p.y < -0.44]
        assert front and min(front) < 0.05, (key, min(front))


def test_keycaps_clear_the_wheel_and_dpi():
    """Behind the wheel slot the centre split widens into a channel: no
    keycap point intrudes into the wheel's or the CPI button's footprint."""
    w = mouse3d._mesh.WHEEL
    hw = w["hw"]
    wf, wb = w["cy"] - w["r"], w["cy"] + w["r"]
    dpi = mouse3d._mesh.CAPS["button6"]
    dx = max(abs(p[0]) for p in dpi)
    dy0, dy1 = min(p[1] for p in dpi), max(p[1] for p in dpi)

    # The cap's inner edge is authored flush with the wheel, and the outline
    # is a resampled curve, so a sample can land a hair inside by less than
    # a hundredth of a millimetre.  Anything larger is real contact.
    tol = 5e-5

    for key in ("button1", "button2"):
        for p in mouse3d.BUTTONS[key][1]:
            if wf - 0.004 < p.y < wb + 0.004:
                assert abs(p.x) >= hw - tol, (key, p)    # clear of the wheel
            if dy0 - 0.003 < p.y < dy1 + 0.003:
                assert abs(p.x) >= dx - tol, (key, p)    # clear of the CPI


def test_keycaps_leave_a_centre_channel():
    """The two caps run down either side of a centre channel.

    Both panels share the channel's inner edge, which is a centred feature
    (wheel well -> spine -> CPI pocket); the inner edge holds ~4.9-5.1 mm
    off the centreline over the body and closes onto it only at the wheel
    well.  Neither cap ever crosses the centreline onto the other's side.

    This replaces a test written against the per-panel free-edge rim, which
    held each panel a steady 5.3-5.5 mm off the centreline the whole way
    back.  That tracing is gone -- it drew the right cap 4.2 mm wider -- so
    the assertion it carried no longer describes the outline.
    """
    left = mouse3d.BUTTONS["button1"][1]
    right = mouse3d.BUTTONS["button2"][1]
    # the panels never merge onto the centreline
    assert max(p.x for p in left) < min(p.x for p in right)

    mm = mouse3d._MM

    def station(p):
        return p.y * mm + mouse3d._Y0_MM

    for key in ("button1", "button2"):
        _label, poly = mouse3d.BUTTONS[key]
        channel = [(station(p), abs(p.x) * mm) for p in poly]
        assert [x for s, x in channel if 12.0 < s < 53.0]
        # over the body proper the inner edge holds ~5 mm off the centreline
        body = [x for s, x in channel if 5.0 < s < 35.0]
        assert body and min(body) > 4.5, (key, min(body))


def test_seam_correction_moves_only_the_traced_run():
    """``_SEAM_FIX`` displaces the drawn stretch of an outline, not the rest.

    The table comes from the two marked-up screenshots in
    ``aerox5_3d_files``: the left click panel's edge follows the long stroke
    drawn along the seam it leaves for the paddles, and then the shorter arc
    drawn later over the corner that first fit leaves.  Interpolation is by
    outline vertex, so the table must leave every vertex outside its runs
    exactly where it found it -- the panel's nose and rear, and all of the
    side buttons, have to stay put.
    """
    assert mouse3d._SEAM_FIX, "the fitted table is empty"
    assert mouse3d._SEAM_STRIDE > 1

    # A loop with a marker vertex per index; only the fitted stretch moves.
    count = len(mouse3d.BUTTONS["button1"][1])
    loop = [mouse3d.Point3D(i * 1e-3, 0.0, 0.0) for i in range(count)]
    moved = mouse3d._seam_shift("button1", loop, ("x", "y"))

    touched = 0
    for i, (before, after) in enumerate(zip(loop, moved)):
        if after.x == before.x and after.y == before.y:
            continue
        touched += 1
        # the panel is planar in (x, y): the seam never lifts or drops it
        assert after.z == before.z
    assert 0 < touched < count // 2

    # Runs are contiguous, and the table decays to nothing at both ends of
    # each, so the interpolation joins back onto the untouched outline
    # instead of stepping off it.
    for _first, knots in mouse3d._SEAM_FIX["button1"]:
        peak = max(math.hypot(*k) for k in knots)
        for k in (knots[0], knots[-1]):
            assert math.hypot(*k) < peak / 100.0

    # An unknown key, and a key with no fitted run, are both a no-op.
    for key in ("button4", "nonsense"):
        same = mouse3d._seam_shift(key, loop, ("x", "y"))
        assert all(a.x == b.x and a.y == b.y for a, b in zip(loop, same))


def test_seam_correction_leaves_the_side_buttons_alone():
    """The paddles' edges already lie on the drawn seam, so they do not move.

    The stroke runs along button 7/8's ends where it passes them; only the
    hooks at either end of it, where the pen left the seam, are further out --
    and those are not corrections.  Nothing else may be displaced either.
    """
    assert set(mouse3d._SEAM_FIX) <= {"button1"}

    for key in ("button4", "button5", "button7", "button8", "button9"):
        poly = mouse3d.BUTTONS[key][1]
        for p in poly:
            assert all(math.isfinite(v) for v in (p.x, p.y, p.z))


def test_tail_cut_moves_the_end_and_carries_its_profile():
    """Button 4's tail is carried back to the station the annotation draws.

    The drawn line crosses the paddle from its top edge to its bottom one, so
    the cut keeps the drawn height exactly -- only ``y`` moves -- and the end
    profile that lands at the new station is the one the outline already drew
    at the old one, moved rather than rebuilt.
    """
    cap = mouse3d._smooth_closed(
        [mouse3d.Point3D(*p) for p in mouse3d._mesh.CAPS["button4"]])
    cut_mm = mouse3d._TAIL_CUT_MM["button4"]
    tip = max(p.y for p in cap)
    face = sorted(p.z for p in cap if p.y == tip)

    cut = mouse3d._cut_tail(cap, cut_mm)
    assert abs(max(p.y for p in cut) * mouse3d._MM - cut_mm) < 1e-6
    assert (tip - max(p.y for p in cut)) * mouse3d._MM > 5.0   # a cut, not a nudge
    assert [min(p.z for p in cap), max(p.z for p in cap)] == \
           [min(p.z for p in cut), max(p.z for p in cut)]
    # The face came across whole: same points, same ``z``, one new station.
    new_tip = max(p.y for p in cut)
    assert sorted(p.z for p in cut if p.y == new_tip) == face
    assert len(face) > 4


def test_tail_cut_leaves_everything_else_alone():
    """Only the button the line was drawn across is cut.

    A loop the plane does not cross exactly twice comes back untouched -- one
    already ending in front of the cut, one behind it, and one made only of
    long edges, which has no end profile to carry -- so a refit can neither
    shorten a button twice nor pick up a neighbour.
    """
    assert set(mouse3d._TAIL_CUT_MM) <= {"button4"}

    square = [mouse3d.Point3D(0.0, y, z) for y, z in
              ((0.0, 0.0), (0.2, 0.0), (0.2, 0.2), (0.0, 0.2))]
    assert mouse3d._cut_tail(square, -5.0) == square            # behind the front
    assert mouse3d._cut_tail(square, 40.0) == square            # past the tail
    assert mouse3d._cut_tail(square, 0.15 * mouse3d._MM) == square  # no profile

    loop = [mouse3d.Point3D(i * 1e-3, 0.0, 0.0) for i in range(8)]
    assert mouse3d._cut_tail(loop, 40.0) == loop


def test_lead_cut_moves_the_end_and_carries_its_profile():
    """Each cut end is carried to its station with the drawn face intact.

    Same guarantee as the tail cut, other end: the drawn height is kept
    exactly -- only ``y`` moves -- and the profile that lands at the new
    station is the one the outline already drew, moved rather than rebuilt.
    The lever's two halves are the tighter case: their corners are about
    0.1 mm between samples, against the 0.15 mm ``_cut_tail`` keeps clear of
    each long edge, so the carried face is the drawn one less its outermost
    sample or two at each corner -- well inside the corner's own 1.9 mm roll.
    Button 5 carries its face whole, and comes back less far: the arc drawn
    across it sits about 2.7 mm behind the end the mesh gives, against the
    lever's 4.1 mm.  The lever's two halves come back to the same station, so
    the split lever still reads as one paddle.
    """
    nudge = {"button5": 2.0, "button7": 3.0, "button8": 3.0}
    for cap in ("button5", "button7", "button8"):
        whole = mouse3d._smooth_closed(
            [mouse3d.Point3D(*p) for p in mouse3d._mesh.CAPS[cap]])
        cut_mm = mouse3d._LEAD_CUT_MM[cap]
        tip = min(p.y for p in whole)

        def face(loop, at):
            return [p.z for p in loop if abs(p.y - at) < 0.5 / mouse3d._MM]

        drawn = face(whole, tip)
        cut = mouse3d._cut_lead(whole, cut_mm)
        assert abs(min(p.y for p in cut) * mouse3d._MM - cut_mm) < 1e-6
        assert (min(p.y for p in cut) - tip) * mouse3d._MM > nudge[cap]  # not a nudge
        assert [min(p.z for p in whole), max(p.z for p in whole)] == \
               [min(p.z for p in cut), max(p.z for p in cut)]

        carried = face(cut, min(p.y for p in cut))
        assert len(carried) > 4
        assert set(carried) <= set(drawn)           # moved, not resampled
        # ...and still the drawn end, not a stub off it.
        assert max(carried) - min(carried) >= \
               max(drawn) - min(drawn) - 0.25 / mouse3d._MM
        assert max(carried) == max(drawn)

    assert mouse3d._LEAD_CUT_MM["button7"] == mouse3d._LEAD_CUT_MM["button8"]


def test_lead_cut_leaves_everything_else_alone():
    """Only the buttons a line was drawn across are cut, and only at the lead.

    The lever's two halves and button 5 -- the three the annotations name --
    and nothing at the other end of any of them.
    """
    assert set(mouse3d._LEAD_CUT_MM) == {"button5", "button7", "button8"}
    assert set(mouse3d._TAIL_CUT_MM).isdisjoint(mouse3d._LEAD_CUT_MM)

    square = [mouse3d.Point3D(0.0, y, z) for y, z in
              ((0.0, 0.0), (0.2, 0.0), (0.2, 0.2), (0.0, 0.2))]
    assert mouse3d._cut_lead(square, -40.0) == square          # past the tail
    assert mouse3d._cut_lead(square, 5.0) == square            # behind the front
    assert mouse3d._cut_lead(square, -0.15 * mouse3d._MM) == square  # no profile


def test_lead_cut_clears_the_click_panel():
    """The reason the lever is cut at all: the panel it would otherwise cross.

    The renderer draws every outline, back faces included, so a side button
    whose corners land inside the click panel's silhouette is drawn straight
    over it -- the lever's did, as a sliver aimed up and left that read as a
    paddle both too long and trending up towards the nose.  In the buttons
    page's own view no side button may put a vertex inside the panel, and
    without the cut the lever's two halves both would.
    """
    view = mouse3d.fit_view(1400, 900, view=mouse3d.View(
        yaw=math.radians(-120.0), pitch=math.radians(42.0)), fill=0.66)
    panel = view.project_many(mouse3d.BUTTONS["button1"][1])
    for key in ("button4", "button5", "button7", "button8", "button9"):
        pts = view.project_many(mouse3d.BUTTONS[key][1])
        offenders = [p for p in pts if mouse3d.point_in_polygon(*p, panel)]
        assert not offenders, (key, offenders[:3])

    # ...and the cut is what does it: on the mesh's own outline the halves
    # collide, so this cannot quietly become a test of an already-clear shape.
    for cap in ("button7", "button8"):
        whole = mouse3d._smooth_closed(
            [mouse3d.Point3D(*p) for p in mouse3d._mesh.CAPS[cap]])
        seated = [mouse3d.Point3D(
            (lambda fx: p.x if fx is None or fx >= 0.0 else fx)(
                mouse3d.side_surface_x(p.y, p.z)) - 0.004, p.y, p.z)
            for p in whole]
        pts = view.project_many(seated)
        assert any(mouse3d.point_in_polygon(*p, panel) for p in pts), cap


def test_uncut_flank_buttons_are_the_seating_of_their_caps():
    """The cuts are the only thing between the mesh's caps and the drawn ones.

    Everything else on the flank is the plain re-seating, so a mesh refit can
    only move a button this file names a station for.  The two cuts are named
    per *cap*, not per drawn button, because that is the key
    :func:`mouse3d._cap_on_flank` is handed -- see the ``mesh_key`` swap.
    """
    # buttons 7 and 8 take their caps from each other's mesh part -- the
    # lever's two halves are drawn as one on the real mouse and split here
    mesh_key = {"button7": "button8", "button8": "button7"}
    for key in ("button4", "button5", "button7", "button8", "button9"):
        cap = mesh_key.get(key, key)
        loop = mouse3d._smooth_closed(
            [mouse3d.Point3D(*p) for p in mouse3d._mesh.CAPS[cap]])
        loop = mouse3d._cut_tail(loop, mouse3d._TAIL_CUT_MM.get(cap, 1e9))
        lead = mouse3d._LEAD_CUT_MM.get(cap)
        if lead is not None:
            loop = mouse3d._cut_lead(loop, lead)
        assert len(loop) == len(mouse3d.BUTTONS[key][1])
        for p, q in zip(loop, mouse3d.BUTTONS[key][1]):
            assert (p.y, p.z) == (q.y, q.z)      # seated in x, untouched in y, z


def test_project_returns_finite_pairs():
    view = mouse3d.fit_view(400, 300)
    for key, (_label, poly) in mouse3d.BUTTONS.items():
        for sx, sy in view.project_many(poly):
            assert math.isfinite(sx) and math.isfinite(sy)


def test_fit_view_keeps_everything_in_frame():
    w, h = 500, 400
    margin = 40
    view = mouse3d.fit_view(w, h, margin=margin)
    rings, _ = mouse3d.build_wireframe()
    for ring in rings:
        for sx, sy in view.project_many(ring):
            assert -1 <= sx <= w + 1, (sx, w)
            assert -1 <= sy <= h + 1, (sy, h)


def test_hit_test_inside_click_panel():
    view = mouse3d.fit_view(600, 500)
    poly = view.project_many(mouse3d.BUTTONS["button1"][1])
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    assert mouse3d.point_in_polygon(cx, cy, poly)


def test_point_in_polygon_outside():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert not mouse3d.point_in_polygon(20, 20, square)
    assert mouse3d.point_in_polygon(5, 5, square)


def test_view_angles_produce_distinct_silhouettes():
    """Top, 3/4 and side views must differ (not degenerate)."""
    def span(yaw_deg, pitch_deg):
        view = mouse3d.fit_view(400, 400, view=mouse3d.View(
            yaw=math.radians(yaw_deg), pitch=math.radians(pitch_deg)))
        rings, _ = mouse3d.build_wireframe()
        pts = [view.project(p) for ring in rings for p in ring]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (max(xs) - min(xs), max(ys) - min(ys))

    top = span(0, 90)
    side = span(-90, 8)
    # Top view: the mouse is longer (screen height) than it is wide.
    assert top[1] > top[0]
    # Side view: long and low (width >> height).
    assert side[0] > side[1]
    assert side[1] < top[1]


class _FakeCR:
    """Minimal cairo-like recorder so render() can run headlessly."""

    def __init__(self):
        self.strokes = 0
        self.fills = 0
        self.texts = []
        self.operators = []
        #: ``set_source_rgb`` fills -- the lit passes (body and strip) -- kept
        #: apart from :attr:`rgba`, which also carries the neutral structure.
        self.colours = []
        self.rgba = []

    def save(self):
        pass

    def restore(self):
        pass

    def set_operator(self, op):
        self.operators.append(op)

    def set_line_join(self, _j):
        pass

    def set_source_rgba(self, *c):
        self.rgba.append(tuple(c))

    def set_source_rgb(self, *c):
        self.colours.append(tuple(c))

    def set_line_width(self, _w):
        pass

    def paint(self):
        pass

    def move_to(self, *_p):
        pass

    def line_to(self, *_p):
        pass

    def close_path(self):
        pass

    def stroke(self):
        self.strokes += 1

    def fill(self):
        self.fills += 1

    def fill_preserve(self):
        self.fills += 1

    def rectangle(self, *_r):
        pass

    def arc(self, *_a):
        pass

    def select_font_face(self, *_f):
        pass

    def set_font_size(self, _s):
        pass

    def text_extents(self, text):
        from collections import namedtuple
        ext = namedtuple("TextExtents", ("x_bearing", "y_bearing", "width",
                                         "height", "x_advance", "y_advance"))
        return ext(0, -10, 7 * len(text), 12, 7 * len(text), 0)

    def show_text(self, text):
        self.texts.append(text)


def test_render_draws_scene_and_reports_geometry():
    w, h = 800, 600
    view = mouse3d.fit_view(w, h, view=mouse3d.View(
        yaw=math.radians(-120), pitch=math.radians(42)))
    cr = _FakeCR()
    info = mouse3d.render(cr, w, h, view,
                          label_fn=lambda key, label: label)
    assert cr.strokes > 100  # shell + details + buttons actually drawn
    assert set(info) == {"view", "chips", "anchors"}
    assert set(info["chips"]) == set(mouse3d.BUTTONS)
    # Every chip stays inside the canvas.
    for key, (cx, cy, cw, ch, _label) in info["chips"].items():
        assert 0 <= cx and cx + cw <= w, key
        assert 0 <= cy and cy + ch <= h, key
    # Every visible button got a chip label.
    assert len(cr.texts) == len(mouse3d.BUTTONS)


def test_side_buttons_stay_on_the_flank():
    """Side-button corners must not wrap over the crown: the shell gets
    lower towards the nose, so heights are expressed as fractions of the
    local shell height and must keep a margin from the top ridge.  The lever
    in the v3 model rides high (0.92 of the local height), so the margin is
    only a few percent -- still well clear of the ridge."""
    for key in ("button4", "button5", "button7", "button8", "button9"):
        _label, poly = mouse3d.BUTTONS[key]
        for p in poly:
            _a, zb, zt = mouse3d._interp_station(p.y)
            # the cap is lifted a hair clear of the shell surface so it
            # reads over the wireframe; allow that tiny epsilon.
            assert zb - 0.004 <= p.z <= zt + 1e-9, (key, p)
            zf = (p.z - zb) / max(1e-9, zt - zb)
            assert zf <= 0.95, (key, p)


def test_click_panels_clear_side_buttons():
    """The click keycaps live on the top surface: behind the nose -- the only
    stretch where the side buttons reach -- every point sits above the shell
    mid-height, so they never spill onto the side-button flank.  Forward of
    that the cap legitimately wraps down the low nose, where there is nothing
    to collide with."""
    for key in ("button1", "button2"):
        _label, poly = mouse3d.BUTTONS[key]
        for p in poly:
            if p.y < -0.30:
                continue
            _a, zb, zt = mouse3d._interp_station(p.y)
            assert p.z > zb + 0.55 * (zt - zb), (key, p)


def test_side_buttons_do_not_overlap_in_side_view():
    """In the pure side view the five side buttons are disjoint tiles."""
    view = mouse3d.fit_view(1200, 800, view=mouse3d.View(
        yaw=math.radians(-90), pitch=math.radians(10)))
    keys = ("button4", "button5", "button7", "button8", "button9")
    polys = {k: view.project_many(mouse3d.BUTTONS[k][1]) for k in keys}
    for key, pts in polys.items():
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for other, opts in polys.items():
            if other != key:
                assert not mouse3d.point_in_polygon(cx, cy, opts), \
                    (key, other)


def test_chips_track_their_anchors():
    """Chips stay near their anchor height so leader lines are short
    (no full-height column spread sending leaders across the drawing)."""
    w, h = 1000, 600
    view = mouse3d.fit_view(w, h, view=mouse3d.View(
        yaw=math.radians(-120), pitch=math.radians(42)))
    info = mouse3d.render(_FakeCR(), w, h, view)
    for key, (_ax, ay) in info["anchors"].items():
        _cx, cy, _cw, ch, _label = info["chips"][key]
        assert abs((cy + ch / 2) - ay) <= 240, key


def test_render_respects_visible_set():
    view = mouse3d.fit_view(400, 400)
    cr = _FakeCR()
    info = mouse3d.render(cr, 400, 400, view, visible={"button1", "button2"})
    assert set(info["chips"]) == {"button1", "button2"}


# ---------------------------------------------------------------------------
# Lighting preview (the RGB page)
# ---------------------------------------------------------------------------

_LIGHT_YAW = math.radians(-120.0)
_LIGHT_PITCH = math.radians(42.0)


def _lighting_view():
    """The camera the RGB page uses: the buttons-page angles, stood up."""
    return mouse3d.View(yaw=_LIGHT_YAW, pitch=_LIGHT_PITCH,
                        roll=mouse3d.upright_roll(_LIGHT_YAW, _LIGHT_PITCH))


def _lighting_geometry(w=260, h=340):
    return mouse3d.build_lighting_geometry(w, h, view=_lighting_view())


def _ring_centre(pts):
    n = len(pts)
    return (sum(p.x for p in pts) / n, sum(p.y for p in pts) / n,
            sum(p.z for p in pts) / n)


def test_zero_roll_is_the_untouched_projection():
    """roll = 0.0 must skip the branch entirely -- same operations, so the
    result is bit-for-bit what the Buttons page has always drawn."""
    view = mouse3d.View(yaw=_LIGHT_YAW, pitch=_LIGHT_PITCH, scale=3.5,
                        offset_x=10.0, offset_y=-4.0)
    assert view.roll == 0.0
    cy, sy = math.cos(view.yaw), math.sin(view.yaw)
    cp, sp = math.cos(view.pitch), math.sin(view.pitch)
    for p in (mouse3d.Point3D(0.1, -0.2, 0.3),
              mouse3d.Point3D(-0.26, 0.4, 0.05)):
        x1 = p.x * cy - p.y * sy
        y1 = p.x * sy + p.y * cy
        z2 = -y1 * sp + p.z * cp
        assert view.project(p) == (10.0 + x1 * view.scale,
                                   -4.0 - z2 * view.scale)


def test_upright_roll_stands_the_mouse_up():
    roll = mouse3d.upright_roll(_LIGHT_YAW, _LIGHT_PITCH)
    # Not a bare quarter turn: the length axis already projects well off
    # horizontal at these angles, so 90 deg would leave the mouse tilted.
    assert abs(roll - math.pi / 2.0) > math.radians(15)

    # The projected body axis (front -> back) has to come out exactly
    # vertical, pointing down the screen.
    view = _lighting_view()
    origin = view.project(mouse3d.Point3D(0.0, 0.0, 0.0))
    axis = view.project(mouse3d.Point3D(0.0, 1.0, 0.0))
    assert abs(axis[0] - origin[0]) < 1e-12
    assert axis[1] - origin[1] > 0.0

    # Which puts the nose above the tail.  The centres are mid-height points,
    # so they lean slightly: the mouse is a wedge, and its centreline rises
    # towards the back -- the body axis, not the centreline, is what is
    # vertical.
    fitted = mouse3d.fit_view(260, 340, view=_lighting_view())
    rings, _longs = mouse3d.build_wireframe()
    nose = fitted.project(mouse3d.Point3D(*_ring_centre(rings[0])))
    tail = fitted.project(mouse3d.Point3D(*_ring_centre(rings[-1])))
    assert nose[1] < tail[1]
    assert abs(nose[0] - tail[0]) < 0.1 * (tail[1] - nose[1])


def test_fit_view_still_fits_with_roll():
    w, h = 260, 340
    view = mouse3d.fit_view(w, h, view=_lighting_view(), margin=14, fill=0.94)
    rings, _longs = mouse3d.build_wireframe()
    for ring in rings:
        for sx, sy in view.project_many(ring):
            assert -1 <= sx <= w + 1, (sx, w)
            assert -1 <= sy <= h + 1, (sy, h)


def test_zone_weights_sum_to_one_and_order_the_zones():
    for y in (-0.5, -0.3, -0.11, 0.0, 0.1, 0.45, 0.5):
        wgt = mouse3d.zone_weights(y)
        assert abs(sum(wgt) - 1.0) < 1e-12, y
        assert all(0.0 <= v <= 1.0 for v in wgt), y

    ys = [s[0] for s in mouse3d._STATIONS]
    rgb = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    # nose is all z1, tail all z3, and the blend is smooth between
    assert mouse3d.zone_colour(rgb, ys[0]) == (1.0, 0.0, 0.0)
    assert mouse3d.zone_colour(rgb, ys[-1]) == (0.0, 0.0, 1.0)
    b1, _b2 = mouse3d._ZONE_EDGES
    w1 = mouse3d.zone_weights(b1)
    assert abs(w1[0] - 0.5) < 1e-12 and abs(w1[1] - 0.5) < 1e-12


def test_zone_boundaries_are_real_millimetres():
    """The band edges come from the mesh: 50 mm is where the keycaps end,
    88 mm leaves the tail as the zone carrying the visible strip."""
    assert mouse3d._ZONE_EDGES_MM == (50.0, 88.0)
    for edge, mm in zip(mouse3d._ZONE_EDGES, mouse3d._ZONE_EDGES_MM):
        assert abs(edge * mouse3d._MM + mouse3d._Y0_MM - mm) < 1e-9


def test_lighting_geometry_bands_the_whole_body():
    w, h = 260, 340
    _view, geo = _lighting_geometry(w, h)
    assert geo["slabs"] and geo["strip"]

    pts = [p for s in geo["slabs"] for p in s["pts"]]
    assert min(p[0] for p in pts) >= -1 and max(p[0] for p in pts) <= w + 1
    assert min(p[1] for p in pts) >= -1 and max(p[1] for p in pts) <= h + 1

    # Every zone lights some of the body, and the order runs nose -> tail.
    assert {s["zone"] for s in geo["slabs"]} == {0, 1, 2}
    assert min(geo["slabs"], key=lambda s: s["y"])["zone"] == 0
    assert max(geo["slabs"], key=lambda s: s["y"])["zone"] == 2

    # The strip ribbon never leaves its own length band.
    for s in geo["strip"]:
        assert mouse3d._STRIP_Y[0] - 1e-9 <= s["y"] <= mouse3d._STRIP_Y[1] + 1e-9


def test_slabs_are_shaded_by_surface_angle():
    """Not a flat fill: the Newell normal has to actually reach the camera
    (a sign flip once left every slab at the same darkness), and the arc has
    to stay short enough that each slab's normal is local (a slab spanning
    half the ring averages out to pointing at the camera)."""
    _view, geo = _lighting_geometry()
    top = mouse3d._SHADE_FLOOR + mouse3d._SHADE_RANGE
    shades = [s["shade"] for s in geo["slabs"]]
    assert all(mouse3d._SHADE_FLOOR - 1e-9 <= v <= top + 1e-9 for v in shades)
    # A real spread, not one value: the shell has to read as a surface.
    assert max(shades) - min(shades) > 0.25
    assert max(shades) > 0.5
    # And it stays well under the full zone colour -- the glow belongs to the
    # strip and the perforations, not to the whole shell.
    assert top < 0.8


def test_zone_at_hits_each_zone_and_misses_outside():
    _view, geo = _lighting_geometry()
    for zone in range(3):
        group = [s for s in geo["slabs"] if s["zone"] == zone]
        assert group, zone
        slab = group[len(group) // 2]
        cx = sum(p[0] for p in slab["pts"]) / len(slab["pts"])
        cy = sum(p[1] for p in slab["pts"]) / len(slab["pts"])
        assert mouse3d.zone_at(geo, cx, cy) == zone
    assert mouse3d.zone_at(geo, -10.0, -10.0) is None


def test_render_lighting_draws_scene_with_additive_bloom():
    w, h = 260, 340
    _view, geo = _lighting_geometry(w, h)
    cr = _FakeCR()
    out = mouse3d.render_lighting(cr, w, h, geo,
                                  [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                   (0.0, 0.0, 1.0)])
    assert out is geo
    assert cr.fills > 50   # body + strip + bloom
    assert cr.strokes > 50  # slabs, honeycomb rims, structure
    assert mouse3d._OPERATOR_ADD in cr.operators


def test_lighting_geometry_carries_no_colour():
    """Colours are applied at draw time, so the cached geometry can be
    re-rendered on every edit without rebuilding it."""
    w, h = 260, 340
    _view, geo = _lighting_geometry(w, h)
    before = [dict(s) for s in geo["slabs"]]
    mouse3d.render_lighting(_FakeCR(), w, h, geo,
                            [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)])
    mouse3d.render_lighting(_FakeCR(), w, h, geo,
                            [(1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (1.0, 1.0, 1.0)])
    assert [dict(s) for s in geo["slabs"]] == before


def test_zone_colours_blend_rather_than_step():
    """The bands have to cross-fade: a hard edge between two pure zone
    colours reads as the three flat blocks this display replaced."""
    rgb = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    ys = [s[0] for s in mouse3d._STATIONS]
    lo, hi = ys[0], ys[-1]

    worst = 0.0
    prev = mouse3d.zone_colour(rgb, lo)
    for i in range(1, 401):
        cur = mouse3d.zone_colour(rgb, lo + (hi - lo) * i / 400.0)
        worst = max(worst, max(abs(a - b) for a, b in zip(cur, prev)))
        prev = cur
    assert worst < 0.05, worst  # no jump between neighbouring samples

    # And the boundary is a genuine mix of its two zones, not either one.
    b1, _b2 = mouse3d._ZONE_EDGES
    mid = mouse3d.zone_colour(rgb, b1)
    assert mid[0] > 0.05 and mid[1] > 0.05 and mid[2] < 0.05


def _dominant(c):
    """The channel a recorded fill colour leads on ('r' wins ties)."""
    return max(((c[0], "r"), (c[1], "g"), (c[2], "b")))[1]


def _hue(c):
    """Hue of a pure ``_hsv(h, 1, 1)`` colour, in turns (0 red, 1/3 green)."""
    mx, mn = max(c), min(c)
    if mx == mn:
        return 0.0
    if mx == c[0]:
        h = ((c[1] - c[2]) / (mx - mn)) % 6.0
    elif mx == c[1]:
        h = (c[2] - c[0]) / (mx - mn) + 2.0
    else:
        h = (c[0] - c[1]) / (mx - mn) + 4.0
    return (h / 6.0) % 1.0


def _hue_gap(a, b):
    """Circular distance between two hues, 0..0.5 turns."""
    d = (a - b) % 1.0
    return min(d, 1.0 - d)


def _close(a, b, tol=1e-9):
    return max(abs(x - y) for x, y in zip(a, b)) < tol


def _zone_stations():
    """One representative station per zone band: (z1 nose, z2 middle, z3 tail)."""
    ys = [s[0] for s in mouse3d._STATIONS]
    out = []
    for i in range(3):
        band = [y for y in ys if mouse3d.zone_index(y) == i]
        out.append((band[0] + band[-1]) / 2.0)
    return out


def test_rainbow_colour_is_a_narrow_sweep_not_a_spectrum():
    """The rainbow is mostly *one colour* that drifts.

    The device's three zones sit close enough in hue that two of them usually
    read as the same colour, so the body has to hold a slice of the hue circle.
    Spread most of a turn down the mouse and you get three visibly different
    colours on one mouse -- a gradient, which is the effect this is not.
    """
    assert 0.0 < mouse3d._RAINBOW_TURNS <= 0.25

    # Pure hues everywhere: one channel at full, one at zero.
    for y in (-0.5, -0.3, 0.0, 0.2, 0.5):
        c = mouse3d.rainbow_colour(y)
        assert abs(max(c) - 1.0) < 1e-12 and abs(min(c)) < 1e-12, (y, c)

    # Nose and tail are exactly one sweep apart -- the slice runs the length
    # of the mouse and no further, at every point in the cycle.
    for p in (0.0, 0.31, 0.77):
        span = (_hue(mouse3d.rainbow_colour(-0.5, p))
                - _hue(mouse3d.rainbow_colour(0.5, p))) % 1.0
        assert abs(span - mouse3d._RAINBOW_TURNS) < 1e-9, (p, span)


def test_rainbow_leaves_two_zone_colours_looking_the_same():
    """Two of the three zones are the same colour most of the time.

    That is a property of the narrow sweep, and it is the user-visible one:
    the mouse reads as one colour drifting, not as three colours at once.
    """
    z1, z2, z3 = _zone_stations()
    assert z1 < z2 < z3  # nose, middle, tail

    for p in (0.0, 0.13, 0.42, 0.9):
        hues = [_hue(mouse3d.rainbow_colour(y, p)) for y in (z1, z2, z3)]
        near, far = (_hue_gap(hues[0], hues[1]), _hue_gap(hues[1], hues[2]))
        # Adjacent zones within ~30 deg of each other: the same colour to the
        # eye.  Nose to tail stays under half a turn too, so no two zones can
        # ever land on opposite sides of the circle.
        assert near < 0.085, (p, near, far)
        assert far < 0.085, (p, near, far)
        assert near + far <= mouse3d._RAINBOW_TURNS + 1e-9, (p, near, far)


def test_rainbow_colour_sweeps_continuously_along_the_body():
    """No jump between neighbouring stations: a wash, not three bands."""
    ys = [s[0] for s in mouse3d._STATIONS]
    lo, hi = ys[0], ys[-1]

    prev, worst = mouse3d.rainbow_colour(lo), 0.0
    for i in range(1, 401):
        cur = mouse3d.rainbow_colour(lo + (hi - lo) * i / 400.0)
        worst = max(worst, max(abs(a - b) for a, b in zip(cur, prev)))
        prev = cur
    assert worst < 0.05, worst  # no jump between neighbouring stations


def test_rainbow_walks_the_whole_hue_circle_over_one_period():
    """Narrow in space, full in time -- which is what makes it a rainbow.

    Any one frame is a slice; over a period every station passes through every
    primary, so the mouse visibly changes colour end to end.
    """
    for y in (-0.4, 0.0, 0.4):
        doms = {_dominant(mouse3d.rainbow_colour(y, i / 72.0))
                for i in range(72)}
        assert doms == {"r", "g", "b"}, (y, doms)


def test_rainbow_phase_rolls_the_sweep_from_the_nose_to_the_tail():
    """*phase* is the motion, and its direction is the point.

    Advancing the phase puts on a station the colour that sat *nearer the nose*
    a moment ago, so the sweep travels nose to tail -- top to bottom on screen,
    where z1 is drawn at the top.  The reverse would be the wrong effect.
    """
    assert _close(mouse3d.rainbow_colour(0.1, 1.0),
                  mouse3d.rainbow_colour(0.1))

    step = 0.25 / mouse3d._RAINBOW_TURNS  # turns of phase -> station offset
    for y in (-0.4, -0.05, 0.3):
        # y - step is closer to the nose: that is where this colour just was.
        assert _close(mouse3d.rainbow_colour(y, 0.25),
                      mouse3d.rainbow_colour(y - step))

    # And the nose leads the tail by exactly the sweep, which is what makes
    # the drift run downward rather than upward.
    for p in (0.0, 0.25, 0.6):
        lead = (_hue(mouse3d.rainbow_colour(-0.5, p))
                - _hue(mouse3d.rainbow_colour(0.5, p))) % 1.0
        assert abs(lead - mouse3d._RAINBOW_TURNS) < 1e-9, (p, lead)


def test_colour_at_takes_zone_colours_or_a_callback():
    """The renderer resolves either form, so the rainbow reuses the cached
    projection the zone colours use."""
    rgb = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    for y in (-0.5, -0.11, 0.0, 0.5):
        assert mouse3d._colour_at(rgb, y) == mouse3d.zone_colour(rgb, y)
        assert mouse3d._colour_at(mouse3d.rainbow_colour, y) == \
            mouse3d.rainbow_colour(y)


def test_render_lighting_accepts_a_colour_callback():
    w, h = 260, 340
    _view, geo = _lighting_geometry(w, h)
    seen = []

    def cb(y):
        seen.append(y)
        return mouse3d.rainbow_colour(y)

    cr = _FakeCR()
    assert mouse3d.render_lighting(cr, w, h, geo, cb) is geo

    # Sampled per station across the whole body: not once, and not only where
    # the zone bands happen to fall.
    assert len(seen) > 20
    assert min(seen) < -0.45 and max(seen) > 0.45

    # The shell is genuinely a spectrum over time.  The same geometry drawn
    # with one flat colour leads on a single channel; the rainbow has to lead
    # on all three -- but not in any *one* frame, since a frame is a narrow
    # slice, so the whole cycle is what turns up the three.
    flat = _FakeCR()
    mouse3d.render_lighting(flat, w, h, geo, ((0.5, 0.5, 0.5),) * 3)
    assert {_dominant(c) for c in flat.colours} == {"r"}

    doms = set()
    for i in range(24):
        frame = _FakeCR()
        mouse3d.render_lighting(
            frame, w, h, geo,
            lambda y, p=i / 24.0: mouse3d.rainbow_colour(y, p))
        doms |= {_dominant(c) for c in frame.colours}
    assert doms == {"r", "g", "b"}
