"""Tests for device_core: capabilities, plans and the command queue."""

import copy
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import device_core as dc  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AEROX5_HELP = """
usage: rivalcfg [-h]

SteelSeries Aerox 5 Wireless Options:
  -s SENSITIVITY, --sensitivity SENSITIVITY
                        Set sensitivity presets (DPI) (from 100 dpi to 18000 dpi)
  -p POLLING_RATE, --polling-rate POLLING_RATE
  --top-color TOP_COLOR
  --middle-color MIDDLE_COLOR
  --bottom-color BOTTOM_COLOR
  -a REACTIVE_COLOR, --reactive-color REACTIVE_COLOR
  -e, --rainbow-effect
  -d DEFAULT_LIGHTING, --default-lighting DEFAULT_LIGHTING
  -b BUTTONS_MAPPING, --buttons BUTTONS_MAPPING
      buttons(button1=button1; button2=button2; button3=button3; button4=button4; button5=button5; button6=dpi; button7=disabled; button8=disabled; button9=disabled; scrollup=scrollup; scrolldown=scrolldown; layout=qwerty)
  -t SLEEP_TIMER, --sleep-timer SLEEP_TIMER
  -T DIM_TIMER, --dim-timer DIM_TIMER
"""


def _aerox5_profile():
    """Real profile when rivalcfg is importable, else a faithful fixture."""
    try:
        from rivalcfg import devices

        return devices.get_profile(0x1038, 0x1854)
    except Exception:
        return {
            "name": "SteelSeries Aerox 5 Wireless",
            "vendor_id": 0x1038,
            "product_id": 0x1854,
            "endpoint": 3,
            "settings": {
                "sensitivity": {
                    "label": "Sensitivity presets", "cli": ["-s", "--sensitivity"],
                    "value_type": "multidpi_range_choice",
                    "input_range": [100, 18000, 100],
                    "output_choices": {}, "dpi_length_byte": 1,
                    "first_preset": 0, "max_preset_count": 5,
                    "default": "400, 800, 1200, 2400, 3200",
                },
                "polling_rate": {
                    "cli": ["-p", "--polling-rate"], "value_type": "choice",
                    "choices": {125: 3, 250: 2, 500: 1, 1000: 0}, "default": 1000,
                },
                "z1_color": {"label": "Strip top LED color", "cli": ["--top-color", "--z1"],
                             "value_type": "rgbcolor", "default": "red"},
                "z2_color": {"label": "Strip middle LED color", "cli": ["--middle-color", "--z2"],
                             "value_type": "rgbcolor", "default": "lime"},
                "z3_color": {"label": "Strip bottom LED color", "cli": ["--bottom-color", "--z3"],
                             "value_type": "rgbcolor", "default": "blue"},
                "reactive_color": {"cli": ["-a", "--reactive-color"],
                                   "value_type": "reactive_rgbcolor", "default": "off"},
                "sleep_timer": {"cli": ["-t", "--sleep-timer"], "value_type": "range",
                                "input_range": [0, 20, 1], "default": 5},
                "dim_timer": {"cli": ["-T", "--dim-timer"], "value_type": "range",
                              "input_range": [0, 1200, 1], "default": 30},
                "buttons_mapping": {
                    "cli": ["-b", "--buttons"], "value_type": "buttons",
                    "buttons": {
                        "Button1": {"id": 1, "offset": 0, "default": "button1"},
                        "Button2": {"id": 2, "offset": 5, "default": "button2"},
                        "Button3": {"id": 3, "offset": 10, "default": "button3"},
                        "Button4": {"id": 4, "offset": 15, "default": "button4"},
                        "Button5": {"id": 5, "offset": 20, "default": "button5"},
                        "Button6": {"id": 6, "offset": 25, "default": "dpi"},
                        "Button7": {"id": 0, "offset": 30, "default": "disabled"},
                        "Button8": {"id": 0, "offset": 35, "default": "disabled"},
                        "Button9": {"id": 0, "offset": 40, "default": "disabled"},
                        "ScrollUp": {"id": 0x31, "offset": 45, "default": "scrollup"},
                        "ScrollDown": {"id": 0x32, "offset": 50, "default": "scrolldown"},
                    },
                    "button_field_length": 5, "button_disable": 0,
                    "button_dpi_switch": 0x30, "button_scroll_up": None,
                    "button_scroll_down": None, "button_keyboard": 0x51,
                    "button_multimedia": 0x61,
                },
                "rainbow_effect": {"cli": ["-e", "--rainbow-effect"], "value_type": "none"},
                "default_lighting": {
                    "cli": ["-d", "--default-lighting"], "value_type": "choice",
                    "choices": {"off": [0, 0], "reactive": [0, 1], "rainbow": [1, 0],
                                "reactive-rainbow": [1, 1]},
                    "default": "rainbow",
                },
            },
            "battery_level": {"command": [0x92], "response_length": 2},
            "save_command": {"command": [0x11, 0x00]},
        }


@pytest.fixture
def caps():
    return dc.derive_caps(_aerox5_profile())


# ---------------------------------------------------------------------------
# Capability derivation
# ---------------------------------------------------------------------------

def test_derive_caps_dpi(caps):
    assert (caps.dpi_min, caps.dpi_max, caps.dpi_step) == (100, 18000, 100)
    assert caps.dpi_max_presets == 5
    assert caps.dpi_default == [400, 800, 1200, 2400, 3200]


def test_derive_caps_lighting(caps):
    lighting = caps.lighting
    assert [z.cli for z in lighting.zones] == ["--top-color", "--middle-color", "--bottom-color"]
    assert lighting.has_reactive
    assert lighting.has_rainbow and lighting.rainbow_kind == "flag"
    assert lighting.has_default_lighting
    assert lighting.default_lighting_choices == ["off", "reactive", "rainbow", "reactive-rainbow"]
    assert lighting.default_lighting_default == "rainbow"
    assert not lighting.has_light_effect


def test_derive_caps_buttons_and_power(caps):
    assert caps.button_keys == [
        "button1", "button2", "button3", "button4", "button5", "button6",
        "button7", "button8", "button9", "scrollup", "scrolldown",
    ]
    assert caps.has_extra_buttons
    assert caps.button_keyboard and caps.button_multimedia
    assert caps.sleep_timer == (0, 20, 1)
    assert caps.dim_timer == (0, 1200, 1)
    assert caps.has_battery
    assert not caps.has_firmware


