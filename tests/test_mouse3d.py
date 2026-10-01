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
    # Real proportions of the Aerox 5 (~128.8 x 68.6 x 42.1 mm), length
    # normalised to 1: half-width ~0.266, top height ~0.32 at the middle.
    assert 0.24 < a < 0.30
    assert 0.28 < t < 0.35


def test_wireframe_has_rings_and_longitudinals():
    rings, longs = mouse3d.build_wireframe()
    assert len(rings) == len(mouse3d._STATIONS)
    assert len(longs) == mouse3d._RING_STEPS
    assert all(len(r) == mouse3d._RING_STEPS for r in rings)


def test_top_surface_is_highest_at_centre():
    z_center = mouse3d.top_surface_z(0.0, 0.0)
    z_edge = mouse3d.top_surface_z(0.34, 0.0)
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


def _hole_span(hole):
    xs = [p.x for p in hole]
    ys = [p.y for p in hole]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def test_honeycomb_is_coarse():
    """The real mouse has a relatively coarse perforation, not a fine mesh."""
    assert 40 <= len(mouse3d.DETAILS["holes"]) <= 160


def test_honeycomb_spans_crown_to_flank():
    """Holes run from the crown all the way down to the base plate."""
    holes = mouse3d.DETAILS["holes"]
    zmean = [sum(p.z for p in h) / len(h) for h in holes]
    assert min(zmean) < 0.22      # reaches low on the flank
    assert max(zmean) > 0.30      # and is present on the crown


def test_honeycomb_holes_vary_in_size():
    """The holes are not all the same size: they grow from crown to flank and
    from nose to tail."""
    holes = mouse3d.DETAILS["holes"]
    sizes = [_hole_span(h) for h in holes]
    assert max(sizes) > 1.2 * min(sizes)


def test_honeycomb_reaches_under_the_keycap():
    """The keycap's lower edge carries holes too (the perforation starts
    forward of where the keycap ends)."""
    holes = mouse3d.DETAILS["holes"]
    assert any(max(p.y for p in h) < 0.0 for h in holes)


def test_lever_spans_the_thumb_buttons():
    """The long up/down flick lever sits over both thumb buttons."""
    def yspan(key):
        poly = mouse3d.BUTTONS[key][1]
        return min(p.y for p in poly), max(p.y for p in poly)

    lo7, hi7 = yspan("button7")
    spans = [yspan("button4"), yspan("button5")]
    assert lo7 <= min(lo for lo, _hi in spans) + 0.03
    assert hi7 >= max(hi for _lo, hi in spans) - 0.03


def test_thumb_buttons_are_adjacent():
    """Buttons 4 and 5 are adjacent (no plastic gap between them)."""
    _l, front = mouse3d.BUTTONS["button5"]
    _l, rear = mouse3d.BUTTONS["button4"]
    gap = min(p.y for p in rear) - max(p.y for p in front)
    assert abs(gap) < 0.02


def test_click_panels_are_symmetric():
    _l, left = mouse3d.BUTTONS["button1"]
    _l, right = mouse3d.BUTTONS["button2"]
    lx = sum(p.x for p in left) / len(left)
    rx = sum(p.x for p in right) / len(right)
    assert abs(lx + rx) < 1e-6  # mirrored about x=0


def test_click_panels_converge_toward_the_nose():
    """The real keycaps are widest at the rear and taper continuously to a
    narrow leading edge at the nose (top view): the width grows monotonically
    from the leading edge to the widest point."""
    _label, poly = mouse3d.BUTTONS["button1"]
    rows = {}
    for p in poly:
        rows.setdefault(round(p.y, 4), []).append(abs(p.x))
    prof = [(y, max(rows[y])) for y in sorted(rows)]
    front_w = prof[0][1]
    widest = max(w for _y, w in prof)
    # the leading edge is much narrower than the widest point
    assert front_w < 0.5 * widest
    # and the width never decreases from the nose toward the widest point
    peak = max(range(len(prof)), key=lambda i: prof[i][1])
    seq = [w for _y, w in prof[:peak + 1]]
    assert all(seq[i] <= seq[i + 1] + 1e-6 for i in range(len(seq) - 1))


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
    """In front of the wheel slot the two keycaps meet at the centreline."""
    _label, poly = mouse3d.BUTTONS["button1"]
    # clearly forward of the wheel slot: the inner edge is on the centreline
    front = [abs(p.x) for p in poly if p.y < -0.44]
    assert front and min(front) < 0.025


def test_keycaps_clear_the_wheel_and_dpi():
    """Behind the wheel slot the centre split widens into a channel: no
    keycap point intrudes into the wheel's or the CPI button's footprint."""
    w = mouse3d._mesh.WHEEL
    hw = w["hw"]
    wf, wb = w["cy"] - w["r"], w["cy"] + w["r"]
    dpi = mouse3d._mesh.CAPS["button6"]
    dx = max(abs(p[0]) for p in dpi)
    dy0, dy1 = min(p[1] for p in dpi), max(p[1] for p in dpi)

    for key in ("button1", "button2"):
        for p in mouse3d.BUTTONS[key][1]:
            if wf - 0.004 < p.y < wb + 0.004:
                assert abs(p.x) >= hw - 1e-6, (key, p)   # clear of the wheel
            if dy0 - 0.003 < p.y < dy1 + 0.003:
                assert abs(p.x) >= dx - 1e-6, (key, p)   # clear of the CPI


def test_keycaps_have_split_channel_after_the_wheel():
    """After the wheel the two caps sit either side of a centre channel, not
    on the centreline (the gap opens from the wheel front onward)."""
    _label, poly = mouse3d.BUTTONS["button1"]
    channel = [abs(p.x) for p in poly if -0.40 < p.y < -0.09]
    assert channel and min(channel) > 0.03


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
    local shell height and must keep a margin from the top ridge."""
    for key in ("button4", "button5", "button7", "button8", "button9"):
        _label, poly = mouse3d.BUTTONS[key]
        for p in poly:
            _a, zb, zt = mouse3d._interp_station(p.y)
            # the cap is lifted a hair clear of the shell surface so it
            # reads over the wireframe; allow that tiny epsilon.
            assert zb - 0.004 <= p.z <= zt + 1e-9, (key, p)
            zf = (p.z - zb) / max(1e-9, zt - zb)
            assert zf <= 0.9, (key, p)


def test_click_panels_clear_side_buttons():
    """The click keycaps live on the top surface: every point sits above the
    shell mid-height, so they never spill onto the side-button flank."""
    for key in ("button1", "button2"):
        _label, poly = mouse3d.BUTTONS[key]
        for p in poly:
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
