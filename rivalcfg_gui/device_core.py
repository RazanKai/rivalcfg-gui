"""Device identity, capabilities and serialized write plans for rivalcfg-gui.

This module is deliberately GTK-free so it can be unit-tested headlessly and
reused by the GUI. It owns three things (see ``PLAN.md`` Phase 1):

* :class:`DeviceManager` -- device enumeration + capability derivation, with a
  library-first (``rivalcfg.devices``) and CLI-scraping fallback strategy.
* :class:`CommandQueue` -- the single writer. Every rivalcfg invocation goes
  through one background worker so HID commands never interleave. Supports
  debounced/coalesced updates (slider drags) and atomic :class:`ApplyPlan`s.
* :class:`ApplyPlan` + plan builders -- one user action is one ordered plan.
  The canonical lighting order (zone colours -> reactive -> default lighting ->
  rainbow last) is enforced here, in exactly one place.
"""

from __future__ import annotations

import logging
import os
import queue as _queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Union


# ---------------------------------------------------------------------------
# Button helpers
# ---------------------------------------------------------------------------

#: Human readable labels for button assignment values (UI chips/popovers).
ACTION_LABELS = {
    "button1": "Left click",
    "button2": "Right click",
    "button3": "Middle click",
    "button4": "Button 4",
    "button5": "Button 5",
    "button6": "Button 6",
    "button7": "Button 7",
    "button8": "Button 8",
    "button9": "Button 9",
    "dpi": "DPI cycle",
    "scrollup": "Scroll up",
    "scrolldown": "Scroll down",
    "disabled": "Disabled",
    "disable": "Disabled",
    "default": "Default",
}

#: Multimedia actions, in the order shown in the popover. Values are the
#: canonical names understood by rivalcfg's buttons handler.
MULTIMEDIA_ACTIONS = [
    ("PlayPause", "Play / Pause"),
    ("Previous", "Previous track"),
    ("Next", "Next track"),
    ("VolumeUp", "Volume up"),
    ("VolumeDown", "Volume down"),
    ("Mute", "Mute"),
]

#: Keyboard keys offered in the popover when the full qwerty layout cannot be
#: imported. Values are rivalcfg qwerty key names.
_FALLBACK_KEYS = [
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "Enter", "Escape", "BackSpace", "Tab", "Space", "Delete",
    "Home", "End", "PageUp", "PageDown", "Insert",
    "Up", "Down", "Left", "Right",
    "LeftCtrl", "LeftShift", "LeftAlt", "LeftSuper",
]


#: Grouping of the offered keys, in the order the groups are shown. Members are
#: rivalcfg qwerty key names; a name no group claims falls into "Other" rather
#: than disappearing from the list. The ids are English msgids, translated by
#: the UI.
KEY_GROUPS = [
    ("Letters", [chr(c) for c in range(ord("A"), ord("Z") + 1)]),
    ("Numbers", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]),
    ("Editing", ["Enter", "Escape", "BackSpace", "Tab", "Space", "Delete",
                 "CapsLock"]),
    ("Punctuation", ["-", "=", "[", "]", "\\", ";", "'", "`", ",", ".", "/"]),
    ("Function keys", ["F%d" % i for i in range(1, 25)]),
    ("Navigation", ["PrintScreen", "ScrollLock", "PauseBreak", "NumLock",
                    "ContextMenu", "Insert", "Home", "PageUp", "End",
                    "PageDown", "Left", "Right", "Up", "Down"]),
    ("Modifiers", ["LeftCtrl", "LeftShift", "LeftAlt", "LeftSuper",
                   "RightCtrl", "RightShift", "RightAlt", "RightSuper"]),
]


def _key_is_offered(name: str) -> bool:
    """Whether a layout name belongs in the picker.

    Drops the keypad, the international ``\\(inter)`` key, and ``#`` -- on a
    qwerty layout that is Shift+3, not a physical key of its own.
    """
    if "(" in name or ")" in name or name.startswith("Keypad"):
        return False
    return name != "#"


def _layout_table() -> dict:
    """Canonical rivalcfg key name -> HID usage code, or ``{}`` if unavailable."""
    try:
        from rivalcfg.handlers.buttons import layout_qwerty  # type: ignore

        return {name: int(code) for name, code in layout_qwerty.layout.items()}
    except Exception:
        return {}


def keyboard_groups() -> list[tuple[str, list[str]]]:
    """Offered keys as ``[(group_id, [key, ...]), ...]``, in keyboard order.

    Sorting by HID usage code reproduces the order the keys sit in on a
    keyboard -- letters A-Z, digits, editing keys, punctuation, function keys,
    navigation, then the modifiers -- rather than an alphabetical string sort
    that puts ``'`` first and ``F1`` before ``F2``.
    """
    table = _layout_table()
    offered = [name for name in table if _key_is_offered(name)]
    if not offered:
        table = {}
        offered = list(_FALLBACK_KEYS)

    def _order(name):
        return table.get(name, 0xFFFF)

    groups = []
    claimed = set()
    for group_id, members in KEY_GROUPS:
        present = sorted((n for n in offered if n in set(members)), key=_order)
        if present:
            groups.append((group_id, present))
            claimed.update(present)
    rest = sorted((n for n in offered if n not in claimed), key=_order)
    if rest:
        groups.append(("Other", rest))
    return groups


def keyboard_keys() -> list[str]:
    """Return the keyboard keys offered by the qwerty layout, in keyboard order."""
    return [name for _group_id, names in keyboard_groups() for name in names]


#: Modifier keys a combination can be built from, in the order they are
#: emitted (modifiers first, then the key). Left variants only.
COMBO_MODIFIERS = [
    ("LeftCtrl", "Ctrl"),
    ("LeftShift", "Shift"),
    ("LeftAlt", "Alt"),
    ("LeftSuper", "Super"),
]

#: One button field holds at most four HID usage codes. Bounded by the packet
#: itself -- ``<type 1B> <param 4B>`` -- and confirmed by rivalcfg's capture
#: ``51 E0 E5 06 00`` (LeftCtrl + RightShift + C).
COMBO_MAX_KEYS = 4

#: Short forms for modifier names, for chips and the popover.
_MODIFIER_LABELS = {
    "leftctrl": "Ctrl", "rightctrl": "Ctrl",
    "leftshift": "Shift", "rightshift": "Shift",
    "leftalt": "Alt", "rightalt": "Alt",
    "leftsuper": "Super", "rightsuper": "Super",
}


def is_combo(value) -> bool:
    """True for a combination value such as ``"LeftCtrl+LeftShift+C"``."""
    return isinstance(value, str) and "+" in value


def parse_combo(value: str) -> list[str]:
    """Split a combination value into its canonical key names."""
    return [part for part in (p.strip() for p in str(value).split("+")) if part]


def format_combo(keys: Iterable[str]) -> str:
    """Canonical value string for *keys* (modifiers first, then the key)."""
    return "+".join(keys)


def combo_label(value: str) -> str:
    """Human readable combination, e.g. ``"Ctrl + Shift + C"``."""
    return " + ".join(_MODIFIER_LABELS.get(k.lower(), k) for k in parse_combo(value))


def keyboard_layout() -> dict:
    """rivalcfg's qwerty name -> HID usage table, lowercased, or ``{}``."""
    try:
        from rivalcfg.handlers.buttons import buttons as handler  # type: ignore

        return handler.build_layout(handler.layout_qwerty)
    except Exception:
        return {}


def combo_codes(keys: Iterable[str]) -> list[int]:
    """HID usage codes for a combination, from rivalcfg's own key table.

    Raises ``ValueError`` for a name the layout does not know, so an
    unencodable combination is a loud failure rather than a wrong packet.
    """
    layout = keyboard_layout()
    if not layout:
        raise ValueError("rivalcfg keyboard layout is not available")
    codes = []
    for key in keys:
        code = layout.get(str(key).lower())
        if code is None:
            raise ValueError("Unknown key '%s'" % key)
        codes.append(int(code))
    return codes