def test_derive_caps_rival3_has_firmware():
    try:
        from rivalcfg import devices

        profile = devices.get_profile(0x1038, 0x1824)
    except Exception:
        pytest.skip("rivalcfg not importable")
    caps = dc.derive_caps(profile)
    assert caps.has_firmware
    assert not caps.has_extra_buttons
    assert caps.lighting.has_light_effect
    assert caps.lighting.light_effect_default == "steady"
    assert not caps.lighting.has_reactive


# ---------------------------------------------------------------------------
# Canonical lighting plan (the §1 matrix ordering)
# ---------------------------------------------------------------------------

def _flat_flags(plan):
    return [step[0] for step in plan.steps]


def _all_flags(plan):
    flags = []
    for step in plan.steps:
        if callable(step):          # a library write carries no CLI flags
            continue
        flags.extend(step[0::2])
    return flags


@pytest.fixture
def cli_only(monkeypatch):
    """Force every plan step onto the CLI (no library writes).

    The library path is a callable step, which has no argv to assert on, so the
    pure-argv ordering tests pin the fallback explicitly.
    """
    monkeypatch.setattr(dc, "USE_LIBRARY_WRITES", False)
    return dc


@pytest.fixture
def lib_on(monkeypatch):
    """Force the library write path on, whatever the environment says."""
    monkeypatch.setattr(dc, "USE_LIBRARY_WRITES", True)
    return dc


class _FakeMouse:
    """Minimal stand-in for ``rivalcfg.mouse.Mouse`` (library write path)."""

    def __init__(self, fail_on=None, profile=None, raw_writes=True):
        self.calls = []
        self.closed = False
        self.fail_on = fail_on
        self.mouse_profile = _aerox5_profile() if profile is None else profile
        if raw_writes:
            self._hid_write = self._record_hid_write

    def _record_hid_write(self, report_type=None, data=None):
        self.calls.append(("_hid_write", report_type, list(data or [])))
        if self.fail_on == "_hid_write":
            raise OSError("[Errno 19] No such device")

    def set_sensitivity(self, values, selected_preset=None):
        self.calls.append(("set_sensitivity", list(values), selected_preset))
        if self.fail_on == "set_sensitivity":
            raise OSError("[Errno 19] No such device")

    def save(self):
        self.calls.append(("save",))
        if self.fail_on == "save":
            raise OSError("save failed")

    def close(self):
        self.closed = True
        self.calls.append(("close",))


def _written_fields(mouse):
    """The raw packet from the last library write, split into button fields.

    Returns ``(command, fields)`` where ``fields[offset]`` is the 5 bytes the
    profile places at *offset* -- i.e. exactly what the mouse receives.

    On the real ``Mouse`` the save command also goes through ``_hid_write``, so
    pick the packet write by length rather than by order.
    """
    writes = [c for c in mouse.calls if c[0] == "_hid_write"]
    assert writes, "no library write reached the mouse"
    si = mouse.mouse_profile["settings"]["buttons_mapping"]
    command = [b & 0xFF for b in si["command"]]
    _, _report_type, data = max(writes, key=lambda c: len(c[2]))
    assert data[:len(command)] == command, "packet is not prefixed by the command"
    length = int(si["button_field_length"])
    body = data[len(command):]
    return command, {off: body[off:off + length] for off in range(0, len(body), length)}


def test_lighting_plan_canonical_order(caps):
    state = {
        "zones": {"z1_color": "ff0000", "z2_color": "00ff00", "z3_color": "0000ff"},
        "reactive": "ff0000",
        "default_lighting": "off",
        "rainbow": True,
    }
    plan = dc.build_lighting_plan(caps, state)
    assert plan.steps == [
        ["--top-color", "ff0000", "--middle-color", "00ff00", "--bottom-color", "0000ff"],
        ["--reactive-color", "ff0000"],
        ["--default-lighting", "off"],
        ["--rainbow-effect"],
    ]
    # Rainbow is ALWAYS last.
    assert plan.steps[-1] == ["--rainbow-effect"]


def test_lighting_plan_rainbow_off_omits_flag(caps):
    state = {"zones": {"z1_color": "010203"}, "reactive": "off",
             "default_lighting": "rainbow", "rainbow": False}
    plan = dc.build_lighting_plan(caps, state)
    assert ["--rainbow-effect"] not in plan.steps
    assert plan.steps[0] == ["--top-color", "010203"]


def test_lighting_plan_choice_rainbow_value():
    caps = dc.DeviceCaps(source="library")
    caps.lighting.has_rainbow = True
    caps.lighting.rainbow_kind = "choice"
    caps.lighting.rainbow_choices = ["all", "top", "bottom"]
    caps.lighting.rainbow_default = "all"
    plan = dc.build_lighting_plan(caps, {"rainbow": True, "rainbow_value": "top"})
    assert plan.steps == [["--rainbow-effect", "top"]]


# ---------------------------------------------------------------------------
# Buttons argument (P1: no KeyError, single source of truth)
# ---------------------------------------------------------------------------

def test_build_buttons_arg_defaults(caps):
    arg = dc.build_buttons_arg(caps, {})
    assert "button7=disabled" in arg
    assert "button9=disabled" in arg
    assert "scrollup=scrollup" in arg
    assert "layout=qwerty" in arg


def test_build_buttons_arg_partial_mapping_does_not_raise(caps):
    arg = dc.build_buttons_arg(caps, {"button1": "disabled"})
    assert "button1=disabled" in arg
    assert "button2=button2" in arg


def test_build_buttons_arg_rejects_a_combination(caps):
    """The CLI validator would only say "Unknown button, key or action"."""
    with pytest.raises(ValueError) as err:
        dc.build_buttons_arg(caps, {"button8": "LeftCtrl+C"})
    assert "combination" in str(err.value)


# ---------------------------------------------------------------------------
# Key combinations (the CLI cannot express them -- see the captures below)
# ---------------------------------------------------------------------------

def test_combo_values_round_trip():
    assert dc.is_combo("LeftCtrl+C") is True
    assert dc.is_combo("C") is False
    assert dc.is_combo(None) is False
    assert dc.parse_combo("LeftCtrl+LeftShift+C") == ["LeftCtrl", "LeftShift", "C"]
    assert dc.format_combo(["LeftCtrl", "C"]) == "LeftCtrl+C"
    assert dc.combo_label("LeftCtrl+LeftShift+C") == "Ctrl + Shift + C"
    # A combination renders through the ordinary label helper too, so chips
    # and the popover need no special case.
    assert dc.action_label("LeftCtrl+C") == "Ctrl + C"
    assert dc.action_label("disabled") == "Disabled"


def test_combo_codes_come_from_rivalcfg_own_layout():
    assert dc.combo_codes(["LeftCtrl", "LeftShift", "C"]) == [0xE0, 0xE1, 0x06]
    with pytest.raises(ValueError):
        dc.combo_codes(["Nope"])


