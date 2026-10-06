"""Host-side lighting effects (ColorShift, Breathe) -- pure maths, no GTK.

SteelSeries GG offers two effects the Aerox 5 Wireless cannot store onboard:
the mouse's firmware knows only a static colour per zone, a click-flash colour
and one rainbow animation, and GG animates the rest *on the PC*, streaming
colours over USB.  The official guide says so outright -- colour customisations
are "software driven" and are not written to the mouse's memory.

So parity means doing the same thing here: this module computes what the three
zones should show at a given instant, and :class:`device_core.LightingAnimator`
writes it.  Everything here is a pure function of time, which is what makes the
effects testable without a display or a mouse attached -- the same reason
:mod:`mouse3d` keeps its colour maths separate from its rendering.

Only the maths lives here.  The tick and the HID writes live elsewhere: the GUI
already knows how to drive a timer that stops when the page is off screen (see
``_RAINBOW_TICK_MS`` in ``app.py``), and device_core owns device I/O.
"""

from __future__ import annotations

import math

from .colorutil import hex_to_rgb, rgb_to_hex

#: Effects this module can animate.  The GUI offers these as lighting modes
#: alongside the two the firmware does itself (Steady, Rainbow).
FX_EFFECTS = ("colorshift", "breathe")

#: How far apart neighbouring zones sit in the palette loop, as a fraction of
#: the whole loop.  Zero would paint all three zones the same colour and the
#: mouse would read as one lamp; a third of the loop would put every zone on a
#: different colour and the "shift" stops reading as a single moving thing.
#: At 0.08 the zones are close enough to look like one colour travelling along
#: the body -- the same reasoning as ``mouse3d._RAINBOW_TURNS``, and for the
#: same reason: the effect is mostly one colour that drifts.
ZONE_SPREAD = 0.08

#: Breathe never goes fully dark.  A pulse that reaches zero looks like the
#: mouse switching off twice a second, which is a different (and alarming)
#: effect; the firmware's own dimming keeps a floor too.
BREATH_FLOOR = 0.12

#: Palette loops per breath -- at 0.5 the colour drifts half way round the
#: palette on each breath, so the mouse has shown the whole palette after two.
BREATHE_COLOUR_DRIFT = 0.5

#: Bounds for the speed control, in seconds per cycle.  GG's slider runs about
#: 2 s to 30 s; below ~2 s ColorShift stops reading as a shift and above ~30 s
#: it looks frozen.
SPEED_MIN = 2.0
SPEED_MAX = 30.0
SPEED_DEFAULT = 8.0

#: What the palette starts as -- four well-separated hues, the way GG's
#: "Multi Color Breathe" suggests four colours to begin with.
DEFAULT_PALETTE = ("ff3b30", "ffcc00", "34c759", "0a84ff")

#: GG caps the palette at four colours ("Add up to 4 colors using the editor").
MAX_PALETTE = 4
MIN_PALETTE = 2


def normalize_palette(palette):
    """A usable palette: 2..4 valid hex colours, falling back to the default.

    A palette of one colour has no loop to travel round, and an empty one has
    nothing to show, so both are repaired rather than rejected -- the GUI can
    hold this state for a moment while the user edits.
    """
    out = []
    for value in palette or ():
        if value:
            out.append(rgb_to_hex(*hex_to_rgb(value)))
    if len(out) < MIN_PALETTE:
        return list(DEFAULT_PALETTE)
    return out[:MAX_PALETTE]


def palette_at(palette, u):
    """Colour at position *u* around the palette loop, as 0..1 RGB floats.

    *u* is in loops, not 0..1 -- any float works, and the whole part is the
    number of times round.  Colours are interpolated in RGB between adjacent
    palette entries and the last segment wraps back to the first, so the loop
    has no seam: without that wrap the mouse would jump from the last colour
    back to the first once per cycle.
    """
    colors = [hex_to_rgb(c) for c in normalize_palette(palette)]
    n = len(colors)
    pos = (u % 1.0) * n
    i = int(math.floor(pos))
    frac = pos - i
    a = colors[i % n]
    b = colors[(i + 1) % n]
    return tuple((a[c] + (b[c] - a[c]) * frac) / 255.0 for c in range(3))


def breath_level(u):
    """The breathe envelope at *u* cycles, in ``BREATH_FLOOR``..1.

    Kept apart from :func:`colours_at` so the pulse can be checked without a
    palette in the way: a colour mid-way between two palette entries has no
    channel at full scale, so reading the envelope off the finished colour
    would confuse "the pulse is dim" with "this hue is not saturated", and the
    floor would look broken whenever the palette was interpolating.

    ``cos`` starts at its trough, so the mouse is at its dimmest on the frame
    the effect begins and brightens from there.
    """
    return BREATH_FLOOR + (1.0 - BREATH_FLOOR) * 0.5 * (1.0 - math.cos(2.0 * math.pi * u))


def colours_at(effect, palette, speed, t, zones=3):
    """The colours the zones show for *effect* at *t* seconds.

    Returns *zones* ``(r, g, b)`` triples of 0..1 floats, nose to tail.

    *speed* is seconds per cycle.  ColorShift walks the palette round the body,
    each zone a little behind the last, so the colours travel along it;
    Breathe pulses the whole mouse together, drifting through the palette as it
    goes.  Both are continuous in *t* -- no discontinuities between frames --
    which is what the animation test checks.
    """
    palette = normalize_palette(palette)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = SPEED_DEFAULT
    if speed <= 0:
        speed = SPEED_DEFAULT
    u = t / speed

    if effect == "breathe":
        level = breath_level(u)
        base = palette_at(palette, u * BREATHE_COLOUR_DRIFT)
        return [tuple(c * level for c in base) for _ in range(zones)]

    # ColorShift, and anything unrecognised -- showing the palette is the safer
    # default: at worst the effect is the wrong one, never a dark mouse.
    return [palette_at(palette, u + i * ZONE_SPREAD) for i in range(zones)]


def to_hex(colours):
    """0..1 RGB triples -> bare 6-digit hex, the form rivalcfg's CLI accepts."""
    return [rgb_to_hex(r * 255.0, g * 255.0, b * 255.0) for r, g, b in colours]
