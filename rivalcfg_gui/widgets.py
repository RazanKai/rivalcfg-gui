"""Reusable GTK3 widgets for rivalcfg-gui.

Contains :class:`ColorPickerButton`: a button that shows the current colour and
opens a popover with a 2D saturation/value gradient, a hue strip and a hex entry
field. ``Gtk.ColorButton`` is intentionally not used (its palette cannot show
exact LED primaries and hides the hex value).
"""

import colorsys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

import cairo  # noqa: E402

from .colorutil import (  # noqa: E402,F401
    clamp,
    hex_to_hsv,
    hex_to_rgb,
    hsv_to_hex,
    normalize_hex,
)


#: Shortcut palette offered in the editor. First four are the true LED
#: primaries so the device can be verified against a pure colour.
PALETTE = [
    ("Red", "ff0000"),
    ("Green", "00ff00"),
    ("Blue", "0000ff"),
    ("White", "ffffff"),
    ("Yellow", "ffff00"),
    ("Cyan", "00ffff"),
    ("Magenta", "ff00ff"),
    ("Orange", "ff6600"),
]


class ColorSwatch(Gtk.Button):
    """A small colour chip. Clicking it (optionally) selects an edit target."""

    def __init__(self, initial_hex="ff6600", on_click=None, width=54, height=26):
        super().__init__()
        self._hex = normalize_hex(initial_hex)
        self._selected = False
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_size_request(width, height)

        self._area = Gtk.DrawingArea()
        self._area.set_size_request(width, height)
        self._area.connect("draw", self._draw)
        self.add(self._area)
        if on_click is not None:
            self.connect("clicked", lambda _b: on_click())

    def get_hex(self):
        return self._hex

    def set_hex(self, hexv):
        self._hex = normalize_hex(hexv)
        self._area.queue_draw()

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._area.queue_draw()

    def _draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r, g, b = (c / 255.0 for c in hex_to_rgb(self._hex))
        cr.set_source_rgb(r, g, b)
        cr.rectangle(1, 1, w - 2, h - 2)
        cr.fill_preserve()
        if self._selected:
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2)
        else:
            cr.set_source_rgba(1, 1, 1, 0.20)
            cr.set_line_width(1)
        cr.stroke()
        return False