@pytest.mark.parametrize("value,expected", [
    # flozz's packet captures on a Rival 650, issue #171 (2021-12-13).
    ("LeftCtrl+C", [0x51, 0xE0, 0x06, 0x00, 0x00]),
    ("LeftCtrl+RightShift+C", [0x51, 0xE0, 0xE5, 0x06, 0x00]),
])
def test_combo_packet_matches_the_rival_650_captures(caps, lib_on, value, expected):
    """Pin the encoding to real hardware evidence, not to a reading of it.

    ``0x51`` is the profile's ``button_keyboard``; the rest are HID usage codes
    for the keys, left to right, with unused slots zero.
    """
    mouse = _FakeMouse()
    step = dc.buttons_library_step(caps, {"button8": value}, mouse_factory=lambda: mouse)
    assert step is not None
    ok, out = step()
    assert ok is True, out
    _command, fields = _written_fields(mouse)
    assert fields[35] == expected          # Button8's field


def test_combo_step_leaves_every_other_field_to_the_handler(caps, lib_on):
    """Only the combination's own 5 bytes bypass ``process_value``."""
    mouse = _FakeMouse()
    dc.buttons_library_step(
        caps,
        {"button7": "A", "button8": "LeftCtrl+C"},
        mouse_factory=lambda: mouse,
    )()
    _command, fields = _written_fields(mouse)
    assert fields[0] == [0x01, 0x00, 0x00, 0x00, 0x00]     # Button1 default
    assert fields[25] == [0x30, 0x00, 0x00, 0x00, 0x00]    # Button6 default (dpi)
    assert fields[30] == [0x51, 0x04, 0x00, 0x00, 0x00]    # Button7 = A
    assert fields[45] == [0x31, 0x00, 0x00, 0x00, 0x00]    # ScrollUp default


def test_combo_step_writes_and_saves_then_closes(caps, lib_on):
    mouse = _FakeMouse()
    ok, out = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"},
                                      mouse_factory=lambda: mouse)()
    assert ok is True and out == ""
    assert ("save",) in mouse.calls
    assert mouse.closed is True
    _report_type = [c for c in mouse.calls if c[0] == "_hid_write"][0][1]
    assert _report_type == 2               # the profile's OUTPUT report


def test_combo_step_save_false_skips_save(caps, lib_on):
    mouse = _FakeMouse()
    ok, _ = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"}, save=False,
                                    mouse_factory=lambda: mouse)()
    assert ok is True
    assert ("save",) not in mouse.calls
    assert mouse.closed is True


def test_combo_step_returns_none_without_a_combination(caps, lib_on):
    """Nothing to do for the library: the ordinary argv path handles it."""
    assert dc.buttons_library_step(caps, {"button8": "disabled"}) is None
    assert dc.buttons_library_step(caps, {}) is None


def test_combo_step_returns_none_when_library_writes_are_off(caps, cli_only):
    assert dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"}) is None


def test_combo_step_never_raises_on_missing_device(caps, lib_on):
    def factory():
        raise OSError("[Errno 19] No such device")

    ok, out = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"},
                                      mouse_factory=factory)()
    assert ok is False and "turned on" in out


def test_combo_step_never_raises_when_the_write_fails(caps, lib_on):
    mouse = _FakeMouse(fail_on="_hid_write")
    ok, out = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"},
                                      mouse_factory=lambda: mouse)()
    assert ok is False and "turned on" in out
    assert mouse.closed is True


def test_combo_step_reports_a_rivalcfg_without_raw_writes(caps, lib_on):
    mouse = _FakeMouse(raw_writes=False)
    ok, out = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"},
                                      mouse_factory=lambda: mouse)()
    assert ok is False and "raw packets" in out


def test_combo_step_refuses_a_device_without_keyboard_keys(caps, lib_on):
    profile = copy.deepcopy(_aerox5_profile())
    profile["settings"]["buttons_mapping"]["button_keyboard"] = None
    mouse = _FakeMouse(profile=profile)
    ok, out = dc.buttons_library_step(caps, {"button8": "LeftCtrl+C"},
                                      mouse_factory=lambda: mouse)()
    assert ok is False and "keyboard keys" in out


def test_combo_step_refuses_more_keys_than_the_field_holds(caps, lib_on):
    """Four HID codes is the field's ceiling -- ``<type 1B> <param 4B>``."""
    mouse = _FakeMouse()
    too_many = "LeftCtrl+LeftShift+LeftAlt+LeftSuper+C"
    ok, out = dc.buttons_library_step(caps, {"button8": too_many},
                                      mouse_factory=lambda: mouse)()
    assert ok is False and "at most 4" in out
    assert not [c for c in mouse.calls if c[0] == "_hid_write"]


def test_combo_step_reports_an_unknown_key_loudly(caps, lib_on):
    """An unencodable name must be a loud failure, never a wrong packet.

    ``process_value`` rejects the placeholder if the *last* key is unknown; a
    bad *modifier* gets past it, so ``combo_codes`` has to catch that one.
    """
    for value, bad_name in (("LeftCtrl+Nope", "nope"), ("Bogus+LeftCtrl", "bogus")):
        mouse = _FakeMouse()
        ok, out = dc.buttons_library_step(caps, {"button8": value},
                                          mouse_factory=lambda: mouse)()
        assert ok is False and bad_name in out.lower()
        assert not [c for c in mouse.calls if c[0] == "_hid_write"]


# ---------------------------------------------------------------------------
# add_buttons: one routing decision for every write path
# ---------------------------------------------------------------------------

def test_add_buttons_routes_a_combination_to_the_library(caps, lib_on):
    plan = dc.add_buttons(dc.ApplyPlan("Buttons"), caps, {"button8": "LeftCtrl+C"})
    assert len(plan.steps) == 1 and callable(plan.steps[0])
    assert "--buttons" not in _all_flags(plan)


def test_add_buttons_routes_a_plain_mapping_to_the_cli(caps, lib_on):
    plan = dc.add_buttons(dc.ApplyPlan("Buttons"), caps, {"button8": "disabled"})
    assert plan.steps == [["--buttons", dc.build_buttons_arg(caps, {"button8": "disabled"})]]


def test_add_buttons_fails_loudly_when_a_combination_has_no_library(caps, cli_only):
    """Better an error than a CLI write that silently drops the other keys."""
    plan = dc.add_buttons(dc.ApplyPlan("Buttons"), caps, {"button8": "LeftCtrl+C"})
    assert len(plan.steps) == 1 and callable(plan.steps[0])
    ok, out = plan.steps[0]()
    assert ok is False
    assert "RIVALCFG_GUI_FORCE_CLI" in out


