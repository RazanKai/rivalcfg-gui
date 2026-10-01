"""Tests for the colour helpers and picker in widgets.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import colorutil as widgets  # noqa: E402


def test_normalize_hex_variants():
    assert widgets.normalize_hex("#FF0000") == "ff0000"
    assert widgets.normalize_hex("f00") == "ff0000"
    assert widgets.normalize_hex("00ff00") == "00ff00"
    assert widgets.normalize_hex("") == "ff6600"
    assert widgets.normalize_hex("nonsense", "123456") == "123456"


def test_hex_rgb_roundtrip():
    assert widgets.hex_to_rgb("ff0000") == (255, 0, 0)
    assert widgets.rgb_to_hex(0, 255, 0) == "00ff00"
    assert widgets.rgb_to_hex(300, -5, 128) == "ff0080"


def test_hsv_roundtrip_is_stable():
    for hexv in ("ff0000", "00ff00", "0000ff", "123456", "ffffff", "000000"):
        h, s, v = widgets.hex_to_hsv(hexv)
        assert widgets.hsv_to_hex(h, s, v) == hexv


def test_hsv_primitives():
    # Pure red is hue 0, full sat/value.
    assert widgets.hex_to_hsv("ff0000") == (0.0, 1.0, 1.0)
    assert widgets.hsv_to_hex(1 / 3, 1.0, 1.0) == "00ff00"
    assert widgets.hsv_to_hex(2 / 3, 1.0, 1.0) == "0000ff"


def test_clamp():
    assert widgets.clamp(5, 0, 1) == 1
    assert widgets.clamp(-2, 0, 1) == 0
    assert widgets.clamp(0.5, 0, 1) == 0.5
