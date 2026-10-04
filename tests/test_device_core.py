"""Tests for device_core: capabilities, plans and the command queue."""

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

    def __init__(self, fail_on=None):
        self.calls = []
        self.closed = False
        self.fail_on = fail_on

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