def test_add_buttons_is_a_no_op_without_buttons(caps, lib_on):
    bare = dc.DeviceCaps(source="library")
    plan = dc.add_buttons(dc.ApplyPlan("Buttons"), bare, {"button1": "disabled"})
    assert plan.steps == []


# ---------------------------------------------------------------------------
# Full plan
# ---------------------------------------------------------------------------

def test_full_plan_order(caps, cli_only):
    state = {
        "dpi": [800, 1600], "polling": 1000,
        "zones": {"z1_color": "ff0000"}, "reactive": "off",
        "default_lighting": "rainbow", "rainbow": True,
        "buttons": {"button1": "button1"},
    }
    plan = dc.build_full_plan(caps, state)
    flags = _all_flags(plan)
    # First invocation batches DPI, polling, buttons and zone colours.
    assert flags[0] == "--sensitivity"
    assert flags[1] == "--polling-rate"
    assert flags[2] == "--buttons"
    assert "--top-color" in flags
    # Order-sensitive phases remain separate and rainbow is last.
    assert flags[-2] == "--default-lighting"
    assert flags[-1] == "--rainbow-effect"
    # Every phase is its own invocation.
    assert all(len(step) > 0 for step in plan.steps)


def test_full_plan_library_dpi_step_first_without_cli_flag(caps, lib_on):
    """On the library path the DPI write is step 1 and carries no CLI flag."""
    plan = dc.build_full_plan(
        caps,
        {"dpi": [800, 1600], "dpi_active_index": 1, "save": False, "polling": 1000},
    )
    assert callable(plan.steps[0]), "DPI write must be the leading library step"
    assert "--sensitivity" not in _all_flags(plan)
    # The rest still batches as one CLI invocation, polling first.
    flags = _all_flags(plan)
    assert flags[0] == "--polling-rate"


def test_full_plan_no_dpi_leaves_no_library_step(caps):
    plan = dc.build_full_plan(caps, {"polling": 500})
    assert plan.steps and not any(callable(s) for s in plan.steps)


def test_full_plan_combo_is_its_own_step_ahead_of_the_batch(caps, lib_on):
    plan = dc.build_full_plan(
        caps,
        {"polling": 1000, "buttons": {"button8": "LeftCtrl+C"},
         "zones": {"z1_color": "ff0000"}},
    )
    assert callable(plan.steps[0]), "the combination needs the library"
    assert "--buttons" not in _all_flags(plan)
    # Everything else still batches into one invocation after it.
    assert _all_flags(plan)[0] == "--polling-rate"
    assert "--top-color" in _all_flags(plan)


def test_full_plan_plain_buttons_stay_in_the_shared_batch(caps, lib_on):
    """A combination-free apply keeps the exact invocation it always had."""
    plan = dc.build_full_plan(
        caps, {"polling": 1000, "buttons": {"button1": "button1"}},
    )
    assert not any(callable(s) for s in plan.steps)
    assert plan.steps[0] == [
        "--polling-rate", "1000",
        "--buttons", dc.build_buttons_arg(caps, {"button1": "button1"}),
    ]


# ---------------------------------------------------------------------------
# Sensitivity library step (the only way to select a DPI preset)
# ---------------------------------------------------------------------------

def test_library_step_writes_presets_and_selected_preset(lib_on):
    mouse = _FakeMouse()
    step = dc.sensitivity_library_step([400, 3200], 1, mouse_factory=lambda: mouse)
    ok, out = step()
    assert ok is True and out == ""
    assert mouse.calls[0] == ("set_sensitivity", [400, 3200], 1)
    assert ("save",) in mouse.calls
    assert mouse.closed is True


def test_library_step_preset_byte_matches_handler_contract(lib_on):
    """Selected preset 1 must land as ``0x1`` in the packet's second byte.

    The packet is built by rivalcfg's own handler, from the device's real
    profile, so this pins the *contract* -- and shows what the CLI can never
    send, since the CLI's only argument is the DPI list.
    """
    from rivalcfg.handlers import multidpi_range_choice as h

    setting_info = _aerox5_profile()["settings"]["sensitivity"]
    if not setting_info.get("output_choices"):
        pytest.skip("rivalcfg library not importable")

    mouse = _FakeMouse()
    dc.sensitivity_library_step([400, 3200], 1, mouse_factory=lambda: mouse)()
    _, values, selected = mouse.calls[0]

    packet = h.process_value(setting_info, values, selected_preset=selected)
    assert packet[0] == 2                      # preset count
    assert packet[1] == 1                      # selected preset
    # The CLI path sends no selected_preset at all, and the handler then falls
    # back to first_preset -- i.e. it drags the mouse back to preset 1.
    cli_packet = h.process_value(setting_info, "400,3200")
    assert cli_packet[1] == setting_info["first_preset"]


def test_library_step_save_false_skips_save(lib_on):
    mouse = _FakeMouse()
    ok, _ = dc.sensitivity_library_step([800], 0, save=False,
                                        mouse_factory=lambda: mouse)()
    assert ok is True
    assert ("save",) not in mouse.calls
    assert mouse.closed is True


def test_library_step_never_raises_on_missing_device(lib_on):
    def factory():
        raise OSError("[Errno 19] No such device")

    ok, out = dc.sensitivity_library_step([800], 0, mouse_factory=factory)()
    assert ok is False
    assert "turned on" in out


def test_library_step_closes_mouse_when_set_fails(lib_on):
    mouse = _FakeMouse(fail_on="set_sensitivity")
    ok, out = dc.sensitivity_library_step([800], 0, mouse_factory=lambda: mouse)()
    assert ok is False and "No such device" in out
    assert mouse.closed is True


def test_library_step_save_failure_is_reported(lib_on):
    mouse = _FakeMouse(fail_on="save")
    ok, out = dc.sensitivity_library_step([800], 0, mouse_factory=lambda: mouse)()
    assert ok is False and "save failed" in out
    assert mouse.closed is True


def test_library_step_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(dc, "USE_LIBRARY_WRITES", False)
    assert dc.sensitivity_library_step([800], 0) is None


def test_library_step_no_values_returns_none():
    assert dc.sensitivity_library_step([], 0) is None


def test_add_sensitivity_falls_back_to_cli_argv(monkeypatch):
    monkeypatch.setattr(dc, "USE_LIBRARY_WRITES", False)
    plan = dc.add_sensitivity(dc.ApplyPlan("Sensitivity"), [400, 800], 1)
    assert plan.steps == [["--sensitivity", "400,800"]]