#: rivalcfg key name -> X11 keysym name, for the keys whose name is not already
#: the keysym name (letters, digits and F1..F24 are; these are not). Used to
#: turn a physical key press into a key name.
KEY_SYMBOL_NAMES = {
    "Enter": "Return", "Escape": "Escape", "BackSpace": "BackSpace",
    "Tab": "Tab", "Space": "space", "CapsLock": "Caps_Lock",
    "PrintScreen": "Print", "ScrollLock": "Scroll_Lock", "PauseBreak": "Pause",
    "Insert": "Insert", "Home": "Home", "PageUp": "Page_Up", "Delete": "Delete",
    "End": "End", "PageDown": "Page_Down", "Right": "Right", "Left": "Left",
    "Down": "Down", "Up": "Up", "NumLock": "Num_Lock", "ContextMenu": "Menu",
    "-": "minus", "=": "equal", "[": "bracketleft", "]": "bracketright",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", "`": "grave",
    ",": "comma", ".": "period", "/": "slash", "#": "numbersign",
    "LeftCtrl": "Control_L", "RightCtrl": "Control_R",
    "LeftShift": "Shift_L", "RightShift": "Shift_R",
    "LeftAlt": "Alt_L", "RightAlt": "Alt_R",
    "LeftSuper": "Super_L", "RightSuper": "Super_R",
    "Keypad/": "KP_Divide", "Keypad*": "KP_Multiply", "Keypad-": "KP_Subtract",
    "Keypad+": "KP_Add", "KeypadEnter": "KP_Enter", "Keypad.": "KP_Decimal",
    "Keypad,": "KP_Separator", "Keypad=": "KP_Equal",
}

#: Keysym names produced by a shifted key on a qwerty layout -> the key it sits
#: on. GTK reports the shifted symbol (Shift+1 arrives as ``"exclam"``) and the
#: Shift itself comes from the event's modifier state.
SHIFTED_SYMBOLS = {
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
    "asciicircum": "6", "ampersand": "7", "asterisk": "8", "parenleft": "9",
    "parenright": "0", "underscore": "-", "plus": "=", "braceleft": "[",
    "braceright": "]", "bar": "\\", "colon": ";", "quotedbl": "'",
    "less": ",", "greater": ".", "question": "/", "asciitilde": "`",
}

#: Keysyms that only report a modifier being held. A capture waits for the real
#: key instead of binding the modifier on its own.
MODIFIER_SYMBOLS = frozenset([
    "Control_L", "Control_R", "Shift_L", "Shift_R",
    "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Super_L", "Super_R",
    "Hyper_L", "Hyper_R", "ISO_Level3_Shift",
])

#: X11 spellings ``Gdk.keyval_name()`` never emits but other toolkits use,
#: mapped to the keysym name it does emit.
_KEY_SYMBOL_ALIASES = {"Prior": "Page_Up", "Next": "Page_Down"}


def key_symbol_name(name: str) -> str:
    """X11 keysym name for a rivalcfg key name (``"A"`` -> ``"a"``)."""
    if name in KEY_SYMBOL_NAMES:
        return KEY_SYMBOL_NAMES[name]
    if len(name) == 1 and name.isascii() and name.isalpha():
        return name.lower()
    if name.startswith("Keypad") and len(name) == 7:
        return "KP_" + name[6]
    return name


def is_modifier_symbol(symbol: str) -> bool:
    """True when *symbol* is a bare modifier name from ``Gdk.keyval_name()``."""
    return symbol in MODIFIER_SYMBOLS


def key_for_symbol(symbol: str) -> "str | None":
    """rivalcfg key name for a keysym name from ``Gdk.keyval_name()``, or ``None``.

    Letters match whichever case the shift state produced (GTK reports ``"S"``
    while Shift is held), and a shifted symbol folds back onto the key it sits
    on. Unknown keysyms -- media keys, dead keys, keys from another group --
    return ``None`` so the caller can ignore the press.
    """
    if not symbol:
        return None
    symbol = _KEY_SYMBOL_ALIASES.get(symbol, symbol)
    for name in keyboard_keys():
        keysym = key_symbol_name(name)
        if keysym == symbol:
            return name
        if len(keysym) == 1 and keysym.lower() == symbol.lower():
            return name
    return SHIFTED_SYMBOLS.get(symbol)


def action_label(value: str) -> str:
    """Human readable label for a button assignment value."""
    if is_combo(value):
        return combo_label(value)
    if value in ACTION_LABELS:
        return ACTION_LABELS[value]
    for canonical, label in MULTIMEDIA_ACTIONS:
        if value.lower() == canonical.lower():
            return label
    return value


# ---------------------------------------------------------------------------
# Capability model
# ---------------------------------------------------------------------------

@dataclass
class ZoneCap:
    """One LED zone (z1..z9, logo, wheel)."""

    key: str          # profile setting name, e.g. "z1_color"
    label: str        # profile label, e.g. "Strip top LED color"
    cli: str          # primary long CLI flag, e.g. "--top-color"
    default: str      # default colour value from the profile

    @property
    def short_label(self) -> str:
        return self.label


@dataclass
class ButtonCap:
    """One physical button / scroll direction."""

    key: str          # canonical lower name, e.g. "button1", "scrollup"
    label: str        # profile label, e.g. "Button1"
    id: int
    offset: int
    default: str      # default assignment, e.g. "button1", "dpi", "disabled"


@dataclass
class LightingCaps:
    zones: list[ZoneCap] = field(default_factory=list)
    has_reactive: bool = False
    reactive_default: str = "off"
    has_rainbow: bool = False
    rainbow_kind: str = "flag"          # "flag" | "choice"
    rainbow_choices: list[str] = field(default_factory=list)
    rainbow_default: str = ""
    has_default_lighting: bool = False
    default_lighting_choices: list[str] = field(default_factory=list)
    default_lighting_default: str = ""
    has_light_effect: bool = False
    light_effect_choices: list[str] = field(default_factory=list)
    light_effect_default: str = ""
    has_led_brightness: bool = False
    led_brightness_range: tuple = (0, 100, 1)

    @property
    def has_any(self) -> bool:
        return bool(
            self.zones
            or self.has_reactive
            or self.has_rainbow
            or self.has_default_lighting
            or self.has_light_effect
            or self.has_led_brightness
        )


@dataclass
class DeviceCaps:
    """Everything the UI is allowed to gate on. No flag sniffing in the UI."""

    name: str = ""
    vendor_id: int = 0
    product_id: int = 0
    endpoint: int = 0
    source: str = "none"  # "library" | "cli" | "none"

    dpi_min: int = 200
    dpi_max: int = 8500
    dpi_step: int = 100
    dpi_max_presets: int = 5
    dpi_first_preset: int = 1
    dpi_default: list[int] = field(default_factory=lambda: [800, 1600])

    polling_choices: list[int] = field(default_factory=lambda: [125, 250, 500, 1000])
    polling_default: int = 1000

    lighting: LightingCaps = field(default_factory=LightingCaps)
    buttons: list[ButtonCap] = field(default_factory=list)
    button_keyboard: bool = False
    button_multimedia: bool = False

    has_battery: bool = False
    has_firmware: bool = False
    sleep_timer: Optional[tuple] = None      # (min, max, step)
    dim_timer: Optional[tuple] = None        # (min, max, step)

    raw_settings: dict = field(default_factory=dict)

    # -- convenience --------------------------------------------------------
    @property
    def dpi_range(self) -> tuple:
        return (self.dpi_min, self.dpi_max)

    @property
    def has_extra_buttons(self) -> bool:
        return any(b.key in ("button7", "button8", "button9") for b in self.buttons)

    @property
    def button_keys(self) -> list[str]:
        return [b.key for b in self.buttons]

    @property
    def has_lighting(self) -> bool:
        return self.lighting.has_any

