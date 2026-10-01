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
import queue as _queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Button helpers
# ---------------------------------------------------------------------------

#: Canonical names of the mouse buttons/actions a button can be mapped to.
MOUSE_BUTTON_ACTIONS = ["button1", "button2", "button3", "button4", "button5",
                        "button6", "button7", "button8", "button9"]
SPECIAL_ACTIONS = ["dpi", "scrollup", "scrolldown", "disabled"]

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


def keyboard_keys() -> list[str]:
    """Return the keyboard keys offered by the qwerty layout."""
    try:
        from rivalcfg.handlers.buttons import layout_qwerty  # type: ignore

        names = set(layout_qwerty.layout.keys())
        names.update(layout_qwerty.aliases.keys())
    except Exception:
        return list(_FALLBACK_KEYS)
    # Keep it to a sane, human-usable subset.
    result = []
    for name in sorted(names):
        if "(" in name or ")" in name:
            continue
        if name.startswith("Keypad"):
            continue
        if name in ("\\", "#", "`"):
            continue
        result.append(name)
    return result


def action_label(value: str) -> str:
    """Human readable label for a button assignment value."""
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

    def button_action_values(self) -> list[str]:
        """Ordered list of assignment values the device accepts."""
        values: list[str] = []
        # Mouse buttons
        for b in self.buttons:
            if b.key.startswith("button") and b.key not in values:
                # A button can be mapped to any mouse button of the device.
                pass
        for name in MOUSE_BUTTON_ACTIONS:
            if name in self.button_keys and name not in values:
                values.append(name)
        if any(b.key == "button6" for b in self.buttons):
            pass
        if "dpi" not in values and self.raw_settings.get("buttons_mapping", {}).get("button_dpi_switch") is not None:
            values.append("dpi")
        if "scrollup" in self.button_keys:
            values.append("scrollup")
        if "scrolldown" in self.button_keys:
            values.append("scrolldown")
        values.append("disabled")
        return values


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

@dataclass
class ApplyPlan:
    """One ordered, atomic unit of work."""

    label: str
    steps: list[list[str]] = field(default_factory=list)
    on_done: Optional[Callable[[bool, str], None]] = None
    #: When True, ``--no-save`` is added by the runner if the user enabled it.
    saves: bool = True

    def add(self, argv: list[str]) -> "ApplyPlan":
        if argv:
            self.steps.append(list(argv))
        return self

    def __bool__(self) -> bool:
        return bool(self.steps)

    def __len__(self) -> int:
        return len(self.steps)


def build_buttons_arg(caps: DeviceCaps, mapping: dict) -> str:
    """Build the rivalcfg ``--buttons`` argument from a canonical mapping.

    Single source of truth for every apply path (fixes the duplicate inline
    builder and the KeyError on trimmed profiles -- P1).
    """
    mapping = mapping or {}
    parts = []
    for cap in caps.buttons:
        value = mapping.get(cap.key, cap.default)
        if value is None or value == "":
            value = cap.default
        parts.append(f"{cap.key}={value}")
    parts.append("layout=qwerty")
    return "buttons(" + "; ".join(parts) + ")"


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

    DPI/polling/buttons join the zone colours in the first invocation; the
    order-sensitive lighting phases stay separate (see build_lighting_plan).
    """
    plan = ApplyPlan("Apply settings")

    leading: list[str] = []
    dpi = state.get("dpi") or []
    if dpi:
        leading.extend(["--sensitivity", ",".join(str(int(v)) for v in dpi)])
    polling = state.get("polling")
    if polling:
        leading.extend(["--polling-rate", str(int(polling))])
    mapping = state.get("buttons")
    if mapping is not None and caps.buttons:
        leading.extend(["--buttons", build_buttons_arg(caps, mapping)])

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
                    ok, out = self._runner(step)
                except Exception as exc:  # runner must not raise, but be safe
                    ok, out = False, str(exc)
                last_output = out or ""
                if not ok:
                    detail = " ".join(step)
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