def test_add_sensitivity_uses_library_when_available(monkeypatch):
    monkeypatch.setattr(dc, "USE_LIBRARY_WRITES", True)
    plan = dc.add_sensitivity(dc.ApplyPlan("Sensitivity"), [400, 800], 1)
    assert len(plan.steps) == 1 and callable(plan.steps[0])


def test_plan_add_keeps_callable_step():
    def step():
        return True, ""

    plan = dc.ApplyPlan("x").add(step).add(["--polling-rate", "1000"])
    assert plan.steps[0] is step
    assert plan.steps[1] == ["--polling-rate", "1000"]


def test_plan_add_ignores_empty():
    assert len(dc.ApplyPlan("x").add(None).add([])) == 0


# ---------------------------------------------------------------------------
# DeviceManager (library primary / CLI fallback)
# ---------------------------------------------------------------------------

class _FakeDevices:
    def __init__(self, raise_on_list=False):
        self._raise = raise_on_list

    def list_plugged_devices(self):
        if self._raise:
            raise RuntimeError("boom")
        yield {"vendor_id": 0x1038, "product_id": 0x1854, "name": "Aerox 5"}

    def get_profile(self, vid, pid):
        return _aerox5_profile()


def test_devicemanager_library_primary():
    dm = dc.DeviceManager(devices_mod=_FakeDevices())
    devices = dm.list_devices()
    assert len(devices) == 1
    caps = dm.get_caps()
    assert caps.source == "library"
    assert caps.lighting.has_rainbow


def test_devicemanager_fallback_to_help():
    dm = dc.DeviceManager(devices_mod=None, help_provider=lambda: AEROX5_HELP)
    dm._devices_mod = None
    # Force the library lookup to fail by making _devices return None.
    dm._devices = lambda: None  # type: ignore
    caps = dm.get_caps()
    assert caps.source == "cli"
    assert (caps.dpi_min, caps.dpi_max) == (100, 18000)
    assert caps.lighting.has_rainbow
    assert caps.has_extra_buttons


def test_caps_from_help_text_parses_lighting():
    caps = dc.caps_from_help_text(AEROX5_HELP)
    assert [z.cli for z in caps.lighting.zones][:3] == ["--top-color", "--middle-color", "--bottom-color"]
    assert caps.lighting.has_default_lighting
    assert caps.sleep_timer is None  # help parser does not infer timers


# ---------------------------------------------------------------------------
# CommandQueue
# ---------------------------------------------------------------------------

def test_queue_runs_plan_atomically_in_order():
    calls = []
    lock = threading.Lock()

    def runner(argv):
        with lock:
            calls.append(list(argv))
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=0)
    done = []
    plan = dc.ApplyPlan("t", [["a", "1"], ["b", "2"]], on_done=lambda ok, out: done.append(ok))
    q.enqueue(plan)
    assert q.wait_idle(5)
    assert calls == [["a", "1"], ["b", "2"]]
    assert done == [True]
    q.stop()


def test_queue_stops_on_first_failure_and_reports_step():
    calls = []

    def runner(argv):
        calls.append(list(argv))
        if argv[0] == "b":
            return False, "bad thing"
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=0)
    results = []
    statuses = []
    q.set_status_callback(lambda kind, msg: statuses.append((kind, msg)))
    plan = dc.ApplyPlan("t", [["a"], ["b"], ["c"]],
                        on_done=lambda ok, out: results.append((ok, out)))
    q.enqueue(plan)
    assert q.wait_idle(5)
    assert calls == [["a"], ["b"]]
    assert results == [(False, "bad thing")]
    assert any(kind == "error" and "b" in msg for kind, msg in statuses)
    q.stop()


def test_queue_runs_a_callable_step_before_argv_steps():
    """A library step is just another step: it runs in order, on the worker."""
    calls = []

    def runner(argv):
        calls.append(list(argv))
        return True, ""

    def library_step():
        calls.append("library")
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=0)
    done = []
    plan = dc.ApplyPlan("t", [library_step, ["--polling-rate", "1000"]],
                        on_done=lambda ok, out: done.append(ok))
    q.enqueue(plan)
    assert q.wait_idle(5)
    assert calls == ["library", ["--polling-rate", "1000"]]
    assert done == [True]
    q.stop()


def test_queue_reports_callable_step_failure():
    def fail():
        return False, "no device"

    q = dc.CommandQueue(lambda argv: (True, ""), debounce_ms=0)
    statuses = []
    q.set_status_callback(lambda kind, msg: statuses.append((kind, msg)))
    plan = dc.ApplyPlan("Sensitivity", [fail],
                        on_done=lambda ok, out: statuses.append(("done", out)))
    q.enqueue(plan)
    assert q.wait_idle(5)
    assert ("error", "fail: no device") in statuses
    assert ("done", "no device") in statuses
    q.stop()


def test_queue_reports_a_raising_callable_step():
    def boom():
        raise RuntimeError("kaboom")

    q = dc.CommandQueue(lambda argv: (True, ""), debounce_ms=0)
    statuses = []
    q.set_status_callback(lambda kind, msg: statuses.append((kind, msg)))
    q.enqueue(dc.ApplyPlan("Sensitivity", [boom]))
    assert q.wait_idle(5)
    assert any(kind == "error" and "kaboom" in msg for kind, msg in statuses)
    q.stop()


def test_queue_serializes_never_interleaves():
    active = [0]
    max_active = [0]
    lock = threading.Lock()

    def runner(argv):
        with lock:
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=0)
    for i in range(4):
        q.enqueue(dc.ApplyPlan("p%d" % i, [["x"]]))
    assert q.wait_idle(5)
    assert max_active[0] == 1
    q.stop()


def test_queue_debounce_coalesces():
    calls = []

    def runner(argv):
        calls.append(list(argv))
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=40)
    for value in ("1", "2", "3"):
        q.enqueue_debounced("dpi", dc.ApplyPlan("dpi", [["--sensitivity", value]]))
    time.sleep(0.15)
    assert q.wait_idle(5)
    assert calls == [["--sensitivity", "3"]]
    q.stop()


def test_queue_one_status_per_plan():
    statuses = []

    def runner(argv):
        return True, ""

    q = dc.CommandQueue(runner, debounce_ms=0)
    q.set_status_callback(lambda kind, msg: statuses.append(kind))
    q.enqueue(dc.ApplyPlan("Apply settings", [["a"], ["b"], ["c"]]))
    assert q.wait_idle(5)
    assert statuses == ["running", "ok"]
    q.stop()


