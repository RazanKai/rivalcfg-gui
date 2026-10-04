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
    front button and the front half of the rear one, as on the real mouse."""
    def yspan(key):
        poly = mouse3d.BUTTONS[key][1]
        return min(p.y for p in poly), max(p.y for p in poly)

    lo7, hi7 = yspan("button7")
    lo5, hi5 = yspan("button5")
    lo4, hi4 = yspan("button4")
    assert lo7 <= lo5 + 0.01              # starts with the front button
    assert hi7 >= hi5                     # covers the front button entirely
    assert lo7 <= lo4                     # reaches into the rear button
    assert hi7 >= lo4 + 0.5 * (hi4 - lo4)  # and past its midpoint


def test_thumb_buttons_are_adjacent():
    """Buttons 4 and 5 are adjacent (no plastic gap between them)."""
    _l, front = mouse3d.BUTTONS["button5"]
    _l, rear = mouse3d.BUTTONS["button4"]
    gap = min(p.y for p in rear) - max(p.y for p in front)
    assert abs(gap) < 0.02


def test_click_panels_are_symmetric():
    """The two keycaps mirror each other about the centreline.

    The model's own right-hand cap is wider (its source breaks the flank
    asymmetrically), so both panels are drawn from the *left* one's part
    line, mirrored -- the pair is symmetric to within the resampling."""
    _l, left = mouse3d.BUTTONS["button1"]
    _l, right = mouse3d.BUTTONS["button2"]
    lx = sum(p.x for p in left) / len(left)
    rx = sum(p.x for p in right) / len(right)
    assert abs(lx + rx) < 0.03, abs(lx + rx)


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


def test_keycaps_have_split_channel_after_the_wheel():
    """After the wheel the two caps sit either side of a centre channel.

    It stays open the whole way back -- neither cap ever crosses onto the
    other's side -- but it pinches to a hairline between the wheel well and
    the CPI pill, where the v3 model's two panels abut along a narrow crowned
    spine, before opening again around the pill."""
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
        # the pinch between the wheel well (ends ~37 mm) and the CPI pill
        # (starts ~46 mm) is well under a millimetre a side
        neck = [x for s, x in channel if 40.5 < s < 44.5]
        assert neck and min(neck) < 0.5, (key, min(neck))
        # alongside the wheel well proper the split is several mm wide
        wide = [x for s, x in channel if 18.0 < s < 36.0]
        assert wide and min(wide) > 3.0, (key, min(wide))


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

    def save(self):
        pass

    def restore(self):
        pass

    def set_line_join(self, _j):
        pass

    def set_source_rgba(self, *_c):
        pass

    def set_source_rgb(self, *_c):
        pass

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
