"""Tests for lighting_fx: the host-side effect maths.

GTK-free and device-free by design -- the effects are pure functions of time,
which is what lets them be checked here at all.  What the numbers *look* like
on the mouse is the in-process preview runs' job (see WORKLOG).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lighting_fx as fx  # noqa: E402

PALETTE = ("ff0000", "00ff00", "0000ff")


def _distance(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def test_normalize_palette_repairs_unusable_input():
    # One colour is not a loop and none at all has nothing to show, so both
    # fall back rather than raising -- the GUI can hold these states mid-edit.
    assert fx.normalize_palette(["ff0000"]) == list(fx.DEFAULT_PALETTE)
    assert fx.normalize_palette([]) == list(fx.DEFAULT_PALETTE)
    assert fx.normalize_palette(None) == list(fx.DEFAULT_PALETTE)


def test_normalize_palette_caps_at_four():
    six = ["ff0000", "00ff00", "0000ff", "ffff00", "ff00ff", "00ffff"]
    assert len(fx.normalize_palette(six)) == fx.MAX_PALETTE


def test_normalize_palette_accepts_short_and_named_forms():
    # The picker hands over bare hex, but profiles and named colours must not
    # crash the animation loop.
    assert fx.normalize_palette(["#f00", "0f0"]) == ["ff0000", "00ff00"]


def test_palette_at_is_a_closed_loop():
    """The wrap from the last colour back to the first has no seam.

    Without it the mouse would jump once per cycle, which reads as a glitch
    rather than an effect.
    """
    for u in (0.0, 0.25, 0.7):
        assert _distance(
            fx.palette_at(PALETTE, u), fx.palette_at(PALETTE, u + 1.0)
        ) == pytest.approx(0.0, abs=1e-9)


def test_palette_at_visits_every_palette_colour():
    seen = [fx.palette_at(PALETTE, i / 3.0) for i in range(3)]
    expected = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    for got, want in zip(seen, expected):
        assert _distance(got, want) == pytest.approx(0.0, abs=1e-9)


def test_palette_at_is_continuous():
    """Neighbouring positions stay close -- no step between frames."""
    steps = 400
    prev = fx.palette_at(PALETTE, 0.0)
    for i in range(1, steps + 1):
        cur = fx.palette_at(PALETTE, i / steps)
        assert _distance(prev, cur) < 0.05, "palette jumps at step %d" % i
        prev = cur


@pytest.mark.parametrize("effect", ["colorshift", "breathe"])
def test_colours_at_returns_one_colour_per_zone(effect):
    out = fx.colours_at(effect, PALETTE, 8.0, 1.0)
    assert len(out) == 3
    for r, g, b in out:
        assert 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0


def test_colorshift_spreads_the_zones_apart():
    """The zones are offset, so the palette travels along the body.

    Exactly equal zones would read as one lamp rather than a shift.
    """
    out = fx.colours_at("colorshift", PALETTE, 8.0, 0.0)
    assert _distance(out[0], out[2]) > 0.02


def test_colorshift_is_continuous_over_time():
    prev = fx.colours_at("colorshift", PALETTE, 8.0, 0.0)
    for i in range(1, 600):
        cur = fx.colours_at("colorshift", PALETTE, 8.0, i * 0.02)
        for a, b in zip(prev, cur):
            assert _distance(a, b) < 0.08, "jump at frame %d" % i
        prev = cur


def test_breathe_pulses_the_whole_mouse_together():
    out = fx.colours_at("breathe", PALETTE, 8.0, 2.0)
    for zone in out[1:]:
        assert _distance(out[0], zone) == pytest.approx(0.0, abs=1e-9)


def test_breath_level_rises_and_falls_within_one_cycle():
    # Sample inclusive of both ends, so 79/80 is not mistaken for a full cycle.
    levels = [fx.breath_level(i / 80.0) for i in range(81)]
    assert max(levels) == pytest.approx(1.0, abs=1e-9)
    # One breath per cycle: the cycle is dimmest at both ends.
    assert levels[0] == pytest.approx(levels[-1], abs=1e-9)
    assert levels[0] == pytest.approx(fx.BREATH_FLOOR, abs=1e-9)
    assert levels[40] == pytest.approx(1.0, abs=1e-9)  # peak at the half cycle


def test_breath_level_never_goes_fully_dark():
    """A pulse reaching zero looks like the mouse switching off, not breathing."""
    for i in range(200):
        assert fx.breath_level(i / 200.0) >= fx.BREATH_FLOOR - 1e-9


def test_breathe_pulses_the_colour_by_the_envelope():
    """The finished colour is the palette entry scaled by the envelope."""
    speed = 8.0
    for i in range(40):
        u = i / 40.0
        out = fx.colours_at("breathe", PALETTE, speed, speed * u)[0]
        base = fx.palette_at(PALETTE, u * fx.BREATHE_COLOUR_DRIFT)
        level = fx.breath_level(u)
        assert _distance(out, tuple(c * level for c in base)) < 1e-9


def test_unknown_effect_still_shows_the_palette():
    """Falling back to ColorShift means a wrong effect, never a dark mouse."""
    out = fx.colours_at("nonsense", PALETTE, 8.0, 0.0)
    assert _distance(out[0], fx.colours_at("colorshift", PALETTE, 8.0, 0.0)[0]) == 0.0


@pytest.mark.parametrize("bad", [0, -4, None, "fast"])
def test_bad_speed_falls_back_instead_of_dividing_by_zero(bad):
    out = fx.colours_at("colorshift", PALETTE, bad, 1.0)
    assert len(out) == 3


def test_to_hex_produces_bare_six_digit_colours():
    assert fx.to_hex([(1.0, 0.0, 0.0)]) == ["ff0000"]
    assert fx.to_hex([(0.0, 0.0, 0.0)]) == ["000000"]
    # The CLI takes these directly, and rgbcolor rejects anything with a '#'.
    assert all("#" not in c for c in fx.to_hex(fx.colours_at("breathe", PALETTE, 8.0, 0.5)))