def test_plan_label_for_args():
    assert dc.plan_label_for_args(["--sensitivity", "800"]) == "DPI"
    assert dc.plan_label_for_args(["--top-color", "ff0000"]) == "Lighting"
    assert dc.plan_label_for_args(["--buttons", "buttons(...)"]) == "Buttons"
    assert dc.plan_label_for_args(["--reset"]) == "Factory reset"


def test_keyboard_keys_available():
    keys = dc.keyboard_keys()
    assert "A" in keys or "a" in keys
    assert len(keys) > 20


def test_keyboard_keys_are_in_keyboard_order_not_alphabetical():
    keys = dc.keyboard_keys()
    # The bug this replaces: a string sort put "'" first and ordered "F1"
    # before "F10".
    assert keys[:3] == ["A", "B", "C"]
    assert keys.index("F1") < keys.index("F10")
    assert keys.index("F12") < keys.index("F13")
    assert keys.index("9") < keys.index("0")
    assert keys.index("Z") < keys.index("1")
    # The aliases were duplicate junk: same key, second name.
    for junk in ("bkspace", "bksp", "esc", "del", "minus", "eq", "dash",
                 "leftbracket", "hash"):
        assert junk not in keys


def test_keyboard_keys_are_grouped_and_nothing_is_dropped():
    groups = dc.keyboard_groups()
    assert [gid for gid, _names in groups] == [
        "Letters", "Numbers", "Editing", "Punctuation", "Function keys",
        "Navigation", "Modifiers",
    ]
    grouped = [name for _gid, names in groups for name in names]
    assert grouped == dc.keyboard_keys()
    assert len(grouped) == len(set(grouped)), "a key is offered twice"
    assert len(grouped) == 100
    assert set(groups[0][1]) == set("abcdefghijklmnopqrstuvwxyz".upper())


def test_key_symbol_names_are_what_gdk_keyval_name_emits():
    # Gdk.keyval_name() says "Page_Up", never the X11 spelling "Prior"; a
    # capture table holding "Prior" would silently never match.
    assert dc.key_symbol_name("PageUp") == "Page_Up"
    assert dc.key_symbol_name("PageDown") == "Page_Down"
    assert dc.key_symbol_name("A") == "a"
    assert dc.key_symbol_name("7") == "7"
    assert dc.key_symbol_name("F13") == "F13"
    assert dc.key_symbol_name("LeftCtrl") == "Control_L"
    assert dc.key_symbol_name("RightSuper") == "Super_R"
    assert dc.key_symbol_name("Enter") == "Return"
    assert dc.key_symbol_name("'") == "apostrophe"


@pytest.mark.parametrize("name", dc.keyboard_keys())
def test_every_offered_key_captures_back_to_itself(name):
    """Pressing any key in the picker must name the same key it offers."""
    assert dc.key_for_symbol(dc.key_symbol_name(name)) == name


def test_capture_accepts_the_spellings_gtk_really_sends():
    assert dc.key_for_symbol("a") == "A"
    assert dc.key_for_symbol("A") == "A"        # Shift+letter
    assert dc.key_for_symbol("Prior") == "PageUp"
    assert dc.key_for_symbol("Next") == "PageDown"
    assert dc.key_for_symbol("space") == "Space"
    assert dc.key_for_symbol("Return") == "Enter"


def test_shifted_symbols_fold_onto_the_key_that_makes_them():
    assert dc.key_for_symbol("exclam") == "1"
    assert dc.key_for_symbol("parenright") == "0"
    assert dc.key_for_symbol("underscore") == "-"
    assert dc.key_for_symbol("plus") == "="
    assert dc.key_for_symbol("braceleft") == "["
    assert dc.key_for_symbol("bar") == "\\"
    assert dc.key_for_symbol("asciitilde") == "`"
    # "#" is a layout name as well as Shift+3, but it is not offered, so the
    # shifted reading wins rather than becoming an unreachable key.
    assert dc.key_for_symbol("numbersign") == "3"


def test_capture_ignores_modifiers_and_unnameable_keys():
    assert dc.is_modifier_symbol("Control_L")
    assert dc.is_modifier_symbol("Shift_R")
    assert dc.is_modifier_symbol("Super_L")
    assert not dc.is_modifier_symbol("a")
    assert not dc.is_modifier_symbol("Return")
    # Media keys, dead keys and other layouts' keys are not encodable.
    for unknown in ("MonBrightnessDown", "Multi_key", "KP_Enter", "", None):
        assert dc.key_for_symbol(unknown) is None


# ---------------------------------------------------------------------------
# §1 canonical-order matrix (reactive x default-lighting x rainbow)
# ---------------------------------------------------------------------------

def _matrix_plan(caps, reactive, default_lighting, rainbow):
    state = {
        "zones": {"z1_color": "ff0000", "z2_color": "00ff00", "z3_color": "0000ff"},
        "reactive": reactive,
        "default_lighting": default_lighting,
        "rainbow": rainbow,
    }
    return dc.build_lighting_plan(caps, state).steps


@pytest.mark.parametrize("reactive", ["off", "ff0000"])
@pytest.mark.parametrize("default_lighting", ["off", "rainbow"])
@pytest.mark.parametrize("rainbow", [False, True])
def test_matrix_order_invariant(caps, reactive, default_lighting, rainbow):
    steps = _matrix_plan(caps, reactive, default_lighting, rainbow)
    flags = _all_flags(dc.ApplyPlan("x", steps))
    # Zone colors always first, rainbow always last.
    assert flags[0] == "--top-color"
    assert flags[1] == "--middle-color"
    assert flags[2] == "--bottom-color"
    if rainbow:
        assert flags[-1] == "--rainbow-effect"
    # Reactive is programmed iff a color is set (click flash decoupled).
    assert ("--reactive-color" in flags) is True  # always sent, value carries on/off
    # Default lighting never comes after the rainbow flag.
    if "--rainbow-effect" in flags:
        assert flags.index("--default-lighting") < flags.index("--rainbow-effect")


def test_matrix_rainbow_off_never_sends_default_after_colors_inconsistently(caps):
    # Rainbow off => no rainbow flag; colors + reactive + default lighting only.
    steps = _matrix_plan(caps, "off", "off", False)
    assert steps == [
        ["--top-color", "ff0000", "--middle-color", "00ff00", "--bottom-color", "0000ff"],
        ["--reactive-color", "off"],
        ["--default-lighting", "off"],
    ]


# ---------------------------------------------------------------------------
# Profile migration v1 -> v2
# ---------------------------------------------------------------------------