def _first_long_cli(cli: Iterable[str]) -> str:
    """Pick the primary long option from a profile ``cli`` list."""
    longs = [c for c in (cli or []) if c.startswith("--")]
    return longs[0] if longs else (list(cli)[0] if cli else "")


def _parse_int_list(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                pass
        return out
    out = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def _default_of(setting: dict):
    if "default" in setting:
        return setting["default"]
    return None


def derive_caps(profile: dict) -> DeviceCaps:
    """Build a :class:`DeviceCaps` from a rivalcfg device profile dict."""
    caps = DeviceCaps(
        name=profile.get("name", ""),
        vendor_id=int(profile.get("vendor_id", 0)),
        product_id=int(profile.get("product_id", 0)),
        endpoint=int(profile.get("endpoint", 0)),
        source="library",
    )
    caps.has_battery = "battery_level" in profile and bool(profile["battery_level"])
    caps.has_firmware = "firmware_version" in profile and bool(profile["firmware_version"])
    caps.raw_settings = profile.get("settings", {})

    lighting = caps.lighting

    # Deterministic zone ordering: z1..z9, then logo, then wheel.
    zone_order: list[str] = []
    for name in profile.get("settings", {}):
        if re.fullmatch(r"z\d+_color", name):
            zone_order.append(name)
    zone_order.sort(key=lambda n: int(re.match(r"z(\d+)_color", n).group(1)))
    for extra in ("logo_color", "wheel_color"):
        if extra in profile.get("settings", {}):
            zone_order.append(extra)

    for name in zone_order:
        setting = profile["settings"][name]
        if setting.get("value_type") not in ("rgbcolor", "rgbgradient", "rgbgradientv2"):
            continue
        lighting.zones.append(
            ZoneCap(
                key=name,
                label=setting.get("label", name),
                cli=_first_long_cli(setting.get("cli", [])),
                default=str(setting.get("default", "#ff0000")),
            )
        )

    # -- sensitivity / DPI --------------------------------------------------
    for name in ("sensitivity", "sensitivity1"):
        setting = profile.get("settings", {}).get(name)
        if not setting:
            continue
        vtype = setting.get("value_type", "")
        if not vtype.startswith("multidpi"):
            continue
        ir = setting.get("input_range") or [200, 8500, 100]
        caps.dpi_min, caps.dpi_max, caps.dpi_step = int(ir[0]), int(ir[1]), int(ir[2] or 100)
        caps.dpi_max_presets = int(setting.get("max_preset_count", 5) or 5)
        caps.dpi_first_preset = int(setting.get("first_preset", 1) or 0)
        parsed = _parse_int_list(setting.get("default"))
        caps.dpi_default = parsed or [caps.dpi_min, min(caps.dpi_max, caps.dpi_min * 2)]
        break

    # -- polling rate -------------------------------------------------------
    for name, setting in profile.get("settings", {}).items():
        if setting.get("cli") and "--polling-rate" in setting["cli"]:
            choices = setting.get("choices", {})
            nums = []
            for key in choices:
                try:
                    nums.append(int(key))
                except (TypeError, ValueError):
                    continue
            if nums:
                caps.polling_choices = sorted(set(nums))
            try:
                caps.polling_default = int(setting.get("default", 1000))
            except (TypeError, ValueError):
                caps.polling_default = caps.polling_choices[-1]
            break

    # -- lighting extras ----------------------------------------------------
    settings = profile.get("settings", {})
    reactive = settings.get("reactive_color")
    if reactive and reactive.get("value_type") == "reactive_rgbcolor":
        lighting.has_reactive = True
        lighting.reactive_default = str(reactive.get("default", "off") or "off")

    rainbow = settings.get("rainbow_effect")
    if rainbow:
        lighting.has_rainbow = True
        if rainbow.get("value_type") == "none":
            lighting.rainbow_kind = "flag"
            lighting.rainbow_choices = []
        else:
            lighting.rainbow_kind = "choice"
            lighting.rainbow_choices = [str(k) for k in rainbow.get("choices", {})]
            default = rainbow.get("default")
            if default is not None:
                lighting.rainbow_default = str(default)

    dl = settings.get("default_lighting")
    if dl:
        lighting.has_default_lighting = True
        lighting.default_lighting_choices = [str(k) for k in dl.get("choices", {})]
        if dl.get("default") is not None:
            lighting.default_lighting_default = str(dl["default"])

    effect = settings.get("light_effect")
    if effect:
        lighting.has_light_effect = True
        lighting.light_effect_choices = [str(k) for k in effect.get("choices", {})]
        if effect.get("default") is not None:
            lighting.light_effect_default = str(effect["default"])

    brightness = settings.get("led_brightness")
    if brightness:
        lighting.has_led_brightness = True
        ir = brightness.get("input_range") or [0, 100, 1]
        lighting.led_brightness_range = (int(ir[0]), int(ir[1]), int(ir[2] or 1))

    for key, attr in (("sleep_timer", "sleep_timer"), ("dim_timer", "dim_timer")):
        setting = settings.get(key)
        if not setting:
            continue
        ir = setting.get("input_range") or [0, 0, 1]
        setattr(caps, attr, (int(ir[0]), int(ir[1]), int(ir[2] or 1)))

    # -- buttons ------------------------------------------------------------
    mapping = settings.get("buttons_mapping")
    if mapping and mapping.get("value_type") == "buttons":
        for label_name, data in mapping.get("buttons", {}).items():
            caps.buttons.append(
                ButtonCap(
                    key=label_name.lower(),
                    label=label_name,
                    id=int(data.get("id", 0)),
                    offset=int(data.get("offset", 0)),
                    default=str(data.get("default", "disabled")),
                )
            )
        caps.button_keyboard = mapping.get("button_keyboard") is not None
        caps.button_multimedia = mapping.get("button_multimedia") is not None

    return caps


# ---------------------------------------------------------------------------
# CLI fallback caps (PR #1 --help parser, kept as the safety net)
# ---------------------------------------------------------------------------

def caps_from_help_text(help_text: str, name_hint: str = "", source: str = "cli") -> DeviceCaps:
    """Derive capabilities from ``rivalcfg --help`` output.

    Used when ``rivalcfg.devices`` cannot be imported or its internal API
    changed. Mirrors the Phase 0 parser but returns the same model as
    :func:`derive_caps`.
    """
    caps = DeviceCaps(source=source)
    help_text = help_text or ""

    m = re.search(r"from\s+(\d+)\s*dpi\s+to\s+(\d+)\s*dpi", help_text)
    if m:
        caps.dpi_min, caps.dpi_max = int(m.group(1)), int(m.group(2))
        caps.dpi_default = [caps.dpi_min, min(caps.dpi_max, caps.dpi_min * 2)]

    aerox = "--top-color" in help_text and "--strip-top-color" not in help_text
    lighting = caps.lighting
    if aerox:
        lighting.zones = [
            ZoneCap("z1_color", "Strip top LED color", "--top-color", "red"),
            ZoneCap("z2_color", "Strip middle LED color", "--middle-color", "lime"),
            ZoneCap("z3_color", "Strip bottom LED color", "--bottom-color", "blue"),
        ]
        if "--logo-color" in help_text:
            lighting.zones.append(ZoneCap("z4_color", "Logo LED color", "--logo-color", "purple"))
        lighting.has_reactive = "--reactive-color" in help_text
        lighting.has_default_lighting = "--default-lighting" in help_text
        if lighting.has_default_lighting:
            lighting.default_lighting_choices = ["off", "reactive", "rainbow", "reactive-rainbow"]
            lighting.default_lighting_default = "rainbow"
        lighting.has_rainbow = "--rainbow-effect" in help_text
        lighting.rainbow_kind = "flag"
        lighting.has_light_effect = "--light-effect" in help_text
    else:
        lighting.zones = [
            ZoneCap("z1_color", "Strip top LED color", "--strip-top-color", "red"),
            ZoneCap("z2_color", "Strip middle LED color", "--strip-middle-color", "lime"),
            ZoneCap("z3_color", "Strip bottom LED color", "--strip-bottom-color", "blue"),
        ]
        if "--logo-color" in help_text:
            lighting.zones.append(ZoneCap("z4_color", "Logo LED color", "--logo-color", "purple"))
        lighting.has_light_effect = "--light-effect" in help_text
        lighting.has_rainbow = "--rainbow-effect" in help_text
        lighting.rainbow_kind = "choice" if "--rainbow-effect" in help_text else "flag"

    if lighting.has_light_effect:
        lighting.light_effect_choices = [
            "steady", "breath", "breath-slow", "breath-fast",
            "rainbow-shift", "rainbow-breath", "disco",
        ]
        lighting.light_effect_default = "steady"

    extra = "button7=" in help_text or "button9=" in help_text
    n_buttons = 9 if extra else 6
    for i in range(1, n_buttons + 1):
        default = "dpi" if i == 6 else f"button{i}"
        caps.buttons.append(ButtonCap(f"button{i}", f"Button{i}", 0, (i - 1) * 5, default))
    caps.buttons.append(ButtonCap("scrollup", "ScrollUp", 0x31, 0x2D, "scrollup"))
    caps.buttons.append(ButtonCap("scrolldown", "ScrollDown", 0x32, 0x32, "scrolldown"))
    caps.button_keyboard = True
    caps.button_multimedia = True

    m2 = re.search(r"(SteelSeries[^\n]*?)\s+Options:", help_text)
    if m2:
        caps.name = m2.group(1).strip()
    elif name_hint:
        caps.name = name_hint

    return caps


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------

@dataclass
class Device:
    name: str
    vendor_id: int
    product_id: int
    endpoint: int = 0

    @property
    def vid_pid(self) -> str:
        return "%04x:%04x" % (self.vendor_id, self.product_id)


class DeviceManager:
    """Enumerate plugged devices and expose their capabilities.

    Library-first: uses ``rivalcfg.devices``. If that import fails or raises,
    falls back to the ``rivalcfg --help`` parser (``source == "cli"``).
    """

    def __init__(self, devices_mod=None, help_provider: Optional[Callable[[], str]] = None):
        self._devices_mod = devices_mod
        self._help_provider = help_provider
        self._caps_cache: dict[tuple[int, int], DeviceCaps] = {}
        self._fallback_help: Optional[str] = None
        self._library_error_logged = False

    # -- module access ------------------------------------------------------
    def _devices(self):
        if self._devices_mod is not None:
            return self._devices_mod
        try:
            from rivalcfg import devices as _devices  # type: ignore

            self._devices_mod = _devices
            return _devices
        except Exception as exc:  # pragma: no cover - depends on environment
            if not self._library_error_logged:
                logging.warning("rivalcfg.devices import failed: %s", exc)
                self._library_error_logged = True
            return None

    def _help_text(self) -> str:
        if self._fallback_help is None:
            if self._help_provider is not None:
                self._fallback_help = self._help_provider() or ""
            else:
                self._fallback_help = ""
        return self._fallback_help

    # -- enumeration --------------------------------------------------------
    def list_devices(self) -> list[Device]:
        """Return the plugged, supported devices."""
        devices = self._devices()
        if devices is not None:
            try:
                result = []
                for item in devices.list_plugged_devices():
                    vid = int(item["vendor_id"])
                    pid = int(item["product_id"])
                    endpoint = 0
                    try:
                        profile = devices.get_profile(vid, pid)
                        endpoint = int(profile.get("endpoint", 0))
                    except Exception:
                        pass
                    result.append(Device(item.get("name", ""), vid, pid, endpoint))
                return result
            except Exception as exc:  # pragma: no cover - depends on environment
                logging.warning("list_plugged_devices failed: %s", exc)
        return []

    def device_signature(self) -> tuple:
        """A hashable signature of the currently plugged devices."""
        return tuple(sorted((d.vendor_id, d.product_id) for d in self.list_devices()))

    def primary_device(self) -> Optional[Device]:
        devices = self.list_devices()
        return devices[0] if devices else None

    # -- capabilities -------------------------------------------------------
    def get_caps(self, device: Optional[Device] = None, refresh: bool = False) -> DeviceCaps:
        """Return capabilities for *device* (or the primary device)."""
        if device is None:
            device = self.primary_device()
        if device is None:
            return self.fallback_caps()
        key = (device.vendor_id, device.product_id)
        if not refresh and key in self._caps_cache:
            return self._caps_cache[key]
        caps = self._derive_library_caps(device)
        if caps is None:
            caps = self.fallback_caps(name_hint=device.name)
        self._caps_cache[key] = caps
        return caps

    def _derive_library_caps(self, device: Device) -> Optional[DeviceCaps]:
        devices = self._devices()
        if devices is None:
            return None
        try:
            profile = devices.get_profile(device.vendor_id, device.product_id)
            return derive_caps(profile)
        except Exception as exc:
            logging.warning("get_profile(%04x:%04x) failed: %s", device.vendor_id, device.product_id, exc)
            return None

    def fallback_caps(self, name_hint: str = "") -> DeviceCaps:
        """Caps derived from ``rivalcfg --help`` (degraded mode)."""
        return caps_from_help_text(self._help_text(), name_hint=name_hint, source="cli")

    def invalidate(self):
        self._caps_cache.clear()
        self._fallback_help = None


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

#: One plan step: a rivalcfg CLI argv, or a callable returning ``(ok, output)``.
#:
#: The library form exists because some settings cannot be expressed on the
#: command line at all -- most notably the active DPI preset, which the CLI
#: always resets to the first one (see :func:`sensitivity_library_step`).
Step = Union[list[str], Callable[[], "tuple[bool, str]"]]


@dataclass
class ApplyPlan:
    """One ordered, atomic unit of work."""

    label: str
    steps: list[Step] = field(default_factory=list)
    on_done: Optional[Callable[[bool, str], None]] = None
    #: When True, ``--no-save`` is added by the runner if the user enabled it.
    saves: bool = True

    def add(self, step: Step) -> "ApplyPlan":
        """Append one step: a CLI argv, or a callable doing a library write."""
        if step:
            self.steps.append(list(step) if isinstance(step, (list, tuple)) else step)
        return self

    def __bool__(self) -> bool:
        return bool(self.steps)

    def __len__(self) -> int:
        return len(self.steps)


#: When False, nothing is written through the rivalcfg library: every plan step
#: falls back to the CLI. Set ``RIVALCFG_GUI_FORCE_CLI=1`` to force it.
USE_LIBRARY_WRITES = os.environ.get("RIVALCFG_GUI_FORCE_CLI") != "1"


def sensitivity_library_step(
    values: Iterable[int],
    active_index: int = 0,
    save: bool = True,
    mouse_factory: Optional[Callable[[], object]] = None,
) -> Optional[Callable[[], "tuple[bool, str]"]]:
    """Return a plan step that writes the DPI presets and selects the active one.

    The CLI cannot do this. ``multidpi_*`` handlers take a ``selected_preset``
    argument, but the CLI only ever parses the DPI list, so every
    ``--sensitivity`` invocation sends ``first_preset`` -- i.e. it drags the
    mouse back to preset 1 (rivalcfg docs: "When you set the sensitivity through
    the CLI, the selected preset always back to the first one"). Only the
    library's generated ``set_sensitivity(values, selected_preset)`` method can
    select a preset, which is why this step exists.

    Returns ``None`` when library writes are disabled or the library is
    unavailable, so callers can fall back to the CLI argv.

    :param values: the DPI presets, in order.
    :param active_index: 0-based index of the preset to make active.
    :param save: persist to the mouse's internal memory.
    :param mouse_factory: injection seam for tests; defaults to
                          ``rivalcfg.get_first_mouse()``, matching the CLI
                          runner, which never passes ``--device``.
    """
    if not USE_LIBRARY_WRITES:
        return None

    dpis = [int(v) for v in values]
    if not dpis:
        return None

    def _get_mouse():
        if mouse_factory is not None:
            return mouse_factory()
        from rivalcfg import get_first_mouse  # type: ignore

        return get_first_mouse()

    def set_sensitivity() -> "tuple[bool, str]":
        """Write the presets and select preset *active_index* (never raises)."""
        logging.info(
            "set_sensitivity (library): values=%s selected_preset=%s save=%s",
            dpis, active_index, save,
        )
        mouse = None
        try:
            mouse = _get_mouse()
            mouse.set_sensitivity(dpis, active_index)
            if save:
                mouse.save()
            return True, ""
        except ImportError:
            return False, "rivalcfg library is not importable"
        except Exception as exc:
            return False, "%s: %s" % (_library_error_hint(exc), exc)
        finally:
            if mouse is not None:
                try:
                    mouse.close()
                except Exception:  # pragma: no cover - defensive
                    logging.debug("mouse.close() failed", exc_info=True)

    return set_sensitivity


def _library_error_hint(exc: Exception) -> str:
    """A readable prefix for library write failures (the mouse may be off)."""
    if isinstance(exc, (IOError, OSError)):
        return "Cannot reach the mouse (is it turned on?)"
    return "Library write failed"


def add_sensitivity(
    plan: ApplyPlan,
    values: Iterable[int],
    active_index: int = 0,
    save: bool = True,
) -> ApplyPlan:
    """Append the sensitivity write to *plan* -- library step, else CLI argv.

    Single source of truth for every DPI write path, so the active preset is
    carried consistently (or, on the CLI fallback, knowingly dropped).
    """
    step = sensitivity_library_step(values, active_index, save=save)
    if step is not None:
        plan.add(step)
    else:
        plan.add(["--sensitivity", ",".join(str(int(v)) for v in values)])
    return plan


def mapping_has_combo(caps: DeviceCaps, mapping: dict) -> bool:
    """True when any button in *mapping* is assigned a key combination."""
    mapping = mapping or {}
    return any(is_combo(mapping.get(cap.key, cap.default)) for cap in caps.buttons)


def buttons_library_step(
    caps: DeviceCaps,
    mapping: dict,
    save: bool = True,
    mouse_factory: Optional[Callable[[], object]] = None,
) -> Optional[Callable[[], "tuple[bool, str]"]]:
    """Return a plan step that writes the button mapping, combinations included.

    The CLI cannot express a combination: rivalcfg's buttons handler writes
    exactly one key byte per button (``packet[offset] = button_keyboard;
    packet[offset + 1] = keyboard_layout[value]``). The field itself is
    ``<BindingType 1B> <Param 4B>`` and Param is a list of HID usage codes, so
    the hardware holds up to four keys at once. flozz's captures on a Rival 650
    (issue #171) -- ``LCtrl + C -> 51 E0 06 00 00`` and
    ``LCtrl + RShift + C -> 51 E0 E5 06 00`` -- and the same encoding measured
    on an Aerox 5 Wireless.

    Returns ``None`` when library writes are disabled or the mapping holds no
    combination, so callers can fall back to the CLI argv.

    :param caps: device capabilities (button keys, labels and offsets).
    :param mapping: canonical ``button_key -> value`` mapping.
    :param save: persist to the mouse's internal memory.
    :param mouse_factory: injection seam for tests; defaults to
                          ``rivalcfg.get_first_mouse()``, matching the CLI
                          runner, which never passes ``--device``.
    """
    if not USE_LIBRARY_WRITES:
        return None

    mapping = dict(mapping or {})
    combos = {
        cap.key: parse_combo(mapping.get(cap.key, cap.default))
        for cap in caps.buttons
        if is_combo(mapping.get(cap.key, cap.default))
    }
    if not combos:
        return None

    def _get_mouse():
        if mouse_factory is not None:
            return mouse_factory()
        from rivalcfg import get_first_mouse  # type: ignore

        return get_first_mouse()

    def set_buttons() -> "tuple[bool, str]":
        """Write the mapping, hand-encoding each combination (never raises)."""
        logging.info(
            "set_buttons (library): combos=%s save=%s",
            {k: format_combo(v) for k, v in combos.items()},
            save,
        )
        mouse = None
        try:
            from rivalcfg.handlers import buttons as handler  # type: ignore
            from rivalcfg.helpers import merge_bytes  # type: ignore

            mouse = _get_mouse()
            profile = getattr(mouse, "mouse_profile", None) or {}
            si = (profile.get("settings") or {}).get("buttons_mapping")
            if not si:
                return False, "This device profile has no button mapping"
            button_keyboard = si.get("button_keyboard")
            if button_keyboard is None:
                return False, "This device profile cannot send keyboard keys"
            field_length = int(si.get("button_field_length") or 5)

            # process_value cannot encode a combination, so feed it each
            # combination's last key -- a valid single key -- and overwrite
            # that button's whole field with the real codes afterwards.
            sent = {}
            for cap in caps.buttons:
                value = mapping.get(cap.key, cap.default)
                if is_combo(value):
                    sent[cap.key] = parse_combo(value)[-1]
                elif value is not None and value != "":
                    sent[cap.key] = value
            sent["layout"] = "qwerty"
            packet = handler.process_value(si, {"buttons": sent})

            for cap in caps.buttons:
                keys = combos.get(cap.key)
                if not keys:
                    continue
                codes = combo_codes(keys)
                if len(codes) > COMBO_MAX_KEYS:
                    return False, "'%s' has %d keys; the mouse holds at most %d" % (
                        combo_label(format_combo(keys)), len(codes), COMBO_MAX_KEYS,
                    )
                field = [int(button_keyboard)] + codes
                field += [0x00] * (field_length - len(field))
                offset = int((si.get("buttons", {}).get(cap.label) or {}).get("offset", cap.offset))
                packet[offset:offset + field_length] = field[:field_length]

            writer = getattr(mouse, "_hid_write", None)
            if writer is None:
                return False, "This rivalcfg version cannot write raw packets"
            writer(
                report_type=si["report_type"],
                data=merge_bytes(si["command"], packet),
            )
            if save:
                mouse.save()
            return True, ""
        except ImportError:
            return False, "rivalcfg library is not importable"
        except AttributeError as exc:
            # Private API drift: ``Mouse._hid_write`` moved or changed shape.
            return False, "This rivalcfg version cannot write raw packets (%s)" % exc
        except Exception as exc:
            return False, "%s: %s" % (_library_error_hint(exc), exc)
        finally:
            if mouse is not None:
                try:
                    mouse.close()
                except Exception:  # pragma: no cover - defensive
                    logging.debug("mouse.close() failed", exc_info=True)

    return set_buttons


def _unavailable_buttons_step() -> Callable[[], "tuple[bool, str]"]:
    """A step that fails loudly, for a combination with the library switched off."""

    def set_buttons_unavailable() -> "tuple[bool, str]":
        return False, (
            "Key combinations need the rivalcfg library, but library writes are "
            "disabled (RIVALCFG_GUI_FORCE_CLI=1)"
        )

    return set_buttons_unavailable


def add_buttons(
    plan: ApplyPlan,
    caps: DeviceCaps,
    mapping: dict,
    save: bool = True,
    merge_into: Optional[list] = None,
) -> ApplyPlan:
    """Append the buttons write to *plan* -- library step if needed, else CLI argv.

    Single source of truth for every buttons write path. A mapping without a
    combination goes through the CLI exactly as before. One with a combination
    needs the library, so rather than hand the CLI a value it would reject,
    forced-CLI mode adds a step that says so.

    :param merge_into: an argv list to append the ``--buttons`` flag to instead
                       of adding a step of its own. ``build_full_plan`` passes
                       its shared batch so a combination-free apply keeps the
                       exact invocation it always had. Ignored for a
                       combination, which cannot be merged (it is not argv).
    """
    if not caps.buttons:
        return plan
    if mapping_has_combo(caps, mapping):
        step = buttons_library_step(caps, mapping, save=save)
        plan.add(step if step is not None else _unavailable_buttons_step())
    elif merge_into is not None:
        merge_into.extend(["--buttons", build_buttons_arg(caps, mapping)])
    else:
        plan.add(["--buttons", build_buttons_arg(caps, mapping)])
    return plan


def build_buttons_arg(caps: DeviceCaps, mapping: dict) -> str:
    """Build the rivalcfg ``--buttons`` argument from a canonical mapping.

    Single source of truth for every apply path (fixes the duplicate inline
    builder and the KeyError on trimmed profiles -- P1).

    Raises ``ValueError`` on a key combination: the CLI validator would only
    reject it later with ``Unknown button, key or action``. ``add_buttons``
    guarantees this is never called with one.
    """
    mapping = mapping or {}
    parts = []
    for cap in caps.buttons:
        value = mapping.get(cap.key, cap.default)
        if value is None or value == "":
            value = cap.default
        if is_combo(value):
            raise ValueError(
                "%s is a key combination; the rivalcfg CLI cannot express it"
                % combo_label(value)
            )
        parts.append(f"{cap.key}={value}")
    parts.append("layout=qwerty")
    return "buttons(" + "; ".join(parts) + ")"


#: Lighting modes the UI offers.  ``steady``, ``rainbow`` and ``off`` are
#: things the firmware does by itself; ``colorshift`` and ``breathe`` are
#: animated on the host (see :mod:`lighting_fx`), because the mouse cannot
#: store them -- the same split SteelSeries GG uses.
LIGHTING_MODES = ("steady", "rainbow", "colorshift", "breathe", "off")

#: The all-off colour.  ``rgbcolor`` has no "off" keyword -- only the reactive
#: handler accepts one -- so a zone is extinguished by asking for black.
OFF_COLOR = "000000"


def lighting_mode_state(mode, zones, flash_hex=None, palette=None,
                        wake=None) -> dict:
    """The raw lighting state that *mode* means.

    The page shows one effect; the device takes four interacting flags.  This
    is the one place that translation happens, kept pure so it can be tested
    without a display -- ``PLAN.md`` §1's send order is untouched.

    Returns the same keys ``build_lighting_plan`` consumes: ``zones``,
    ``reactive``, ``default_lighting``, ``rainbow``.

    Three of the choices matter beyond bookkeeping:

    * ``rainbow`` also sets ``default_lighting`` to ``"rainbow"``.  The rainbow
      is a flag the mouse forgets on sleep unless the wake lighting asks for it
      back, and leaving the two independent is what let the old page build a
      mouse that goes dark every time it wakes (§1 matrix, row 2).
    * ``steady`` keeps *wake* -- the value the Advanced control holds -- rather
      than deriving one.  A mode that overwrote it would make that control dead,
      which is the defect this page exists to fix; a wake value the user cannot
      influence is exactly the "dropdown that does nothing" problem.
    * the host-side effects seed ``zones`` from the palette, so the mouse is
      left showing sensible static colours when the app closes and the
      animation stops -- rather than whatever frame it happened to die on.
    """
    zones = dict(zones or {})
    flash = flash_hex if flash_hex and flash_hex != "off" else None
    reactive = flash or "off"

    if mode == "off":
        # Off means off: no wake animation either, or the LED would come back
        # on the next time the mouse woke.
        return {
            "zones": {key: OFF_COLOR for key in zones},
            "reactive": reactive,
            "default_lighting": "off",
            "rainbow": False,
        }

    if mode == "rainbow":
        return {
            "zones": zones,
            "reactive": reactive,
            "default_lighting": "rainbow",
            "rainbow": True,
        }

    if mode in ("colorshift", "breathe"):
        seeds = list(palette or ())
        seeded = {
            key: seeds[i % len(seeds)]
            for i, key in enumerate(zones)
        } if seeds else dict(zones)
        # "off" here means "no wake animation", not "dark": these modes leave
        # the rainbow flag down and the zones lit, so the seeded colour shows.
        return {
            "zones": seeded,
            "reactive": reactive,
            "default_lighting": "off",
            "rainbow": False,
        }

    # Steady, and anything unrecognised: the colours the user picked, and the
    # wake lighting exactly as the Advanced control has it.
    if wake is None:
        wake = "reactive" if flash else "off"
    return {
        "zones": zones,
        "reactive": reactive,
        "default_lighting": str(wake),
        "rainbow": False,
    }


#: Seconds the animator waits between HID writes.  ``Mouse._hid_write`` sleeps
#: once per write and a frame is three writes, so this is the frame rate: the
#: library's default 0.05 s would cap the animation at ~6 fps and the default
#: is chosen to land nearer 20.  The library's own floor is 0.001 s and its
#: docstring warns that too-fast writes "can hang the device", so this is
#: deliberately between the two and is the knob to lower only if the hardware
#: proves it can take it.
ANIM_COMMAND_DELAY = 0.016

#: Seconds between frames.  A frame costs three writes at ``command_delay``,
#: so this only has to be the *ceiling* on the rate -- the writes themselves
#: are the floor.  Slightly above the write cost so the loop is paced by the
#: sleep rather than spinning.
ANIM_FRAME_INTERVAL = 0.05


class LightingAnimator:
    """Streams host-side effect frames to the mouse over one held HID handle.

    GG's ColorShift and Multi Color Breathe are animated on the PC and never
    written to the mouse, so parity means doing the same here.  The obvious
    route -- the debounced CLI queue -- cannot do it: every step is a
    ``rivalcfg`` subprocess, which costs far more than a frame.  Holding one
    library handle open and writing the three zones directly is what makes the
    frame rate possible.

    ``save()`` is deliberately never called.  The mouse persists settings only
    when asked (``save_command``), and an animation that saved would rewrite
    the device's internal memory twenty times a second -- wearing it out to
    store a colour that is about to change anyway, and fighting the CLI apply
    path over what "the saved state" even is.

    :meth:`start` runs the frame loop on a **worker thread**, not on a GLib
    timeout.  A frame blocks for three ``command_delay`` sleeps (~48 ms), and
    doing that from a main-loop callback would stall the whole GUI at 20 times
    a second; the main loop only ever hands frames over as data.  The GUI still
    owns *when* the animation runs -- :meth:`start` and :meth:`stop` are the
    map/unmap hooks -- it just does not tick the frames itself.
    """

    def __init__(self, zone_keys, mouse_factory=None,
                 command_delay=ANIM_COMMAND_DELAY,
                 frame_interval=ANIM_FRAME_INTERVAL):
        self._zone_keys = list(zone_keys)
        self._mouse_factory = mouse_factory
        self._command_delay = command_delay
        self._frame_interval = frame_interval
        self._mouse = None
        self._thread = None
        self._stop = threading.Event()
        self.last_error = ""

    @property
    def active(self) -> bool:
        """True while the device handle is held open."""
        return self._mouse is not None

    def _get_mouse(self):
        if self._mouse_factory is not None:
            return self._mouse_factory()
        from rivalcfg import get_first_mouse  # type: ignore

        return get_first_mouse()

    def open(self) -> "tuple[bool, str]":
        """Take the device handle.  Returns (ok, message); never raises."""
        if self._mouse is not None:
            return True, ""
        if not USE_LIBRARY_WRITES:
            return False, "library writes are disabled"
        try:
            mouse = self._get_mouse()
        except ImportError:
            return False, "rivalcfg library is not importable"
        except Exception as exc:
            return False, "%s: %s" % (_library_error_hint(exc), exc)
        try:
            mouse.command_delay = self._command_delay
        except Exception:  # pragma: no cover - defensive
            logging.debug("could not set command_delay", exc_info=True)
        self._mouse = mouse
        logging.info("animator opened (%s zones, delay=%.3fs)",
                     len(self._zone_keys), self._command_delay)
        return True, ""

    def write(self, hexes) -> "tuple[bool, str]":
        """Write one frame -- the zone colours, in order.  Never saves."""
        if self._mouse is None:
            return False, "animator is not open"
        try:
            for key, value in zip(self._zone_keys, hexes):
                getattr(self._mouse, "set_" + key)(value)
            return True, ""
        except Exception as exc:
            message = "%s: %s" % (_library_error_hint(exc), exc)
            self.last_error = message
            # A failed write usually means the mouse went to sleep or the link
            # dropped.  Drop the handle rather than retrying into a dead one --
            # the GUI stops the animation and reopens if the user asks again.
            self.close()
            return False, message

    def close(self):
        """Release the device handle.  Safe to call when not open."""
        mouse, self._mouse = self._mouse, None
        if mouse is not None:
            try:
                mouse.close()
            except Exception:  # pragma: no cover - defensive
                logging.debug("animator close failed", exc_info=True)

    @property
    def animating(self) -> bool:
        """True while the frame loop is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, provider, on_error=None) -> "tuple[bool, str]":
        """Begin streaming frames from *provider* on a worker thread.

        *provider* is called with the elapsed seconds and returns the frame's
        zone colours (one hex per zone, in order).  Keeping the colour maths
        outside is what lets this class stay ignorant of ``lighting_fx`` -- and
        it means the GUI can drive the 3D preview from the *same* function the
        mouse is being painted with.

        ``on_error(message)`` is called **from the worker thread** if the
        device disappears mid-animation; the caller marshals it back to its own
        main loop.  Returns (ok, message) for the opening, which never raises.
        """
        if self.animating:
            return True, ""
        ok, message = self.open()
        if not ok:
            return False, message
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(provider, on_error),
            name="lighting-animator", daemon=True)
        self._thread.start()
        return True, ""

    def _run(self, provider, on_error):
        start = time.monotonic()
        while not self._stop.is_set():
            try:
                frame = provider(time.monotonic() - start)
            except Exception as exc:  # pragma: no cover - defensive
                logging.warning("animation provider failed: %s", exc)
                break
            ok, message = self.write(frame)
            if not ok:
                # write() has already dropped the handle; the mouse is gone or
                # asleep.  Stop rather than reopening into a dead link.
                if on_error is not None:
                    try:
                        on_error(message)
                    except Exception:  # pragma: no cover - defensive
                        logging.debug("animator error callback failed",
                                      exc_info=True)
                break
            # An Event, not time.sleep, so stop() interrupts the wait instead
            # of the caller blocking for up to a frame after asking to stop.
            self._stop.wait(self._frame_interval)

    def stop(self, final_hexes=None) -> "tuple[bool, str]":
        """Stop the loop, optionally leaving *final_hexes* on the mouse, and
        release the handle.  Safe to call when not running.

        The final frame is what keeps the mouse from freezing mid-fade when the
        animation ends: the caller passes the resting colours the profile
        describes (see :func:`lighting_mode_state`), not whatever frame the
        loop happened to be on.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        result = (True, "")
        if final_hexes is not None and self._mouse is not None:
            result = self.write(final_hexes)
        self.close()
        return result


def build_lighting_plan(caps: DeviceCaps, state: dict) -> ApplyPlan:
    """Canonical lighting plan.

    Order is locked by ``PLAN.md`` §1:

        zone colours -> reactive colour -> default lighting -> rainbow last

    ``state`` keys: ``zones`` (dict key->hex), ``reactive`` (hex or "off"),
    ``default_lighting``, ``rainbow`` (bool), ``rainbow_value`` (for choice
    rainbows), ``light_effect``.

    Each phase is its own rivalcfg invocation because the CLI processes flags
    in the profile's setting order, not the order given on the command line.
    """
    plan = ApplyPlan("Lighting")
    return _append_lighting(plan, caps, state)


def _append_lighting(plan: ApplyPlan, caps: DeviceCaps, state: dict) -> ApplyPlan:
    lighting = caps.lighting
    zones = state.get("zones") or {}

    # Zone colours can share one invocation (their relative order is inert).
    batch: list[str] = []
    for zone in lighting.zones:
        value = zones.get(zone.key)
        if value:
            batch.extend([zone.cli, value])
    if batch:
        plan.add(batch)

    if lighting.has_reactive:
        plan.add(["--reactive-color", state.get("reactive") or "off"])

    if lighting.has_default_lighting and state.get("default_lighting"):
        plan.add(["--default-lighting", str(state["default_lighting"])])

    if lighting.has_light_effect and state.get("light_effect"):
        plan.add(["--light-effect", str(state["light_effect"])])

    # Rainbow goes last: --default-lighting sent after it would kill it.
    if lighting.has_rainbow and state.get("rainbow"):
        if lighting.rainbow_kind == "flag":
            plan.add(["--rainbow-effect"])
        else:
            value = state.get("rainbow_value") or lighting.rainbow_default or "all"
            plan.add(["--rainbow-effect", str(value)])

    return plan


def build_full_plan(caps: DeviceCaps, state: dict) -> ApplyPlan:
    """Everything the "Apply all" path sends for the current profile.

    The DPI write goes first, as its own step, because selecting the active
    preset needs the library (see ``sensitivity_library_step``) while the rest
    of the settings batch into one CLI invocation. The order-sensitive lighting
    phases stay separate (see build_lighting_plan).

    ``state`` keys: ``dpi``, ``dpi_active_index``, ``save``, ``polling``,
    ``buttons``, plus the lighting keys documented on build_lighting_plan.
    """
    plan = ApplyPlan("Apply settings")

    dpi = state.get("dpi") or []
    if dpi:
        add_sensitivity(
            plan,
            dpi,
            state.get("dpi_active_index", 0),
            save=state.get("save", True),
        )

    leading: list[str] = []
    polling = state.get("polling")
    if polling:
        leading.extend(["--polling-rate", str(int(polling))])
    mapping = state.get("buttons")
    if mapping is not None:
        # A key combination cannot ride in the CLI batch -- it needs the library
        # -- so it becomes its own step ahead of the batch; anything else keeps
        # the single shared invocation.
        add_buttons(plan, caps, mapping, save=state.get("save", True),
                    merge_into=leading)

    zones = state.get("zones") or {}
    for zone in caps.lighting.zones:
        value = zones.get(zone.key)
        if value:
            leading.extend([zone.cli, value])
    if leading:
        plan.add(leading)

    # Remaining lighting phases (skips zone colours: already in `leading`).
    rest = dict(state)
    rest["zones"] = {}
    _append_lighting(plan, caps, rest)
    return plan


# ---------------------------------------------------------------------------
# Command queue -- the single writer
# ---------------------------------------------------------------------------

_SENTINEL = object()

StatusCallback = Callable[[str, str], None]
Runner = Callable[[list[str]], "tuple[bool, str]"]


class CommandQueue:
    """Serialize every rivalcfg invocation through one worker thread.

    * :meth:`enqueue` runs a plan atomically (stops on first failure, reports
      the failing command, calls ``on_done`` once).
    * :meth:`enqueue_debounced` coalesces rapid updates sharing a key (e.g. a
      slider drag) into a single plan sent after ``debounce_ms``.
    """

    def __init__(self, runner: Runner, status_cb: Optional[StatusCallback] = None,
                 debounce_ms: int = 300):
        self._runner = runner
        self._status_cb = status_cb
        self._debounce_ms = debounce_ms
        self._queue: "_queue.Queue" = _queue.Queue()
        self._debounce: dict[str, ApplyPlan] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._busy = False
        self._current_label: Optional[str] = None
        self._running = True
        self._worker = threading.Thread(target=self._loop, name="rivalcfg-queue", daemon=True)
        self._worker.start()

    # -- public API ---------------------------------------------------------
    def set_runner(self, runner: Runner):
        self._runner = runner

    def set_status_callback(self, cb: Optional[StatusCallback]):
        self._status_cb = cb

    def enqueue(self, plan: ApplyPlan):
        if plan is None or not plan:
            if plan is not None and plan.on_done:
                plan.on_done(True, "")
            return
        self._queue.put(plan)

    def enqueue_args(self, argv: list[str], label: str = "Apply",
                     on_done: Optional[Callable[[bool, str], None]] = None):
        plan = ApplyPlan(label, [list(argv)], on_done=on_done)
        self.enqueue(plan)

    def enqueue_debounced(self, key: str, plan: ApplyPlan):
        with self._lock:
            self._debounce[key] = plan
            timer = self._timers.pop(key, None)
            if timer is not None:
                timer.cancel()
            delay = max(0, self._debounce_ms) / 1000.0
            if delay == 0:
                self._debounce.pop(key, None)
                self.enqueue(plan)
                return
            timer = threading.Timer(delay, self._fire, args=(key,))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def flush_debounced(self):
        """Fire every pending debounced plan now (tests / shutdown)."""
        with self._lock:
            keys = list(self._debounce)
        for key in keys:
            self._fire(key)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0 and not self._busy:
                return True
            time.sleep(0.005)
        return None  # type: ignore[return-value]

    def stop(self, timeout: float = 5.0):
        self.flush_debounced()
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        self._running = False
        self._queue.put(_SENTINEL)
        self._worker.join(timeout=timeout)

    # -- internals ----------------------------------------------------------
    def _fire(self, key: str):
        with self._lock:
            plan = self._debounce.pop(key, None)
            self._timers.pop(key, None)
        if plan is not None:
            self.enqueue(plan)

    def _status(self, kind: str, message: str):
        if self._status_cb is None:
            return
        try:
            self._status_cb(kind, message)
        except Exception:  # pragma: no cover - defensive
            logging.exception("status callback failed")

    def _loop(self):
        while True:
            plan = self._queue.get()
            try:
                if plan is _SENTINEL:
                    return
                self._execute(plan)
            finally:
                self._queue.task_done()

    def _execute(self, plan: ApplyPlan):
        self._busy = True
        self._current_label = plan.label
        self._status("running", plan.label)
        last_output = ""
        try:
            for step in plan.steps:
                try:
                    # A step is either a CLI argv or a self-contained callable
                    # (library write); both must return ``(ok, output)``.
                    if callable(step):
                        ok, out = step()
                    else:
                        ok, out = self._runner(step)
                except Exception as exc:  # runner must not raise, but be safe
                    ok, out = False, str(exc)
                last_output = out or ""
                if not ok:
                    detail = (
                        " ".join(step)
                        if isinstance(step, (list, tuple))
                        else getattr(step, "__name__", "library call")
                    )
                    msg = "%s: %s" % (detail, last_output or "failed")
                    logging.error("Plan %r failed at step %r: %s", plan.label, step, last_output)
                    self._status("error", msg)
                    if plan.on_done:
                        plan.on_done(False, last_output)
                    return
            self._status("ok", plan.label)
            if plan.on_done:
                plan.on_done(True, last_output)
        finally:
            self._busy = False
            self._current_label = None


PROFILE_SCHEMA = 2

#: v1 flat state keys -> canonical v2 zone keys.
LEGACY_ZONE_KEYS = {
    "z1_hex": "z1_color",
    "z2_hex": "z2_color",
    "z3_hex": "z3_color",
    "z4_hex": "z4_color",
}


def migrate_profile(data) -> Optional[dict]:
    """Validate and migrate a profile dict to the current schema.

    v1 files are flat mixes of device + macro settings; v2 is
    ``{"schema": 2, "device_hint": {...}, ...device settings...}``. Unknown or
    invalid values are dropped, never raised (PLAN.md Phase 5 / P2).
    """
    if not isinstance(data, dict):
        return None
    out = {"schema": PROFILE_SCHEMA}

    def _copy(key, cast=None, default=None):
        if key in data and data[key] is not None:
            try:
                out[key] = cast(data[key]) if cast else data[key]
            except (TypeError, ValueError):
                if default is not None:
                    out[key] = default
        elif default is not None:
            out[key] = default

    _copy("dpi_values", lambda v: [int(x) for x in v], [800, 1600])
    _copy("dpi_active_index", int, 0)
    _copy("polling_hz", int, 1000)
    out["button_mapping"] = data.get("button_mapping") if isinstance(data.get("button_mapping"), dict) else {}

    zones = data.get("zones")
    if not isinstance(zones, dict):
        zones = {}
        for old, new in LEGACY_ZONE_KEYS.items():
            if old in data and data[old]:
                zones[new] = data[old]
    out["zones"] = {k: str(v) for k, v in zones.items() if isinstance(v, str) and v}

    reactive = data.get("reactive", data.get("reactive_hex", "off"))
    out["reactive"] = str(reactive) if reactive not in (None, "") else "off"

    out["rainbow"] = bool(data.get("rainbow", data.get("rainbow_enabled", False)))
    out["rainbow_value"] = data.get("rainbow_value") or ""
    out["default_lighting"] = data.get("default_lighting") or ""
    out["light_effect"] = data.get("light_effect", data.get("selected_effect", "")) or ""
    if data.get("led_brightness") is not None:
        try:
            out["led_brightness"] = int(data["led_brightness"])
        except (TypeError, ValueError):
            pass

    # The lighting mode arrived with the mode-first RGB page.  A profile from
    # before it has none, so derive one from the raw flags it does have --
    # otherwise every old profile would open as Steady and silently drop the
    # rainbow it was saved with.
    mode = data.get("lighting_mode")
    if mode not in LIGHTING_MODES:
        if out["rainbow"]:
            mode = "rainbow"
        elif out["zones"] and all(
                str(v).lower() in ("000000", "off", "disable")
                for v in out["zones"].values()):
            mode = "off"
        else:
            mode = "steady"
    out["lighting_mode"] = mode

    palette = data.get("fx_palette")
    if isinstance(palette, list):
        # Stored as given; lighting_fx.normalize_palette repairs the count and
        # the colour forms, so this only has to keep the strings.
        out["fx_palette"] = [str(v) for v in palette if isinstance(v, str) and v]
    speed = data.get("fx_speed")
    if speed is not None:
        try:
            out["fx_speed"] = float(speed)
        except (TypeError, ValueError):
            pass

    if isinstance(data.get("device_hint"), dict):
        out["device_hint"] = data["device_hint"]
    return out


def plan_label_for_args(args: Iterable[str]) -> str:
    """Best-effort human label for a bare argument vector."""
    args = list(args)
    for i, arg in enumerate(args):
        if arg == "--sensitivity":
            return "DPI"
        if arg == "--polling-rate":
            return "Polling rate"
        if arg == "--buttons":
            return "Buttons"
        if arg in ("--top-color", "--middle-color", "--bottom-color",
                   "--strip-top-color", "--strip-middle-color", "--strip-bottom-color",
                   "--logo-color", "--z1", "--z2", "--z3", "--z4"):
            return "Lighting"
        if arg in ("--reactive-color", "--rainbow-effect", "--default-lighting", "--light-effect"):
            return "Lighting"
        if arg == "--reset":
            return "Factory reset"
        if arg == "--firmware-version":
            return "Firmware version"
        if arg == "--battery-level":
            return "Battery level"
    return "Apply"