class ColorEditor(Gtk.Box):
    """Embedded HSV editor: gradient, hue strip, hex entry and palette.

    Not a popover -- meant to live in a page column. Calls ``on_changed(hex)``
    live while dragging / typing.
    """

    def __init__(self, on_changed=None, initial_hex="ff6600"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._on_changed = on_changed
        self._hex = normalize_hex(initial_hex)
        self._h, self._s, self._v = hex_to_hsv(self._hex)
        self._dragging = None
        self._updating_entry = False

        self._sv = Gtk.DrawingArea()
        self._sv.set_size_request(280, 170)
        self._sv.add_events(Gdk.EventMask.BUTTON1_MOTION_MASK
                            | Gdk.EventMask.BUTTON_PRESS_MASK
                            | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self._sv.connect("draw", self._draw_sv)
        self._sv.connect("button-press-event", self._sv_press)
        self._sv.connect("motion-notify-event", self._sv_motion)
        self._sv.connect("button-release-event", self._release)
        self.pack_start(self._sv, False, False, 0)

        self._hue = Gtk.DrawingArea()
        self._hue.set_size_request(280, 20)
        self._hue.add_events(Gdk.EventMask.BUTTON1_MOTION_MASK
                             | Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self._hue.connect("draw", self._draw_hue)
        self._hue.connect("button-press-event", self._hue_press)
        self._hue.connect("motion-notify-event", self._hue_motion)
        self._hue.connect("button-release-event", self._release)
        self.pack_start(self._hue, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._preview = ColorSwatch(self._hex, width=42, height=26)
        row.pack_start(self._preview, False, False, 0)
        row.pack_start(Gtk.Label(label="#"), False, False, 0)
        self._entry = Gtk.Entry()
        self._entry.set_max_length(7)
        self._entry.set_width_chars(8)
        self._entry.set_text(self._hex)
        self._entry.connect("activate", self._entry_activate)
        self._entry.connect("focus-out-event", self._entry_activate)
        row.pack_start(self._entry, False, False, 0)
        self.pack_start(row, False, False, 0)

        palette_label = Gtk.Label(label="Shortcuts")
        palette_label.get_style_context().add_class("setting-desc")
        palette_label.set_halign(Gtk.Align.START)
        self.pack_start(palette_label, False, False, 0)

        pal = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pal.set_halign(Gtk.Align.START)
        for name, hexv in PALETTE:
            sw = Gtk.Button()
            sw.set_relief(Gtk.ReliefStyle.NONE)
            sw.set_tooltip_text(name)
            sw.set_size_request(30, 24)
            area = Gtk.DrawingArea()
            area.set_size_request(30, 24)
            area.connect("draw", self._palette_draw, hexv)
            sw.add(area)
            sw.connect("clicked", lambda _b, h=hexv: self._set_and_notify(h))
            pal.pack_start(sw, False, False, 0)
        self.pack_start(pal, False, False, 0)

    # -- public ------------------------------------------------------------
    def get_hex(self):
        return self._hex

    def set_color(self, hexv, notify=False):
        self._hex = normalize_hex(hexv)
        self._h, self._s, self._v = hex_to_hsv(self._hex)
        self._updating_entry = True
        self._entry.set_text(self._hex)
        self._updating_entry = False
        self._preview.set_hex(self._hex)
        self._sv.queue_draw()
        self._hue.queue_draw()
        if notify and self._on_changed:
            self._on_changed(self._hex)

    # -- internals ---------------------------------------------------------
    def _set_and_notify(self, hexv):
        self.set_color(hexv)
        if self._on_changed:
            self._on_changed(self._hex)

    def _commit(self):
        self._hex = hsv_to_hex(self._h, self._s, self._v)
        self._updating_entry = True
        self._entry.set_text(self._hex)
        self._updating_entry = False
        self._preview.set_hex(self._hex)
        self._sv.queue_draw()
        self._hue.queue_draw()
        if self._on_changed:
            self._on_changed(self._hex)

    def _release(self, _w, _e):
        self._dragging = None
        return False

    def _sv_press(self, _w, event):
        self._dragging = "sv"
        self._sv_apply(event.x, event.y)
        return True

    def _sv_motion(self, _w, event):
        if self._dragging == "sv":
            self._sv_apply(event.x, event.y)
            return True
        return False

    def _sv_apply(self, x, y):
        w = self._sv.get_allocated_width() or 280
        h = self._sv.get_allocated_height() or 170
        self._s = clamp(x / max(1, w), 0, 1)
        self._v = 1.0 - clamp(y / max(1, h), 0, 1)
        self._commit()

    def _hue_press(self, _w, event):
        self._dragging = "hue"
        self._hue_apply(event.x)
        return True

    def _hue_motion(self, _w, event):
        if self._dragging == "hue":
            self._hue_apply(event.x)
            return True
        return False

    def _hue_apply(self, x):
        w = self._hue.get_allocated_width() or 280
        self._h = clamp(x / max(1, w), 0, 1)
        self._commit()

    def _entry_activate(self, *_a):
        if self._updating_entry:
            return False
        self.set_color(self._entry.get_text() or self._hex)
        if self._on_changed:
            self._on_changed(self._hex)
        return False

    # -- drawing -----------------------------------------------------------
    def _palette_draw(self, widget, cr, hexv):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r, g, b = (c / 255.0 for c in hex_to_rgb(hexv))
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        return False

    def _draw_sv(self, widget, cr):
        w = widget.get_allocated_width() or 280
        h = widget.get_allocated_height() or 170
        # Saturation: white -> pure hue (horizontal).
        r1, g1, b1 = colorsys.hsv_to_rgb(self._h, 1.0, 1.0)
        lin = cairo.LinearGradient(0, 0, w, 0)
        lin.add_color_stop_rgb(0, 1, 1, 1)
        lin.add_color_stop_rgb(1, r1, g1, b1)
        cr.set_source(lin)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # Value: transparent -> black (vertical).
        lin2 = cairo.LinearGradient(0, 0, 0, h)
        lin2.add_color_stop_rgba(0, 0, 0, 0, 0)
        lin2.add_color_stop_rgba(1, 0, 0, 0, 1)
        cr.set_source(lin2)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # Marker.
        mx, my = self._s * w, (1.0 - self._v) * h
        cr.set_line_width(2)
        cr.set_source_rgb(0, 0, 0)
        cr.arc(mx, my, 7, 0, 6.2832)
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        cr.arc(mx, my, 6, 0, 6.2832)
        cr.stroke()
        return False

    def _draw_hue(self, widget, cr):
        w = widget.get_allocated_width() or 280
        h = widget.get_allocated_height() or 20
        lin = cairo.LinearGradient(0, 0, w, 0)
        stops = 12
        for i in range(stops + 1):
            t = i / stops
            r, g, b = colorsys.hsv_to_rgb(t, 1.0, 1.0)
            lin.add_color_stop_rgb(t, r, g, b)
        cr.set_source(lin)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        mx = self._h * w
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2)
        cr.rectangle(clamp(mx - 2, 0, w - 4), 0, 4, h)
        cr.stroke()
        return False