def test_migrate_v1_profile():
    v1 = {
        "dpi_values": [400, 800], "polling_hz": 250, "z1_hex": "aabbcc",
        "z2_hex": "112233", "reactive_hex": "ff0000", "rainbow_enabled": True,
        "selected_effect": "breath", "button_mapping": {"button1": "disabled"},
        "macro_cps": 10, "macro_trigger_key": "f6",
    }
    out = dc.migrate_profile(v1)
    assert out["schema"] == dc.PROFILE_SCHEMA
    assert out["zones"] == {"z1_color": "aabbcc", "z2_color": "112233"}
    assert out["reactive"] == "ff0000"
    assert out["rainbow"] is True
    assert out["light_effect"] == "breath"
    assert "macro_cps" not in out
    assert out["dpi_values"] == [400, 800]
    assert out["dpi_active_index"] == 0        # v1 has no active preset


def test_migrate_invalid_profile_never_raises():
    assert dc.migrate_profile(None) is None
    out = dc.migrate_profile({"dpi_values": "nonsense", "polling_hz": "x"})
    assert out["schema"] == 2
    # Bad numbers fall back rather than crash.
    assert out["polling_hz"] == 1000
    assert out["dpi_active_index"] == 0


def test_migrate_keeps_active_dpi_preset():
    out = dc.migrate_profile({"dpi_values": [400, 800, 1600], "dpi_active_index": 2})
    assert out["dpi_active_index"] == 2
    # Garbage falls back to the first preset rather than crashing the page.
    out = dc.migrate_profile({"dpi_values": [400, 800], "dpi_active_index": "x"})
    assert out["dpi_active_index"] == 0


# ---------------------------------------------------------------------------
# Lighting mode -> raw state (the page's single translation point)
# ---------------------------------------------------------------------------

ZONES = {"z1_color": "ff0000", "z2_color": "00ff00", "z3_color": "0000ff"}


def test_mode_steady_keeps_colours_and_asks_for_no_rainbow():
    out = dc.lighting_mode_state("steady", ZONES)
    assert out["zones"] == ZONES
    assert out["rainbow"] is False
    assert out["default_lighting"] == "off"
    assert out["reactive"] == "off"


def test_mode_steady_keeps_the_wake_value_the_advanced_control_holds():
    """Steady must not derive the wake value, or that control is dead."""
    out = dc.lighting_mode_state("steady", ZONES, flash_hex="00ff00",
                                 wake="reactive-rainbow")
    assert out["reactive"] == "00ff00"
    assert out["default_lighting"] == "reactive-rainbow"


def test_mode_steady_still_derives_a_sane_wake_when_none_is_given():
    assert dc.lighting_mode_state(
        "steady", ZONES, flash_hex="00ff00")["default_lighting"] == "reactive"
    assert dc.lighting_mode_state(
        "steady", ZONES)["default_lighting"] == "off"


def test_mode_rainbow_carries_its_wake_lighting_with_it():
    """The rainbow is forgotten on sleep unless the wake lighting asks for it.

    Leaving the two independent is exactly what let the old page build a mouse
    that goes dark every time it wakes (PLAN.md section 1, matrix row 2).
    """
    out = dc.lighting_mode_state("rainbow", ZONES)
    assert out["rainbow"] is True
    assert out["default_lighting"] == "rainbow"


@pytest.mark.parametrize("mode", list(dc.LIGHTING_MODES))
def test_no_mode_can_produce_the_dark_trap(mode):
    """Rainbow on + wake off = no light. No mode may send that pair."""
    out = dc.lighting_mode_state(mode, ZONES)
    assert not (out["rainbow"] and out["default_lighting"] == "off")


def test_mode_off_blackens_every_zone():
    out = dc.lighting_mode_state("off", ZONES)
    assert set(out["zones"]) == set(ZONES)
    assert set(out["zones"].values()) == {dc.OFF_COLOR}
    assert out["rainbow"] is False
    assert out["default_lighting"] == "off"


@pytest.mark.parametrize("mode", ["colorshift", "breathe"])
def test_host_side_modes_seed_the_zones_from_the_palette(mode):
    """So the mouse settles on real colours when the app closes."""
    palette = ["ff0000", "00ff00", "0000ff"]
    out = dc.lighting_mode_state(mode, ZONES, palette=palette)
    assert out["zones"] == {"z1_color": "ff0000", "z2_color": "00ff00",
                            "z3_color": "0000ff"}
    assert out["rainbow"] is False


def test_host_side_mode_without_a_palette_keeps_the_zone_colours():
    out = dc.lighting_mode_state("colorshift", ZONES)
    assert out["zones"] == ZONES


@pytest.mark.parametrize("mode", list(dc.LIGHTING_MODES))
def test_click_flash_stays_independent_of_the_mode(mode):
    """PLAN.md section 1: the flash happens iff a reactive colour is set."""
    off = dc.lighting_mode_state(mode, ZONES, flash_hex="off")
    on = dc.lighting_mode_state(mode, ZONES, flash_hex="ff00ff")
    assert off["reactive"] == "off"
    assert on["reactive"] == "ff00ff"


def test_mode_state_feeds_the_lighting_plan(caps):
    """The translation and the plan agree -- end to end, no CLI needed."""
    state = dc.lighting_mode_state("rainbow", ZONES, flash_hex="00ff00")
    plan = dc.build_lighting_plan(caps, state)
    assert plan.steps[-1] == ["--rainbow-effect"]
    assert ["--reactive-color", "00ff00"] in plan.steps
    assert ["--default-lighting", "rainbow"] in plan.steps


# ---------------------------------------------------------------------------
# LightingAnimator (host-side effects)
# ---------------------------------------------------------------------------

