"""Pure colour helpers (no GTK import) shared by the GUI and the picker."""

import colorsys


def clamp(value, lo, hi):
    return lo if value < lo else (hi if value > hi else value)


def normalize_hex(value, default="ff6600"):
    """Return a bare lowercase 6-digit hex string for *value*."""
    if not value:
        return default
    s = str(value).strip().lstrip("#").lower()
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        try:
            int(s, 16)
            return s
        except ValueError:
            return default
    return default


def hex_to_rgb(hexv):
    h = normalize_hex(hexv, "000000")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r, g, b):
    return "%02x%02x%02x" % (int(clamp(round(r), 0, 255)),
                             int(clamp(round(g), 0, 255)),
                             int(clamp(round(b), 0, 255)))


def hex_to_hsv(hexv):
    r, g, b = (c / 255.0 for c in hex_to_rgb(hexv))
    return colorsys.rgb_to_hsv(r, g, b)


def hsv_to_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(clamp(h, 0, 1), clamp(s, 0, 1), clamp(v, 0, 1))
    return rgb_to_hex(r * 255, g * 255, b * 255)