class _FakeAnimMouse:
    """Records zone writes and, crucially, whether save() was ever called."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.closed = False
        self.fail_on = fail_on
        self.command_delay = None

    def set_z1_color(self, value):
        self._write("z1_color", value)

    def set_z2_color(self, value):
        self._write("z2_color", value)

    def set_z3_color(self, value):
        self._write("z3_color", value)

    def _write(self, key, value):
        self.calls.append((key, value))
        if self.fail_on == key:
            raise OSError("[Errno 19] No such device")

    def save(self):
        self.calls.append(("save",))

    def close(self):
        self.closed = True


ANIM_ZONES = ["z1_color", "z2_color", "z3_color"]


def _animator(mouse, **kwargs):
    return dc.LightingAnimator(ANIM_ZONES, mouse_factory=lambda: mouse, **kwargs)


def test_animator_writes_each_zone_in_order():
    mouse = _FakeAnimMouse()
    anim = _animator(mouse)
    assert anim.open() == (True, "")
    assert anim.write(["ff0000", "00ff00", "0000ff"]) == (True, "")
    assert mouse.calls == [("z1_color", "ff0000"),
                           ("z2_color", "00ff00"),
                           ("z3_color", "0000ff")]


def test_animator_never_saves_to_the_mouse():
    """A save per frame would rewrite internal memory twenty times a second."""
    mouse = _FakeAnimMouse()
    anim = _animator(mouse)
    anim.open()
    for _ in range(5):
        anim.write(["ff0000", "00ff00", "0000ff"])
    anim.close()
    assert not any(c[0] == "save" for c in mouse.calls)


def test_animator_lowers_the_command_delay():
    """The library default caps a three-write frame at ~6 fps."""
    mouse = _FakeAnimMouse()
    _animator(mouse).open()
    assert mouse.command_delay == dc.ANIM_COMMAND_DELAY
    assert dc.ANIM_COMMAND_DELAY < 0.05


def test_animator_write_failure_drops_the_handle():
    """A dead link must not be retried into -- reopen on the next request."""
    mouse = _FakeAnimMouse(fail_on="z2_color")
    anim = _animator(mouse)
    anim.open()
    ok, message = anim.write(["ff0000", "00ff00", "0000ff"])
    assert ok is False
    assert "Cannot reach the mouse" in message
    assert anim.active is False
    assert mouse.closed is True


def test_animator_open_failure_is_reported_not_raised():
    def boom():
        raise OSError("[Errno 19] No such device")

    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=boom)
    ok, message = anim.open()
    assert ok is False
    assert "Cannot reach the mouse" in message
    assert anim.active is False


def test_animator_close_is_idempotent_and_write_after_close_fails():
    mouse = _FakeAnimMouse()
    anim = _animator(mouse)
    anim.open()
    anim.close()
    anim.close()
    assert mouse.closed is True
    ok, message = anim.write(["ff0000", "00ff00", "0000ff"])
    assert ok is False
    assert "not open" in message


def test_animator_open_is_a_noop_when_already_open():
    mouse = _FakeAnimMouse()
    anim = _animator(mouse)
    anim.open()
    assert anim.open() == (True, "")
    assert anim.active is True


def test_animator_start_streams_frames_until_stopped():
    mouse = _FakeAnimMouse()
    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=lambda: mouse,
                               frame_interval=0.001)
    frames = [["ff0000", "00ff00", "0000ff"], ["0000ff", "ff0000", "00ff00"]]

    def provider(t):
        # Bounded so a broken stop() cannot leave a thread spinning forever.
        return frames[min(int(t * 1000), len(frames) - 1)]

    assert anim.start(provider) == (True, "")
    deadline = time.monotonic() + 5.0
    while len(mouse.calls) < 6 and time.monotonic() < deadline:
        time.sleep(0.005)
    anim.stop()
    assert not anim.animating
    assert len([c for c in mouse.calls if c[0] == "z1_color"]) >= 2
    assert not any(c[0] == "save" for c in mouse.calls)


def test_animator_stop_leaves_the_resting_colours_on_the_mouse():
    """Whatever frame the loop died on, the mouse settles on the profile."""
    mouse = _FakeAnimMouse()
    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=lambda: mouse,
                               frame_interval=0.001)
    anim.start(lambda t: ["111111", "222222", "333333"])
    deadline = time.monotonic() + 5.0
    while not mouse.calls and time.monotonic() < deadline:
        time.sleep(0.005)
    anim.stop(final_hexes=["ff0000", "00ff00", "0000ff"])
    assert mouse.calls[-3:] == [("z1_color", "ff0000"),
                                ("z2_color", "00ff00"),
                                ("z3_color", "0000ff")]
    assert mouse.closed is True


def test_animator_reports_a_dropped_device_and_stops_itself():
    mouse = _FakeAnimMouse(fail_on="z1_color")
    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=lambda: mouse,
                               frame_interval=0.001)
    seen = []
    anim.start(lambda t: ["ff0000", "00ff00", "0000ff"],
               on_error=seen.append)
    deadline = time.monotonic() + 5.0
    while anim.animating and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not anim.animating
    assert seen and "Cannot reach the mouse" in seen[0]
    # The handle was dropped, so a later stop does not write into it.
    ok, _ = anim.stop(final_hexes=["ff0000", "00ff00", "0000ff"])
    assert ok is True


def test_animator_start_failure_reports_rather_than_raises():
    def boom():
        raise OSError("[Errno 19] No such device")

    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=boom)
    ok, message = anim.start(lambda t: ["ff0000", "00ff00", "0000ff"])
    assert ok is False
    assert "Cannot reach the mouse" in message
    assert not anim.animating


def test_animator_stop_without_start_is_harmless():
    anim = dc.LightingAnimator(ANIM_ZONES, mouse_factory=_FakeAnimMouse)
    assert anim.stop() == (True, "")
    assert not anim.animating


# ---------------------------------------------------------------------------
# Profile migration: the lighting mode survives, and old files get one
# ---------------------------------------------------------------------------

def _profile(**kw):
    data = {"schema": 2, "zones": {"z1_color": "ff0000"}}
    data.update(kw)
    return dc.migrate_profile(data)


def test_migration_keeps_a_stored_mode():
    assert _profile(lighting_mode="breathe")["lighting_mode"] == "breathe"


def test_migration_rejects_an_unknown_mode_by_deriving_one():
    # A corrupt or hand-edited value must not reach the page as a mode it
    # cannot render.
    assert _profile(lighting_mode="disco-inferno",
                    rainbow=True)["lighting_mode"] == "rainbow"


def test_migration_derives_rainbow_for_an_old_profile():
    """Profiles from before the mode-first page have no mode at all."""
    assert _profile(rainbow=True)["lighting_mode"] == "rainbow"


def test_migration_derives_off_for_an_all_black_profile():
    out = _profile(zones={"z1_color": "000000", "z2_color": "000000"})
    assert out["lighting_mode"] == "off"


def test_migration_derives_steady_for_anything_else():
    out = _profile(zones={"z1_color": "ff0000"}, rainbow=False)
    assert out["lighting_mode"] == "steady"


def test_migration_keeps_a_palette_and_speed():
    out = _profile(fx_palette=["ff0000", "00ff00"], fx_speed=12)
    assert out["fx_palette"] == ["ff0000", "00ff00"]
    assert out["fx_speed"] == 12.0


def test_migration_drops_a_broken_palette_and_speed():
    out = _profile(fx_palette="not-a-list", fx_speed="soon")
    assert "fx_palette" not in out
    assert "fx_speed" not in out
