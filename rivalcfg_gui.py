#!/usr/bin/env python3

# GTK3 dependency check
import sys
import os
import json
import subprocess
import gettext
import locale
import logging
import re
from logging.handlers import TimedRotatingFileHandler

def _get_locale_dir():
    locale_env = os.environ.get("RIVALCFG_GUI_LOCALE_DIR")
    if locale_env:
        return locale_env
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "locales")

LOCALE_DIR = _get_locale_dir()

def setup_gettext(lang=None):
    """Setup gettext translation for the given language."""
    if not lang:
        lang = locale.getlocale()[0][:2] if locale.getlocale()[0] else "en"
    try:
        translation = gettext.translation("rivalcfg_gui", localedir=LOCALE_DIR, languages=[lang], fallback=True)
    except FileNotFoundError:
        translation = gettext.NullTranslations()
    return translation.gettext

_ = setup_gettext()


def _set_language(lang=None):
    """Update the global _ function to use the specified language."""
    global _
    _ = setup_gettext(lang)

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
except ImportError:
    logging.critical("python-gobject is not installed. Install: pacman -S python-gobject")
    print(_("python-gobject is not installed. Install: pacman -S python-gobject"))
    sys.exit(1)

import device_core
import widgets

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
FLATPAK_ID = os.environ.get("FLATPAK_ID")
IN_FLATPAK = FLATPAK_ID is not None

# Prefer bundled rivalcfg binary, fall back to pip-installed or system PATH
def _find_rivalcfg():
    bundled = os.path.join(SCRIPT_DIR, "rivalcfg")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return "rivalcfg"

RIVALCFG_BIN = _find_rivalcfg()

# Check rivalcfg CLI availability
try:
    subprocess.run([RIVALCFG_BIN], capture_output=True, timeout=5)
except FileNotFoundError:
    logging.critical("rivalcfg is not installed. Install: pip install rivalcfg")
    print(_("rivalcfg is not installed. Install: pip install rivalcfg"))
    sys.exit(1)

# Global dictionary holding application state
app_state = {}

# --- Device capabilities ---------------------------------------------------
# Device identity/capabilities now live in device_core (library-first, CLI
# fallback). The UI only talks to the DeviceManager / DeviceCaps model.

DEVICE_MANAGER = device_core.DeviceManager(help_provider=lambda: _rivalcfg_help_text())


def _rivalcfg_help_text():
    try:
        result = subprocess.run(
            [RIVALCFG_BIN, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or "") + "\n" + (result.stderr or "")
    except Exception:
        return ""


def get_device_caps(force=False):
    """DeviceCaps for the active device (or the primary plugged device).

    Kept as the single UI entry point; delegates to device_core.
    """
    if force:
        DEVICE_MANAGER.invalidate()
    return DEVICE_MANAGER.get_caps(refresh=force)


def get_primary_device():
    return DEVICE_MANAGER.primary_device()


def is_steelseries_connected(debug_text):
    """True when debug output shows any SteelSeries USB device (1038:xxxx)."""
    if not debug_text:
        return False
    return bool(re.search(r"1038:[0-9a-fA-F]{4}", debug_text))


def build_buttons_arg(mapping, caps=None):
    """Build the rivalcfg --buttons argument (single source of truth)."""
    if caps is None:
        caps = get_device_caps()
    return device_core.build_buttons_arg(caps, mapping)


def _caps_device_name(caps):
    return caps.name or _("Mouse connected")


SETTINGS_DIR = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
SETTINGS_DIR = os.path.join(SETTINGS_DIR, "rivalcfg-gui")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
LOGS_DIR = os.path.join(SETTINGS_DIR, "logs")

DEFAULT_SETTINGS = {
    "startup_minimize": False,
    "auto_apply": False,
    "accent_color": "#ff7800",
    "language": "en",
    "active_profile": "Default",
    "hyprland_mouse_sync": False,
    "hyprland_follow_mouse": 1,
}

#: Bumped whenever the on-disk profile/state shape changes.
PROFILE_SCHEMA = 2


def setup_logging():
    """Configure logging: daily rotating logs kept for 7 days."""
    os.makedirs(LOGS_DIR, exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    def log_namer(default_name):
        basedir = os.path.dirname(default_name)
        fname = os.path.basename(default_name)
        parts = fname.split(".")
        if len(parts) >= 3 and len(parts[-1]) == 10 and parts[-1][4] == "-":
            return os.path.join(basedir, f"{parts[-1]}.log")
        return default_name

    rotating = TimedRotatingFileHandler(
        os.path.join(LOGS_DIR, "app.log"),
        when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    rotating.namer = log_namer
    rotating.setFormatter(formatter)
    rotating.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(rotating)


def load_settings():
    """Load settings from JSON file."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                saved = json.load(f)
            settings = dict(DEFAULT_SETTINGS)
            settings.update(saved)
            return settings
    except Exception as e:
        logging.warning("Failed to load settings: %s", e)
    return dict(DEFAULT_SETTINGS)


def save_settings():
    """Save current settings to JSON file."""
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        to_save = {k: v for k, v in app_state["settings"].items() if not k.startswith("macro_")}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(to_save, f, indent=4)
    except Exception as e:
        logging.error("Failed to save settings: %s", e)
        print(_("Failed to save settings: {}").format(e))

PROFILES_DIR = os.path.join(SETTINGS_DIR, "profiles")

def ensure_profiles_dir():
    os.makedirs(PROFILES_DIR, exist_ok=True)

def list_profiles():
    ensure_profiles_dir()
    profiles = []
    for f in os.listdir(PROFILES_DIR):
        if f.endswith(".json"):
            profiles.append(f[:-5])
    return sorted(profiles) if profiles else ["Default"]

def migrate_profile(data):
    """Validate and migrate a profile dict to the current schema."""
    return device_core.migrate_profile(data)


def _current_zones():
    caps = get_device_caps()
    zones = app_state.get("zones")
    if isinstance(zones, dict) and zones:
        return dict(zones)
    # Seed from caps defaults.
    return {z.key: z.default for z in caps.lighting.zones}


def _current_lighting_state():
    """Lighting state dict consumed by device_core.build_lighting_plan()."""
    return {
        "zones": _current_zones(),
        "reactive": app_state.get("reactive_hex", "off"),
        "default_lighting": app_state.get("default_lighting"),
        "rainbow": app_state.get("rainbow_enabled", False),
        "rainbow_value": app_state.get("rainbow_value"),
        "light_effect": app_state.get("selected_effect"),
    }


def current_apply_state():
    """Full apply state consumed by device_core.build_full_plan()."""
    state = _current_lighting_state()
    state["dpi"] = app_state.get("dpi_values") or []
    state["dpi_active_index"] = app_state.get("dpi_active_index", 0)
    state["save"] = not app_state.get("no_save")
    state["polling"] = app_state.get("polling_hz")
    state["buttons"] = app_state.get("button_mapping")
    return state


def save_profile(name):
    ensure_profiles_dir()
    caps = get_device_caps()
    profile = {
        "schema": PROFILE_SCHEMA,
        "dpi_values": app_state.get("dpi_values", [800, 1600]),
        "dpi_active_index": app_state.get("dpi_active_index", 0),
        "polling_hz": app_state.get("polling_hz", 1000),
        "zones": _current_zones(),
        "reactive": app_state.get("reactive_hex", "off"),
        "rainbow": app_state.get("rainbow_enabled", False),
        "rainbow_value": app_state.get("rainbow_value", ""),
        "default_lighting": app_state.get("default_lighting", ""),
        "light_effect": app_state.get("selected_effect", ""),
        "button_mapping": app_state.get("button_mapping", {}),
    }
    if app_state.get("led_brightness") is not None:
        profile["led_brightness"] = app_state.get("led_brightness")
    if caps.vendor_id:
        profile["device_hint"] = {"vid": caps.vendor_id, "pid": caps.product_id}
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(profile, f, indent=4)
    logging.info("Profile saved: %s (dpi=%s, polling=%s)", name,
                 profile["dpi_values"], profile["polling_hz"])


def load_profile_data(name):
    ensure_profiles_dir()
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return migrate_profile(json.load(f))
    except Exception as e:
        logging.warning("Failed to load profile %s: %s", name, e)
        return None

def delete_profile_file(name):
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)

def rename_profile_file(old_name, new_name):
    if not new_name or old_name == new_name:
        return False
    ensure_profiles_dir()
    old_path = os.path.join(PROFILES_DIR, f"{old_name}.json")
    new_path = os.path.join(PROFILES_DIR, f"{new_name}.json")
    if not os.path.exists(old_path) or os.path.exists(new_path):
        return False
    os.rename(old_path, new_path)
    return True

def save_active_profile():
    name = app_state.get("settings", {}).get("active_profile", "Default")
    if name:
        save_profile(name)

CSS = """
window {
    background: #0a0a0f;
}

label {
    color: #ffffff;
}

button {
    background: #0f0f1a;
    color: #ffffff;
    border: 1px solid #2a2a40;
    border-radius: 6px;
    padding: 6px 16px;
}

entry {
    background: #0f0f1a;
    color: #ffffff;
    border: 1px solid #2a2a40;
    border-radius: 4px;
}

combobox {
    background: #0f0f1a;
    color: #ffffff;
    border: 1px solid #2a2a40;
    border-radius: 4px;
}

combobox window {
    background: #0f0f1a;
    color: #ffffff;
}

spinbutton {
    background: #0f0f1a;
    color: #ffffff;
    border: 1px solid #2a2a40;
    border-radius: 4px;
}

checkbutton {
    color: #ffffff;
}

.sidebar {
    background: #0d0d14;
    border-right: 1px solid #1e1e2e;
}

.sidebar combobox {
    min-width: 0;
}

.profile-selector {
    background: #0f0f1a;
    border: 1px solid #2a2a40;
    border-radius: 4px;
    padding: 6px 10px;
    min-height: 28px;
}

.profile-selector:hover {
    border-color: #3a3a55;
}

.profile-selector label {
    color: #ffffff;
    font-size: 13px;
}

.profile-popover {
    background: #0f0f1a;
    border: 1px solid #2a2a40;
    padding: 4px 0;
}

.profile-popover-row {
    padding: 2px 4px;
}

.profile-popover-row:hover {
    background-color: #1a1a2e;
}

.profile-menu-select {
    background: transparent;
    border: none;
    color: #ccccdd;
    font-size: 13px;
    padding: 6px 8px;
}

.profile-menu-select:hover {
    color: #ffffff;
}

.profile-menu-action {
    background: transparent;
    border: none;
    color: #888899;
    font-size: 14px;
    padding: 4px 6px;
    min-width: 28px;
    min-height: 28px;
}

.profile-menu-action:hover {
    background: #1a1a2e;
    color: #ccccdd;
}

.profile-menu-delete:hover {
    color: #ff7800;
}

.nav-btn {
    background: transparent;
    border: none;
    color: #888899;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
}

.nav-active {
    background: #1a0a14;
    color: #ff7800;
}

.page-title {
    font-size: 20px;
    font-weight: bold;
    color: #ffffff;
}

.card {
    background: #0f0f1a;
    border: 1px solid #1e1e2e;
    border-radius: 8px;
    padding: 20px;
}

.card-title {
    font-size: 11px;
    color: #ff7800;
    font-weight: bold;
}

.value-display {
    font-size: 26px;
    color: #ffffff;
    font-family: monospace;
    border: none;
    background: transparent;
    box-shadow: none;
}

.active-preset {
    font-size: 12px;
    font-weight: bold;
    color: #ff7800;
}

spinbutton.value-display button {
    -gtk-icon-source: none;
    min-width: 0;
    min-height: 0;
    padding: 0;
    border: none;
    background: transparent;
}

.apply-btn {
    background: #ff7800;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: bold;
}

.apply-btn:hover {
    background: #ff5555;
}

.reset-btn {
    background: transparent;
    border: 1px solid #2a2a40;
    color: #666688;
    border-radius: 6px;
    padding: 8px 20px;
}

.status-bar {
    background: #080810;
    border-top: 1px solid #1a1a28;
    padding: 6px 20px;
}

.status-running {
    color: #ffaa33;
}

.status-ok {
    color: #33cc77;
}

.status-error {
    color: #ff7800;
}

.status-bar-btn {
    background: transparent;
    border: none;
    color: #888899;
    padding: 2px 6px;
    min-width: 24px;
    min-height: 24px;
}

.status-bar-btn:hover {
    background: #1a1a2e;
    color: #ccccdd;
}

scale trough {
    background: #1a1a2e;
    min-height: 6px;
}

scale trough highlight {
    background: #ff7800;
}

scale slider {
    background: #ffffff;
    min-width: 16px;
    min-height: 16px;
}

.danger-btn {
    background: #2a0a0a;
    border: 1px solid #ff7800;
    color: #ff7800;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: bold;
}

.danger-btn:hover {
    background: #ff7800;
    color: white;
}

combobox {
    background: #0f0f1a;
    color: #ffffff;
    border: 1px solid #2a2a40;
    border-radius: 4px;
}

checkbutton label {
    color: #666688;
    font-size: 12px;
}

checkbutton:checked label {
    color: #ffaa33;
}

.setting-row {
    padding: 4px 0;
}

.setting-label {
    font-size: 13px;
    color: #ccccdd;
}

.setting-desc {
    font-size: 11px;
    color: #555566;
}

.color-preview {
    border: 1px solid #2a2a40;
    border-radius: 6px;
}

scrolledwindow scrollbar {
    background: transparent;
}
scrolledwindow scrollbar slider {
    background: #2a2a40;
    border-radius: 4px;
    min-width: 6px;
}
scrolledwindow scrollbar slider:hover {
    background: #3a3a55;
}
"""


def set_status(status_type, message):
    """Update status bar; must be called from the GUI thread."""
    dot = app_state["status_dot"]
    label = app_state["status_label"]

    dot.get_style_context().remove_class("status-running")
    dot.get_style_context().remove_class("status-ok")
    dot.get_style_context().remove_class("status-error")
    dot.get_style_context().add_class(f"status-{status_type}")

    label.set_text(message)


def _status_from_queue(kind, message):
    """CommandQueue status callback; marshalled onto the GUI thread."""
    if kind == "running":
        text = "⏳ " + _("Processing...") + " — " + message
    elif kind == "ok":
        text = "✓ " + _("Done") + " — " + message
    else:
        text = "✗ " + _("Error") + ": " + message
    GLib.idle_add(set_status, "running" if kind == "running" else ("ok" if kind == "ok" else "error"), text)


def _run_rivalcfg_sync(args):
    """Execute one rivalcfg command. Returns ``(ok, output)``.

    Runs on the CommandQueue worker thread; must never call GTK directly.
    """
    cmd = [RIVALCFG_BIN]
    if app_state.get("no_save"):
        cmd.append("--no-save")
    cmd.extend(args)
    logging.info("rivalcfg %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, _("rivalcfg is not installed. Install: pip install rivalcfg")
    except subprocess.TimeoutExpired:
        return False, _("Timeout (30s)")
    except Exception as e:  # pragma: no cover - defensive
        return False, "%s: %s" % (_("Unexpected error"), e)
    if result.returncode != 0:
        err = (result.stderr or "").strip() or _("Unknown error")
        return False, err
    return True, (result.stdout or "").strip()


def _init_command_queue():
    if app_state.get("command_queue") is not None:
        return app_state["command_queue"]
    q = device_core.CommandQueue(_run_rivalcfg_sync, status_cb=_status_from_queue)
    app_state["command_queue"] = q
    return q


def _queue_plan(plan):
    """Enqueue a full ApplyPlan (Phase 1+). One plan == one status update."""
    return _init_command_queue().enqueue(plan)


def _debounce_args(key, args, on_done=None):
    """Debounced single-command apply (sliders/spins: coalesce ticks)."""
    plan = device_core.ApplyPlan(device_core.plan_label_for_args(args), [list(args)], on_done=on_done)
    return _init_command_queue().enqueue_debounced(key, plan)


def run_rivalcfg(args, on_done=None):
    """Compatibility wrapper: queue a single rivalcfg invocation.

    All calls funnel through the CommandQueue so no two subprocesses ever race
    for the HID device (PLAN.md W6). ``on_done`` is invoked on the GUI thread.
    """
    def _done(ok, out):
        if on_done:
            GLib.idle_add(on_done, ok, out)
    _init_command_queue().enqueue_args(list(args), on_done=_done)



def rgba_to_hex(rgba):
    """Convert Gdk.RGBA to lowercase hex string without #."""
    r = int(rgba.red * 255)
    g = int(rgba.green * 255)
    b = int(rgba.blue * 255)
    return f"{r:02x}{g:02x}{b:02x}"


_NAMED_COLORS = {
    "white": "ffffff", "silver": "c0c0c0", "gray": "808080", "grey": "808080",
    "black": "000000", "maroon": "800000", "red": "ff0000", "purple": "800080",
    "fuchsia": "ff00ff", "green": "008000", "lime": "00ff00", "olive": "808000",
    "yellow": "ffff00", "navy": "000080", "blue": "0000ff", "teal": "008080",
    "aqua": "00ffff", "cyan": "00ffff", "magenta": "ff00ff",
}


def color_to_hex(value, default="ff6600"):
    """Normalise a profile colour (named, #rgb, #rrggbb) to bare hex."""
    if not value:
        return default
    v = str(value).strip().lower()
    if v in ("off", "disable", "none"):
        return default
    if v.startswith("#"):
        v = v[1:]
    if re.fullmatch(r"[0-9a-f]{6}", v):
        return v
    if re.fullmatch(r"[0-9a-f]{3}", v):
        return "".join(c * 2 for c in v)
    return _NAMED_COLORS.get(v, default)


def _hex_to_rgb(hexv):
    """Bare hex -> (r, g, b) floats in 0..1."""
    try:
        h = str(hexv).lstrip("#")
        return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    except Exception:
        return 0.0, 0.0, 0.0





_IS_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))
IS_HYPRLAND = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
HYPRLAND_AVAILABLE = False
if IS_HYPRLAND:
    try:
        subprocess.run(["hyprctl", "version"], capture_output=True, timeout=5)
        HYPRLAND_AVAILABLE = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        HYPRLAND_AVAILABLE = False


def _hyprctl_get_mouse_settings():
    """Get current Hyprland mouse settings via hyprctl."""
    if not HYPRLAND_AVAILABLE:
        return {}
    try:
        result = subprocess.run(
            ["hyprctl", "getoption", "input:follow_mouse"],
            capture_output=True, text=True, timeout=5
        )
        settings = {"follow_mouse": 1}
        for line in result.stdout.splitlines():
            if "follow_mouse" in line:
                try:
                    settings["follow_mouse"] = int(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass
        result = subprocess.run(
            ["hyprctl", "getoption", "input:sensitivity"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "sensitivity" in line:
                try:
                    settings["sensitivity"] = float(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass
        return settings
    except Exception as e:
        logging.error("Failed to get Hyprland mouse settings: %s", e)
        return {}


def _hyprctl_set_mouse_sensitivity(sensitivity):
    """Set Hyprland mouse sensitivity."""
    if not HYPRLAND_AVAILABLE:
        return False
    try:
        result = subprocess.run(
            ["hyprctl", "keyword", "input:sensitivity", str(sensitivity)],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception as e:
        logging.error("Failed to set Hyprland sensitivity: %s", e)
        return False


def _hyprctl_set_follow_mouse(follow):
    """Set Hyprland follow_mouse setting."""
    if not HYPRLAND_AVAILABLE:
        return False
    try:
        result = subprocess.run(
            ["hyprctl", "keyword", "input:follow_mouse", str(follow)],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception as e:
        logging.error("Failed to set Hyprland follow_mouse: %s", e)
        return False


def _hyprctl_sync_mouse_to_rivalcfg(dpi_value):
    """Sync Hyprland mouse DPI/sensitivity with rivalcfg settings."""
    if not HYPRLAND_AVAILABLE or dpi_value is None:
        return False
    sensitivity = max(-1.0, min(1.0, (dpi_value - 800) / 3700.0))
    return _hyprctl_set_mouse_sensitivity(sensitivity)


def _dpi_active_value():
    """The DPI value the app treats as the active preset (clamped, never raises).

    The mouse's live CPI stage cannot be read back (rivalcfg has no state
    query), so this is the preset the app *sets* -- on Apply, or on a radio
    click when auto-apply is on. See ``create_sensitivity_page``.
    """
    vals = app_state.get("dpi_values") or []
    if not vals:
        return None
    idx = app_state.get("dpi_active_index", 0)
    if not isinstance(idx, int) or not (0 <= idx < len(vals)):
        idx = 0
        app_state["dpi_active_index"] = 0
    return vals[idx]


def _sync_hyprland_to_active_dpi():
    """Mirror the active DPI preset into Hyprland sensitivity.

    Local system setting only — never a device write.
    """
    if not (app_state["settings"].get("hyprland_mouse_sync") and HYPRLAND_AVAILABLE):
        return False
    value = _dpi_active_value()
    if value is None:
        return False
    return _hyprctl_sync_mouse_to_rivalcfg(value)


def create_sensitivity_page():
    """Create the Sensitivity page: DPI presets + polling rate, one page.

    DPI presets and the polling rate are two halves of the same "how does the
    pointer feel" question and were too sparse as separate pages.

    The active DPI preset is picked with a radio beside each row, stored in the
    profile as ``dpi_active_index`` and mirrored into Hyprland mouse sync. On
    Apply (or on a click, when auto-apply is on) it is sent to the mouse through
    the rivalcfg *library*, which is the only way to select a preset -- the CLI
    resets the selection to the first preset on every write. The mouse cannot
    report the stage back, so the readout shows what the app sets, not what the
    mouse's own DPI button last did.
    """
    caps = get_device_caps()
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("Sensitivity"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    card.get_style_context().add_class("card")
    page.pack_start(card, True, True, 0)

    # -- DPI presets --------------------------------------------------------
    dpi_title = Gtk.Label(label=_("DPI PRESETS"))
    dpi_title.get_style_context().add_class("card-title")
    dpi_title.set_halign(Gtk.Align.START)
    card.pack_start(dpi_title, False, False, 0)

    presets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.pack_start(presets_box, False, False, 0)

    dpi_min, dpi_max, dpi_step = caps.dpi_min, caps.dpi_max, caps.dpi_step
    max_presets = max(1, caps.dpi_max_presets)
    default_values = [v for v in caps.dpi_default if dpi_min <= v <= dpi_max] or [dpi_min, min(dpi_max, dpi_min * 2)]

    app_state["dpi_scales"] = []
    app_state["dpi_labels"] = []
    app_state["dpi_radios"] = []
    app_state["dpi_values"] = list(default_values)
    if not isinstance(app_state.get("dpi_active_index"), int):
        app_state["dpi_active_index"] = 0

    assets_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets")
    trash_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        os.path.join(assets_dir, "trash.svg"), 16, 16, True
    )

    add_btn = Gtk.Button(label="+ " + _("Add DPI"))
    add_btn.set_halign(Gtk.Align.START)
    card.pack_start(add_btn, False, False, 0)

    active_label = Gtk.Label()
    active_label.get_style_context().add_class("active-preset")
    active_label.set_halign(Gtk.Align.START)
    active_label.set_tooltip_text(
        _("What the app will set. The mouse's own DPI button isn't reported "
          "back, so this can be out of date.")
    )
    card.pack_start(active_label, False, False, 0)

    def _update_active_label():
        vals = app_state["dpi_values"]
        idx = app_state.get("dpi_active_index", 0)
        if vals and 0 <= idx < len(vals):
            active_label.set_text(
                _("App preset: {n} — {value} DPI").format(n=idx + 1, value=vals[idx])
            )
        else:
            active_label.set_text("")

    def on_dpi_active_toggled(btn, idx):
        if not btn.get_active() or app_state.get("_dpi_selecting"):
            return
        _set_active_index(idx)

    def _set_active_index(idx):
        """Make preset *idx* the active one.

        Updates the UI, the Hyprland sync, and -- with auto-apply on -- the
        mouse itself; without auto-apply the device is left alone until Apply
        (the app's no-writes-without-consent rule).
        """
        vals = app_state["dpi_values"]
        if not vals:
            return
        idx = max(0, min(int(idx), len(vals) - 1))
        app_state["dpi_active_index"] = idx
        app_state["_dpi_selecting"] = True
        for i, rb in enumerate(app_state["dpi_radios"]):
            rb.set_active(i == idx)
        app_state["_dpi_selecting"] = False
        _update_active_label()
        if app_state["settings"].get("auto_apply"):
            _sync_hyprland_to_active_dpi()
            _auto_apply_dpi()

    def _auto_apply_dpi():
        if app_state.get("_loading_profile") or not app_state["settings"].get("auto_apply"):
            return
        plan = device_core.ApplyPlan(_("Sensitivity"))
        device_core.add_sensitivity(
            plan,
            app_state["dpi_values"],
            app_state.get("dpi_active_index", 0),
            save=not app_state.get("no_save"),
        )
        _debounce_plan("dpi", plan)

    def on_add_dpi(btn):
        if len(app_state["dpi_values"]) >= max_presets:
            return
        app_state["dpi_values"].append(default_values[0])
        rebuild_dpi_ui()
        _auto_apply_dpi()

    add_btn.connect("clicked", on_add_dpi)

    def on_delete_dpi(btn, idx):
        if len(app_state["dpi_values"]) <= 1:
            return
        del app_state["dpi_values"][idx]
        active = app_state.get("dpi_active_index", 0)
        if idx < active:
            active -= 1                  # every preset after the hole moved down
        elif idx == active:
            active = min(active, len(app_state["dpi_values"]) - 1)
        app_state["dpi_active_index"] = max(0, active)
        rebuild_dpi_ui()
        _auto_apply_dpi()

    def rebuild_dpi_ui():
        for child in presets_box.get_children():
            presets_box.remove(child)
        app_state["dpi_scales"] = []
        app_state["dpi_labels"] = []
        app_state["dpi_radios"] = []

        active = app_state.get("dpi_active_index", 0)
        if not (0 <= active < len(app_state["dpi_values"])):
            active = app_state["dpi_active_index"] = 0

        group = None
        # The guard is up for the whole build, so the programmatic set_active()
        # below can never be mistaken for the user picking a preset.
        app_state["_dpi_selecting"] = True

        for i, val in enumerate(app_state["dpi_values"]):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_bottom(8)

            trash_btn = Gtk.Button()
            trash_btn.set_image(Gtk.Image.new_from_pixbuf(trash_pixbuf))
            trash_btn.set_relief(Gtk.ReliefStyle.NONE)
            trash_btn.get_style_context().add_class("dpi-delete-btn")
            trash_btn.connect("clicked", on_delete_dpi, i)

            active_radio = Gtk.RadioButton() if group is None else Gtk.RadioButton(group=group)
            if group is None:
                group = active_radio
            active_radio.set_active(i == active)
            active_radio.set_tooltip_text(
                _("Select this preset as the active DPI (sent to the mouse on Apply)")
            )
            active_radio.connect("toggled", on_dpi_active_toggled, i)
            row.pack_start(active_radio, False, False, 0)
            app_state["dpi_radios"].append(active_radio)

            lbl = Gtk.Label(label=f"DPI {i + 1}")
            lbl.set_size_request(80, -1)
            lbl.set_halign(Gtk.Align.START)
            row.pack_start(lbl, False, False, 0)

            spin_btn = Gtk.SpinButton.new_with_range(dpi_min, dpi_max, dpi_step)
            spin_btn.set_digits(0)
            spin_btn.set_numeric(True)
            spin_btn.set_max_length(5)
            spin_btn.set_value(val)
            spin_btn.set_size_request(80, -1)
            spin_btn.set_halign(Gtk.Align.START)
            spin_btn.get_style_context().add_class("value-display")
            row.pack_start(spin_btn, False, False, 0)
            app_state["dpi_labels"].append(spin_btn)

            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, dpi_min, dpi_max, dpi_step)
            scale.set_draw_value(False)
            scale.set_digits(0)
            scale.set_value(val)
            scale.set_hexpand(True)

            _updating = [False]

            def _snap(value, step=dpi_step):
                if step <= 0:
                    return int(value)
                return int(round(int(value) / step) * step)

            def on_dpi_changed(sc, idx=i, sb=spin_btn, guard=_updating):
                if guard[0]:
                    return
                v = _snap(sc.get_value())
                guard[0] = True
                if int(sc.get_value()) != v:
                    sc.set_value(v)
                sb.set_value(v)
                guard[0] = False
                app_state["dpi_values"][idx] = v
                _auto_apply_dpi()
                _update_active_label()

            def on_spin_changed(sb, idx=i, sc=scale, guard=_updating):
                if guard[0]:
                    return
                v = _snap(sb.get_value())
                guard[0] = True
                if int(sb.get_value()) != v:
                    sb.set_value(v)
                sc.set_value(v)
                guard[0] = False
                app_state["dpi_values"][idx] = v
                _auto_apply_dpi()
                _update_active_label()

            scale.connect("value-changed", on_dpi_changed)
            spin_btn.connect("value-changed", on_spin_changed)
            row.pack_start(scale, True, True, 0)
            app_state["dpi_scales"].append(scale)

            row.pack_end(trash_btn, False, False, 0)
            presets_box.pack_start(row, False, False, 0)

        app_state["_dpi_selecting"] = False
        presets_box.show_all()
        add_btn.set_visible(len(app_state["dpi_values"]) < max_presets)
        _update_active_label()

    app_state["_rebuild_dpi_ui"] = rebuild_dpi_ui
    rebuild_dpi_ui()

    # -- Polling rate -------------------------------------------------------
    polling_title = Gtk.Label(label=_("POLLING RATE"))
    polling_title.get_style_context().add_class("card-title")
    polling_title.set_halign(Gtk.Align.START)
    polling_title.set_margin_top(12)
    card.pack_start(polling_title, False, False, 0)

    rates = list(caps.polling_choices) or [125, 250, 500, 1000]
    app_state["polling_hz"] = caps.polling_default
    app_state["polling_radios"] = {}

    display = Gtk.Label()
    display.get_style_context().add_class("value-display")
    display.set_halign(Gtk.Align.START)
    app_state["polling_display"] = display

    def _update_polling_display(hz):
        display.set_text(_("{} Hz → {} ms").format(hz, f"{1000.0 / hz:.1f}"))

    _update_polling_display(caps.polling_default)

    group = None
    radio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

    for hz in rates:
        if group is None:
            rb = Gtk.RadioButton(label=_("{} Hz").format(hz))
            group = rb
            rb.set_active(hz == caps.polling_default)
        else:
            rb = Gtk.RadioButton(label=_("{} Hz").format(hz), group=group)
            rb.set_active(hz == caps.polling_default)

        app_state["polling_radios"][hz] = rb

        def on_polling_toggled(button, val=hz):
            if button.get_active():
                app_state["polling_hz"] = val
                _update_polling_display(val)
                if not app_state.get("_loading_profile") and app_state["settings"].get("auto_apply"):
                    _debounce_args("polling", ["--polling-rate", str(val)])

        rb.connect("toggled", on_polling_toggled)
        radio_box.pack_start(rb, False, False, 0)

    card.pack_start(radio_box, False, False, 0)
    card.pack_start(display, False, False, 0)

    # -- Apply / reset (one plan for both halves of the page) --------------
    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    btn_row.set_halign(Gtk.Align.START)
    btn_row.set_margin_top(12)

    apply_btn = Gtk.Button(label=_("APPLY"))
    apply_btn.get_style_context().add_class("apply-btn")

    def on_apply_sensitivity(btn):
        save_active_profile()
        plan = device_core.ApplyPlan(_("Sensitivity"))
        # The DPI presets go through the library (the CLI cannot select a preset);
        # polling stays on the CLI batch, in the same order as before.
        device_core.add_sensitivity(
            plan,
            app_state["dpi_values"],
            app_state.get("dpi_active_index", 0),
            save=not app_state.get("no_save"),
        )
        plan.add(["--polling-rate", str(app_state["polling_hz"])])
        _queue_plan(plan)
        _sync_hyprland_to_active_dpi()

    apply_btn.connect("clicked", on_apply_sensitivity)
    btn_row.pack_start(apply_btn, False, False, 0)

    reset_btn = Gtk.Button(label=_("RESET"))
    reset_btn.get_style_context().add_class("reset-btn")

    def on_reset_sensitivity(btn):
        app_state["dpi_values"] = list(default_values)
        app_state["dpi_active_index"] = 0
        app_state["polling_hz"] = caps.polling_default
        rebuild_dpi_ui()
        for hz, rb in app_state["polling_radios"].items():
            rb.set_active(hz == caps.polling_default)

    reset_btn.connect("clicked", on_reset_sensitivity)
    btn_row.pack_start(reset_btn, False, False, 0)

    card.pack_start(btn_row, False, False, 0)

    return page


def _debounce_plan(key, plan):
    """Debounced multi-command plan (e.g. a full lighting re-apply)."""
    return _init_command_queue().enqueue_debounced(key, plan)


def create_rgb_page():
    """Create the RGB Lighting page (capability-driven, embedded colour editor)."""
    caps = get_device_caps()
    lighting = caps.lighting
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("RGB Lighting"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
    page.pack_start(columns, True, True, 0)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    left.set_hexpand(True)
    columns.pack_start(left, True, True, 0)
    right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    right.set_size_request(320, -1)
    columns.pack_start(right, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.get_style_context().add_class("card")
    left.pack_start(card, True, True, 0)

    color_buttons = {}
    color_previews = {}
    app_state["color_buttons"] = color_buttons
    app_state["color_previews"] = color_previews

    # -- Seed zone state from profile defaults ------------------------------
    zones = {}
    for zone in lighting.zones:
        zones[zone.key] = color_to_hex(zone.default, "ff6600")
    app_state["zones"] = zones
    app_state["reactive_hex"] = "off"
    app_state["reactive_color_saved"] = app_state.get("reactive_color_saved", "00ff00")
    app_state["rainbow_enabled"] = False
    app_state["rainbow_value"] = lighting.rainbow_default
    app_state["default_lighting"] = lighting.default_lighting_default or (
        "rainbow" if lighting.has_default_lighting else ""
    )
    app_state["selected_effect"] = lighting.light_effect_default or "steady"

    def auto_apply_lighting():
        if app_state.get("_loading_profile") or not app_state["settings"].get("auto_apply"):
            return
        plan = device_core.build_lighting_plan(caps, _current_lighting_state())
        _debounce_plan("lighting", plan)

    # -- Embedded colour editor --------------------------------------------
    editor = widgets.ColorEditor(initial_hex="ff6600", on_changed=lambda h: None)
    editor_state = {
        "target": [None],   # ("zone", key) | ("reactive", None) | (None, None)
        "widgets": {},      # key -> ColorSwatch
    }

    def _apply_editor_color(hexv):
        kind, key = editor_state["target"]
        if kind == "zone":
            zones[key] = hexv
            sw = editor_state["widgets"].get(key)
            if sw is not None:
                sw.set_hex(hexv)
            strip = app_state.get("_rgb_strip")
            if strip is not None:
                strip.queue_draw()
        elif kind == "reactive":
            app_state["reactive_color_saved"] = hexv
            sw = editor_state["widgets"].get("reactive_hex")
            if sw is not None:
                sw.set_hex(hexv)
            if app_state.get("reactive_switch") is not None and app_state["reactive_switch"].get_active():
                app_state["reactive_hex"] = hexv
            else:
                app_state["reactive_hex"] = "off"
        else:
            return
        auto_apply_lighting()

    editor._on_changed = _apply_editor_color

    def select_target(kind, key, current_hex, label):
        editor_state["target"] = (kind, key)
        for k, sw in editor_state["widgets"].items():
            sw.set_selected(k == key)
        editor.set_color(current_hex)
        editor_title.set_text(label)

    def refresh_editor():
        """Re-sync the editor with the current target after a profile load."""
        kind, key = editor_state["target"]
        if kind == "zone":
            editor.set_color(zones.get(key, "ff6600"))
        elif kind == "reactive":
            editor.set_color(app_state.get("reactive_color_saved", "00ff00"))

    app_state["_color_editor_refresh"] = refresh_editor

    editor_title = Gtk.Label(label=_("Pick a colour to edit"))
    editor_title.get_style_context().add_class("card-title")
    editor_title.set_halign(Gtk.Align.START)
    right.pack_start(editor_title, False, False, 0)
    right.pack_start(editor, False, False, 0)

    # -- Vertical strip preview (below the picker) -------------------------
    def on_strip_press(widget, event):
        # Click a segment of the vertical strip preview to select that zone.
        h = widget.get_allocated_height()
        if not lighting.zones or h <= 0:
            return False
        idx = int(event.y / (h / len(lighting.zones)))
        idx = max(0, min(len(lighting.zones) - 1, idx))
        zone = lighting.zones[idx]
        select_target("zone", zone.key, zones.get(zone.key, "ff6600"), zone.label)
        return True

    if lighting.zones:
        strip_label = Gtk.Label(label=_("STRIP PREVIEW (top → bottom)"))
        strip_label.get_style_context().add_class("card-title")
        strip_label.set_halign(Gtk.Align.START)
        strip_label.set_margin_top(10)
        right.pack_start(strip_label, False, False, 0)

        strip = Gtk.DrawingArea()
        strip.set_size_request(90, 200)
        strip.set_halign(Gtk.Align.START)
        strip.get_style_context().add_class("color-preview")
        strip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        strip.connect("button-press-event", on_strip_press)

        def on_strip_draw(widget, cr):
            w = widget.get_allocated_width()
            h = widget.get_allocated_height()
            n = max(1, len(lighting.zones))
            seg = h / n
            for i, zone in enumerate(lighting.zones):
                r, g, b = _hex_to_rgb(zones.get(zone.key, "ff6600"))
                cr.set_source_rgb(r, g, b)
                cr.rectangle(0, i * seg, w, seg)
                cr.fill()
            cr.set_line_width(1)
            cr.set_source_rgba(0, 0, 0, 0.55)
            for i in range(1, n):
                cr.move_to(0, i * seg)
                cr.line_to(w, i * seg)
            cr.stroke()
            return False

        strip.connect("draw", on_strip_draw)
        right.pack_start(strip, False, False, 0)
        app_state["_rgb_strip"] = strip

    # -- Zone colors --------------------------------------------------------
    if lighting.zones:
        colors_title = Gtk.Label(label=_("COLORS"))
        colors_title.get_style_context().add_class("card-title")
        colors_title.set_halign(Gtk.Align.START)
        card.pack_start(colors_title, False, False, 0)

        color_hint = Gtk.Label(label=_(
            "Colors look washed out or pink? Set the Dim timer to 0 on the "
            "Power page before comparing."))
        color_hint.get_style_context().add_class("setting-desc")
        color_hint.set_halign(Gtk.Align.START)
        color_hint.set_line_wrap(True)
        card.pack_start(color_hint, False, False, 0)

        def make_zone_row(zone):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(4)

            lbl = Gtk.Label(label=zone.label)
            lbl.set_size_request(160, -1)
            lbl.set_halign(Gtk.Align.START)
            row.pack_start(lbl, False, False, 0)

            swatch = widgets.ColorSwatch(zones.get(zone.key, "ff6600"), width=64)
            swatch.connect("clicked", lambda _b, z=zone: select_target(
                "zone", z.key, zones.get(z.key, "ff6600"), z.label))
            row.pack_start(swatch, False, False, 0)
            color_buttons[zone.key] = swatch
            editor_state["widgets"][zone.key] = swatch
            return row

        for zone in lighting.zones:
            card.pack_start(make_zone_row(zone), False, False, 0)

    # -- Reactive (standalone) ---------------------------------------------
    reactive_title = Gtk.Label(label=_("REACTIVE"))
    reactive_title.get_style_context().add_class("card-title")
    reactive_title.set_halign(Gtk.Align.START)
    reactive_title.set_margin_top(12)
    if lighting.has_reactive:
        card.pack_start(reactive_title, False, False, 0)

        reactive_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        reactive_label = Gtk.Label(label=_("Click flash"))
        reactive_label.set_size_request(160, -1)
        reactive_label.set_halign(Gtk.Align.START)
        reactive_row.pack_start(reactive_label, False, False, 0)

        reactive_swatch = widgets.ColorSwatch(app_state["reactive_color_saved"], width=64)

        def _select_reactive(_b=None):
            select_target("reactive", "reactive_hex",
                          app_state.get("reactive_color_saved", "00ff00"),
                          _("Click flash color"))

        reactive_swatch.connect("clicked", _select_reactive)
        reactive_row.pack_start(reactive_swatch, False, False, 0)
        color_buttons["reactive_hex"] = reactive_swatch
        editor_state["widgets"]["reactive_hex"] = reactive_swatch

        reactive_switch = Gtk.Switch()
        reactive_switch.set_active(False)
        reactive_switch.set_valign(Gtk.Align.START)
        reactive_row.pack_start(reactive_switch, False, False, 0)
        app_state["reactive_switch"] = reactive_switch

        def on_reactive_switch(switch, _param):
            if switch.get_active():
                app_state["reactive_hex"] = app_state.get(
                    "reactive_color_saved") or reactive_swatch.get_hex()
            else:
                app_state["reactive_hex"] = "off"
            app_state["_reactive_on"] = switch.get_active()
            auto_apply_lighting()

        reactive_switch.connect("notify::active", on_reactive_switch)
        app_state["_reactive_swatch"] = reactive_swatch
        app_state["_reactive_on"] = False

        card.pack_start(reactive_row, False, False, 0)
        react_hint = Gtk.Label(label=_("Click flash happens only when this is on."))
        react_hint.get_style_context().add_class("setting-desc")
        react_hint.set_halign(Gtk.Align.START)
        card.pack_start(react_hint, False, False, 0)

    # -- Wake lighting (default lighting) ----------------------------------
    if lighting.has_default_lighting:
        wake_title = Gtk.Label(label=_("ON WAKE"))
        wake_title.get_style_context().add_class("card-title")
        wake_title.set_halign(Gtk.Align.START)
        wake_title.set_margin_top(12)
        card.pack_start(wake_title, False, False, 0)

        dl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dl_label = Gtk.Label(label=_("Wake lighting"))
        dl_label.set_size_request(160, -1)
        dl_label.set_halign(Gtk.Align.START)
        dl_row.pack_start(dl_label, False, False, 0)
        dl_combo = Gtk.ComboBoxText()
        dl_options = list(lighting.default_lighting_choices) or [
            "off", "reactive", "rainbow", "reactive-rainbow"
        ]
        for opt in dl_options:
            dl_combo.append_text(opt)
        if app_state["default_lighting"] in dl_options:
            dl_combo.set_active(dl_options.index(app_state["default_lighting"]))
        elif "rainbow" in dl_options:
            dl_combo.set_active(dl_options.index("rainbow"))
        dl_row.pack_start(dl_combo, False, False, 0)
        app_state["default_lighting_combo"] = dl_combo

        def on_dl_changed(combo):
            app_state["default_lighting"] = combo.get_active_text()
            auto_apply_lighting()

        dl_combo.connect("changed", on_dl_changed)
        card.pack_start(dl_row, False, False, 0)
        wake_hint = Gtk.Label(label=_("Applied when the mouse wakes; it does not change steady colors now."))
        wake_hint.get_style_context().add_class("setting-desc")
        wake_hint.set_halign(Gtk.Align.START)
        card.pack_start(wake_hint, False, False, 0)

    # -- Light effect (Rival 3 class) --------------------------------------
    if lighting.has_light_effect:
        effect_title = Gtk.Label(label=_("EFFECT"))
        effect_title.get_style_context().add_class("card-title")
        effect_title.set_halign(Gtk.Align.START)
        effect_title.set_margin_top(12)
        card.pack_start(effect_title, False, False, 0)

        effect_labels = {
            "steady": _("Steady"),
            "breath": _("Breath"),
            "breath-slow": _("Breath (Slow)"),
            "breath-fast": _("Breath (Fast)"),
            "rainbow-shift": _("Rainbow Shift"),
            "rainbow-breath": _("Rainbow Breath"),
            "disco": _("Disco"),
        }
        app_state["effect_radios"] = {}
        eff_group = None
        eff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        choices = lighting.light_effect_choices or list(effect_labels)
        for value in choices:
            name = effect_labels.get(value, value)
            if eff_group is None:
                rb = Gtk.RadioButton(label=name)
                eff_group = rb
            else:
                rb = Gtk.RadioButton(label=name, group=eff_group)
            rb.set_active(value == app_state["selected_effect"])
            app_state["effect_radios"][value] = rb

            def on_effect_toggled(button, val=value):
                if button.get_active():
                    app_state["selected_effect"] = val
                    auto_apply_lighting()

            rb.connect("toggled", on_effect_toggled)
            eff_box.pack_start(rb, False, False, 0)
        card.pack_start(eff_box, False, False, 0)

    # -- Rainbow (always sent LAST in the plan) ----------------------------
    if lighting.has_rainbow:
        rainbow_title = Gtk.Label(label=_("RAINBOW"))
        rainbow_title.get_style_context().add_class("card-title")
        rainbow_title.set_halign(Gtk.Align.START)
        rainbow_title.set_margin_top(12)
        card.pack_start(rainbow_title, False, False, 0)

        rainbow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        rainbow_check = Gtk.CheckButton(label=_("Rainbow effect"))
        rainbow_check.set_active(False)
        rainbow_row.pack_start(rainbow_check, False, False, 0)
        app_state["rainbow_check"] = rainbow_check

        if lighting.rainbow_kind == "choice" and lighting.rainbow_choices:
            rainbow_combo = Gtk.ComboBoxText()
            for opt in lighting.rainbow_choices:
                rainbow_combo.append_text(opt)
            default = lighting.rainbow_default or lighting.rainbow_choices[0]
            if default in lighting.rainbow_choices:
                rainbow_combo.set_active(lighting.rainbow_choices.index(default))
            rainbow_row.pack_start(rainbow_combo, False, False, 0)
            app_state["rainbow_combo"] = rainbow_combo

            def on_rainbow_choice(combo):
                app_state["rainbow_value"] = combo.get_active_text()
                auto_apply_lighting()

            rainbow_combo.connect("changed", on_rainbow_choice)

        def on_rainbow_toggled(button):
            app_state["rainbow_enabled"] = button.get_active()
            auto_apply_lighting()

        rainbow_check.connect("toggled", on_rainbow_toggled)
        card.pack_start(rainbow_row, False, False, 0)

    # -- Apply --------------------------------------------------------------
    apply_btn = Gtk.Button(label=_("APPLY"))
    apply_btn.get_style_context().add_class("apply-btn")
    apply_btn.set_halign(Gtk.Align.START)
    apply_btn.set_margin_top(8)

    def on_apply_rgb(btn):
        save_active_profile()
        plan = device_core.build_lighting_plan(get_device_caps(), _current_lighting_state())
        _queue_plan(plan)

    apply_btn.connect("clicked", on_apply_rgb)
    card.pack_start(apply_btn, False, False, 0)

    # Select the first zone (or reactive) by default.
    if lighting.zones:
        z0 = lighting.zones[0]
        select_target("zone", z0.key, zones.get(z0.key, "ff6600"), z0.label)
    elif lighting.has_reactive:
        _select_reactive()

    return page
def create_buttons_page():
    """Button Mapping page (3D wireframe of the mouse + assignment popover)."""
    import math as _math

    import mouse3d

    caps = get_device_caps()
    keyboard = caps.button_keyboard
    multimedia = caps.button_multimedia

    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("Button Mapping"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    # -- State --------------------------------------------------------------
    app_state.setdefault("button_mapping", {})
    defaults = {b.key: b.default for b in caps.buttons}
    for key, value in defaults.items():
        app_state["button_mapping"].setdefault(key, value)
    app_state["button_defaults"] = defaults

    device_button_keys = set(caps.button_keys)
    mouse_targets = sorted(k for k in device_button_keys if k.startswith("button"))
    scroll_targets = [s for s in ("scrollup", "scrolldown") if s in device_button_keys]
    has_dpi_action = caps.raw_settings.get("buttons_mapping", {}).get("button_dpi_switch") is not None
    special_targets = (["dpi"] if has_dpi_action else []) + scroll_targets + ["disabled"]
    key_items = device_core.keyboard_keys() if keyboard else []
    mm_items = device_core.MULTIMEDIA_ACTIONS if multimedia else []

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.get_style_context().add_class("card")
    page.pack_start(card, True, True, 0)

    # (key, label, yaw, pitch, fill): fill < 1.0 keeps the wireframe from
    # filling the whole canvas so the fixed-size label chips stay readable.
    views = [
        ("3d", _("3D view"), -120.0, 42.0, 0.66),
        ("top", _("Top view"), 0.0, 90.0, 0.60),
        ("side", _("Left side"), -90.0, 10.0, 0.70),
    ]
    view_state = [views[0]]

    switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    switcher.set_halign(Gtk.Align.START)
    view_buttons = []
    for key, label, yaw, pitch, fill in views:
        vb = Gtk.Button(label=label)
        vb.get_style_context().add_class("nav-btn")
        if key == view_state[0][0]:
            vb.get_style_context().add_class("nav-active")
        switcher.pack_start(vb, False, False, 0)
        view_buttons.append((vb, key, label, yaw, pitch, fill))
    card.pack_start(switcher, False, False, 0)

    drawing = Gtk.DrawingArea()
    drawing.set_hexpand(True)
    drawing.set_vexpand(True)
    drawing.set_size_request(-1, 420)
    drawing.get_style_context().add_class("color-preview")
    drawing.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    card.pack_start(drawing, True, True, 0)

    geom = {"view": None, "chips": {}}

    def _chip_text(key, label):
        assigned = app_state["button_mapping"].get(key, "")
        return "%s: %s" % (label, device_core.action_label(assigned))

    def on_draw(widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        _key, _label, yaw, pitch, fill = view_state[0]
        view = mouse3d.fit_view(w, h, margin=28, fill=fill,
                                view=mouse3d.View(yaw=_math.radians(yaw),
                                                  pitch=_math.radians(pitch)))
        visible = set(mouse3d.BUTTONS) & (device_button_keys | set(defaults))
        active = {k for k, v in app_state["button_mapping"].items()
                  if v not in (None, "", "disabled")}
        info = mouse3d.render(cr, w, h, view, visible=visible, active=active,
                              hover=geom.get("hover"), label_fn=_chip_text)
        geom.update(info)
        return False

    drawing.connect("draw", on_draw)

    # -- Popover ------------------------------------------------------------
    popover = Gtk.Popover.new(drawing)
    popover.set_modal(True)
    popover_scroll = Gtk.ScrolledWindow()
    popover_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    popover_scroll.set_min_content_height(120)
    popover_scroll.set_max_content_height(420)
    popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    popover_box.set_margin_start(8)
    popover_box.set_margin_end(8)
    popover_box.set_margin_top(8)
    popover_box.set_margin_bottom(8)
    popover_scroll.add(popover_box)
    popover.add(popover_scroll)

    current_button = [None]

    def _section_label(text):
        lbl = Gtk.Label(label=text)
        lbl.get_style_context().add_class("card-title")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_margin_top(6)
        return lbl

    def _apply(button_key, value):
        if not button_key:
            return
        app_state["button_mapping"][button_key] = value
        drawing.queue_draw()
        popover.popdown()
        if app_state["settings"].get("auto_apply"):
            _debounce_args("buttons", ["--buttons", build_buttons_arg(app_state["button_mapping"], caps)])

    def _add_action_button(text, value, button_key):
        btn = Gtk.Button(label=text)
        btn.set_halign(Gtk.Align.FILL)
        btn.connect("clicked", lambda _w: _apply(button_key, value))
        popover_box.pack_start(btn, False, False, 0)

    def _build_popover(button_key):
        for child in popover_box.get_children():
            popover_box.remove(child)
        header = Gtk.Label(label=_("%s assignment") % button_key)
        header.get_style_context().add_class("setting-label")
        header.set_halign(Gtk.Align.START)
        popover_box.pack_start(header, False, False, 0)

        popover_box.pack_start(_section_label(_("MOUSE")), False, False, 0)
        for target in mouse_targets:
            _add_action_button(device_core.action_label(target), target, button_key)
        for target in special_targets:
            _add_action_button(device_core.action_label(target), target, button_key)

        if mm_items:
            popover_box.pack_start(_section_label(_("MULTIMEDIA")), False, False, 0)
            for value, label in mm_items:
                _add_action_button(label, value, button_key)

        if key_items:
            popover_box.pack_start(_section_label(_("KEYBOARD")), False, False, 0)
            key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            key_combo = Gtk.ComboBoxText()
            for key in key_items:
                key_combo.append_text(key)
            key_combo.set_active(0)
            key_row.pack_start(key_combo, True, True, 0)
            assign_btn = Gtk.Button(label=_("Assign"))
            key_row.pack_start(assign_btn, False, False, 0)
            popover_box.pack_start(key_row, False, False, 0)
            assign_btn.connect("clicked", lambda _w: _apply(button_key, key_combo.get_active_text()))
            layout_hint = Gtk.Label(label=_("Layout: qwerty"))
            layout_hint.get_style_context().add_class("setting-desc")
            layout_hint.set_halign(Gtk.Align.START)
            popover_box.pack_start(layout_hint, False, False, 0)

        popover_box.show_all()

    def _hit_test(x, y):
        view = geom["view"]
        if view is None:
            return None
        # Geometry first (top-most / smallest area wins).
        best = None
        best_area = None
        for key, (label, poly) in mouse3d.BUTTONS.items():
            if key not in device_button_keys and key not in defaults:
                continue
            pts = view.project_many(poly)
            if mouse3d.point_in_polygon(x, y, pts):
                area = _poly_area(pts)
                if best is None or area < best_area:
                    best, best_area = key, area
        if best is not None:
            return best
        for key, (cx, cy, cw, ch, _label) in geom["chips"].items():
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                return key
        return None

    def on_button_press(widget, event):
        key = _hit_test(event.x, event.y)
        if not key:
            return False
        current_button[0] = key
        view = geom["view"]
        poly = mouse3d.BUTTONS[key][1]
        pts = view.project_many(poly)
        rect = Gdk.Rectangle()
        rect.x = int(min(p[0] for p in pts))
        rect.y = int(min(p[1] for p in pts))
        rect.width = max(1, int(max(p[0] for p in pts) - rect.x))
        rect.height = max(1, int(max(p[1] for p in pts) - rect.y))
        popover.set_pointing_to(rect)
        popover.set_position(Gtk.PositionType.RIGHT)
        _build_popover(key)
        popover.popup()
        return True

    drawing.connect("button-press-event", on_button_press)

    for vb, key, label, yaw, pitch, fill in view_buttons:
        def _switch(_b, k=key, lbl=label, y=yaw, p=pitch, f=fill):
            view_state[0] = (k, lbl, y, p, f)
            for other, _k, _l, _y, _p, _f in view_buttons:
                other.get_style_context().remove_class("nav-active")
            _b.get_style_context().add_class("nav-active")
            drawing.queue_draw()
        vb.connect("clicked", _switch)

    # -- Apply / Reset ------------------------------------------------------
    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    btn_row.set_halign(Gtk.Align.START)
    btn_row.set_margin_top(8)

    apply_btn = Gtk.Button(label=_("APPLY"))
    apply_btn.get_style_context().add_class("apply-btn")

    def on_apply_buttons(btn):
        save_active_profile()
        plan = device_core.ApplyPlan("Buttons")
        plan.add(["--buttons", build_buttons_arg(app_state["button_mapping"], get_device_caps())])
        _queue_plan(plan)

    apply_btn.connect("clicked", on_apply_buttons)
    btn_row.pack_start(apply_btn, False, False, 0)

    reset_btn = Gtk.Button(label=_("RESET"))
    reset_btn.get_style_context().add_class("reset-btn")

    def on_reset_buttons(btn):
        app_state["button_mapping"].clear()
        app_state["button_mapping"].update(app_state["button_defaults"])
        drawing.queue_draw()

    reset_btn.connect("clicked", on_reset_buttons)
    btn_row.pack_start(reset_btn, False, False, 0)

    card.pack_start(btn_row, False, False, 0)

    return page


def _poly_area(poly):
    area = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0
def create_devices_page():
    """Connected Devices page: only the plugged, supported devices (D2)."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("Connected Devices"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    card.get_style_context().add_class("card")
    page.pack_start(card, True, True, 0)

    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    card.pack_start(info_box, False, False, 0)

    footnote = Gtk.Label()
    footnote.set_markup(
        "<small>" + _("Only devices currently plugged in are listed. "
                       "See the rivalcfg device list for every supported model.") + "</small>"
    )
    footnote.get_style_context().add_class("setting-desc")
    footnote.set_halign(Gtk.Align.START)
    footnote.set_line_wrap(True)
    card.pack_start(footnote, False, False, 0)

    def refresh():
        DEVICE_MANAGER.invalidate()
        for child in info_box.get_children():
            info_box.remove(child)
        devices = DEVICE_MANAGER.list_devices()
        if not devices:
            empty = Gtk.Label(label=_("No supported mouse plugged in."))
            empty.get_style_context().add_class("setting-label")
            empty.set_halign(Gtk.Align.START)
            info_box.pack_start(empty, False, False, 0)
            info_box.show_all()
            return
        for dev in devices:
            caps = DEVICE_MANAGER.get_caps(dev, refresh=True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_bottom(8)
            name_lbl = Gtk.Label(label="%s" % (dev.name or caps.name))
            name_lbl.get_style_context().add_class("setting-label")
            name_lbl.set_halign(Gtk.Align.START)
            box.pack_start(name_lbl, False, False, 0)
            details = "%s" % dev.vid_pid
            if dev.endpoint:
                details += " · endpoint %d" % dev.endpoint
            if caps.has_battery:
                details += " · " + _("battery supported")
            det_lbl = Gtk.Label(label=details)
            det_lbl.get_style_context().add_class("setting-desc")
            det_lbl.set_halign(Gtk.Align.START)
            box.pack_start(det_lbl, False, False, 0)
            if caps.has_firmware:
                fw_btn = Gtk.Button(label=_("Read firmware version"))
                fw_btn.set_halign(Gtk.Align.START)

                def on_fw(_b, d=dev):
                    plan = device_core.ApplyPlan("Firmware version")
                    plan.add(["--firmware-version"])
                    plan.on_done = lambda ok, out: GLib.idle_add(
                        set_status,
                        "ok" if ok else "error",
                        ("✓ " + out) if ok else ("✗ " + _("Could not read firmware")),
                    )
                    _queue_plan(plan)

                fw_btn.connect("clicked", on_fw)
                box.pack_start(fw_btn, False, False, 0)
            info_box.pack_start(box, False, False, 0)
        info_box.show_all()

    app_state["_refresh_devices"] = refresh

    refresh_btn = Gtk.Button(label=_("REFRESH"))
    refresh_btn.get_style_context().add_class("apply-btn")
    refresh_btn.set_halign(Gtk.Align.START)
    refresh_btn.set_margin_top(8)
    refresh_btn.connect("clicked", lambda _b: refresh())
    card.pack_start(refresh_btn, False, False, 0)

    refresh()
    return page


def create_power_page():
    """Battery, sleep timer, dim timer (+ brightness where supported)."""
    caps = get_device_caps()
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("Power"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.get_style_context().add_class("card")
    page.pack_start(card, True, True, 0)

    # -- Battery ------------------------------------------------------------
    if caps.has_battery:
        batt_title = Gtk.Label(label=_("BATTERY"))
        batt_title.get_style_context().add_class("card-title")
        batt_title.set_halign(Gtk.Align.START)
        card.pack_start(batt_title, False, False, 0)

        batt_label = Gtk.Label(label=_("Unknown"))
        batt_label.get_style_context().add_class("value-display")
        batt_label.set_halign(Gtk.Align.START)
        card.pack_start(batt_label, False, False, 0)
        charge_label = Gtk.Label(label="")
        charge_label.get_style_context().add_class("setting-desc")
        charge_label.set_halign(Gtk.Align.START)
        card.pack_start(charge_label, False, False, 0)

        def read_battery():
            plan = device_core.ApplyPlan("Battery level")
            plan.add(["--battery-level"])
            plan.on_done = lambda ok, out: GLib.idle_add(_show_battery, ok, out)
            _queue_plan(plan)

        def _show_battery(ok, out):
            if not ok or not out:
                batt_label.set_text(_("Unavailable"))
                charge_label.set_text(_("Is the mouse turned on?"))
                return
            batt_label.set_text(out)
            charge_label.set_text("")

        batt_status = Gtk.Label(label="")
        batt_status.get_style_context().add_class("setting-desc")
        batt_status.set_halign(Gtk.Align.START)
        card.pack_start(batt_status, False, False, 0)

        def _tick_battery():
            if not app_state.get("window") or not app_state["window"].get_visible():
                return True
            read_battery()
            return True

        def _start_polling():
            # Auto-poll the battery: once now, then every 60 s (the mouse is
            # wireless, so a read costs a command but no user action).
            read_battery()
            batt_status.set_text(_("Auto-refreshing every 60 s"))
            GLib.timeout_add_seconds(60, _tick_battery)

        GLib.idle_add(_start_polling)
    else:
        no_batt = Gtk.Label(label=_("This device does not report a battery level."))
        no_batt.get_style_context().add_class("setting-desc")
        no_batt.set_halign(Gtk.Align.START)
        card.pack_start(no_batt, False, False, 0)

    # -- Timers -------------------------------------------------------------
    def add_timer_rows(setting_key, title_text, desc_text, rng, default):
        section = Gtk.Label(label=title_text)
        section.get_style_context().add_class("card-title")
        section.set_halign(Gtk.Align.START)
        section.set_margin_top(12)
        card.pack_start(section, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        spin = Gtk.SpinButton()
        spin.set_range(rng[0], rng[1])
        spin.set_increments(rng[2], max(rng[2] * 5, 1))
        spin.set_digits(0)
        spin.set_numeric(True)
        spin.set_value(default)
        spin.set_size_request(90, -1)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        scale.set_range(rng[0], rng[1])
        scale.set_increments(rng[2], max(rng[2] * 5, 1))
        scale.set_draw_value(False)
        scale.set_value(default)
        scale.set_hexpand(True)
        guard = [False]
        app_state[setting_key] = int(default)

        def push(*_a):
            app_state[setting_key] = int(spin.get_value())
            if app_state.get("_loading_profile") or not app_state["settings"].get("auto_apply"):
                return
            _debounce_args(setting_key, ["--" + setting_key.replace("_", "-"), str(int(spin.get_value()))])

        def on_scale(sc, sb=spin, g=guard):
            if g[0]:
                return
            g[0] = True
            sb.set_value(int(sc.get_value()))
            g[0] = False
            push()

        def on_spin(sb, sc=scale, g=guard):
            if g[0]:
                return
            g[0] = True
            sc.set_value(int(sb.get_value()))
            g[0] = False
            push()

        scale.connect("value-changed", on_scale)
        spin.connect("value-changed", on_spin)
        row.pack_start(spin, False, False, 0)
        row.pack_start(scale, True, True, 0)
        card.pack_start(row, False, False, 0)
        hint = Gtk.Label(label=desc_text)
        hint.get_style_context().add_class("setting-desc")
        hint.set_halign(Gtk.Align.START)
        card.pack_start(hint, False, False, 0)
        return spin

    if caps.sleep_timer:
        add_timer_rows("sleep_timer", _("SLEEP TIMER"),
                       _("Idle time before the mouse sleeps (minutes, 0 = disable)."),
                       caps.sleep_timer, 5)
    if caps.dim_timer:
        add_timer_rows("dim_timer", _("DIM TIMER"),
                       _("Idle time before LEDs dim (seconds, 0 = disable). "
                         "Set 0 while comparing colors."),
                       caps.dim_timer, 30)

    # -- Brightness ---------------------------------------------------------
    if caps.lighting.has_led_brightness:
        bright_title = Gtk.Label(label=_("LED BRIGHTNESS"))
        bright_title.get_style_context().add_class("card-title")
        bright_title.set_halign(Gtk.Align.START)
        bright_title.set_margin_top(12)
        card.pack_start(bright_title, False, False, 0)
        rng = caps.lighting.led_brightness_range
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        scale.set_range(rng[0], rng[1])
        scale.set_increments(rng[2], max(rng[2] * 5, 1))
        scale.set_draw_value(True)
        scale.set_value(app_state.get("led_brightness", rng[1]))
        scale.set_hexpand(True)

        def on_brightness(sc):
            app_state["led_brightness"] = int(sc.get_value())
            if app_state.get("_loading_profile") or not app_state["settings"].get("auto_apply"):
                return
            _debounce_args("led_brightness", ["--led-brightness", str(int(sc.get_value()))])

        scale.connect("value-changed", on_brightness)
        row.pack_start(scale, True, True, 0)
        card.pack_start(row, False, False, 0)

    no_writes = Gtk.Label(label=_("Values apply through the shared command queue."))
    no_writes.get_style_context().add_class("setting-desc")
    no_writes.set_halign(Gtk.Align.START)
    no_writes.set_margin_top(12)
    card.pack_start(no_writes, False, False, 0)

    # Apply button for timers (explicit apply path).
    apply_btn = Gtk.Button(label=_("APPLY"))
    apply_btn.get_style_context().add_class("apply-btn")
    apply_btn.set_halign(Gtk.Align.START)

    def on_apply_power(_b):
        save_active_profile()
        plan = device_core.ApplyPlan("Power")
        if caps.sleep_timer and app_state.get("sleep_timer") is not None:
            plan.add(["--sleep-timer", str(int(app_state["sleep_timer"]))])
        if caps.dim_timer and app_state.get("dim_timer") is not None:
            plan.add(["--dim-timer", str(int(app_state["dim_timer"]))])
        if caps.lighting.has_led_brightness and app_state.get("led_brightness") is not None:
            plan.add(["--led-brightness", str(int(app_state["led_brightness"]))])
        _queue_plan(plan)

    apply_btn.connect("clicked", on_apply_power)
    card.pack_start(apply_btn, False, False, 0)

    return page


def create_about_page():
    """Create About page."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("About"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.get_style_context().add_class("card")
    page.pack_start(card, True, True, 0)

    text = (
        _("Linux GUI configuration tool for SteelSeries mice.") + "\n" +
        _("Built on top of the rivalcfg library.") #+ "\n\n" +
#        _("Requirements:") + "\n" +
#        _("  pip install rivalcfg") + "\n" +
#        _("  pacman -S python-gobject python-cairo")
    )
    label = Gtk.Label(label=text)
    label.set_line_wrap(True)
    label.set_halign(Gtk.Align.START)
    label.set_valign(Gtk.Align.START)
    card.pack_start(label, False, False, 0)

    github_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    github_box.set_halign(Gtk.Align.START)

    github_prefix = Gtk.Label(label=_("GitHub: "))
    github_prefix.set_halign(Gtk.Align.START)
    github_box.pack_start(github_prefix, False, False, 0)

    link_btn = Gtk.LinkButton(
        uri="https://github.com/MrGodzilla38/rivalcfg-gui",
        label=_("github.com/MrGodzilla38/rivalcfg-gui")
    )
    link_btn.set_halign(Gtk.Align.START)
    link_btn.set_relief(Gtk.ReliefStyle.NONE)
    github_box.pack_start(link_btn, False, False, 0)

    card.pack_start(github_box, False, False, 0)

    return page


def _combo_select(combo, value):
    """Select *value* in a Gtk.ComboBoxText if present."""
    if combo is None or value is None:
        return
    model = combo.get_model()
    for i, row in enumerate(model):
        if row[0] == value:
            combo.set_active(i)
            return


def apply_profile_to_ui(profile):
    """Load a (migrated) profile into the UI. Never writes to the device."""
    if not profile:
        return
    profile = migrate_profile(profile) or {}
    caps = get_device_caps()
    app_state["_loading_profile"] = True
    try:
        dpi = profile.get("dpi_values")
        if dpi:
            vals = [int(v) for v in dpi]
            app_state["dpi_values"] = vals
            try:
                active = int(profile.get("dpi_active_index", 0))
            except (TypeError, ValueError):
                active = 0
            app_state["dpi_active_index"] = max(0, min(active, len(vals) - 1))
            if "_rebuild_dpi_ui" in app_state:
                app_state["_rebuild_dpi_ui"]()

        if "polling_hz" in profile and "polling_radios" in app_state:
            for hz, rb in app_state["polling_radios"].items():
                rb.set_active(hz == profile["polling_hz"])

        zones = profile.get("zones", {})
        app_state.setdefault("zones", {})
        for key, val in zones.items():
            app_state["zones"][key] = color_to_hex(val, app_state["zones"].get(key, "ff6600"))
            btn = app_state.get("color_buttons", {}).get(key)
            if btn is not None:
                btn.set_hex(app_state["zones"][key])

        reactive = profile.get("reactive", "off")
        reactive_on = reactive not in ("off", "disable", "", None)
        app_state["reactive_hex"] = color_to_hex(reactive, "00ff00") if reactive_on else "off"
        if reactive_on:
            app_state["reactive_color_saved"] = app_state["reactive_hex"]
        rbtn = app_state.get("color_buttons", {}).get("reactive_hex")
        if rbtn is not None:
            rbtn.set_hex(app_state.get("reactive_color_saved", "00ff00"))
        rswitch = app_state.get("reactive_switch")
        if rswitch is not None:
            rswitch.set_active(reactive_on)

        if "rainbow" in profile:
            app_state["rainbow_enabled"] = bool(profile["rainbow"])
            if "rainbow_check" in app_state:
                app_state["rainbow_check"].set_active(bool(profile["rainbow"]))
        if profile.get("rainbow_value"):
            app_state["rainbow_value"] = profile["rainbow_value"]
            _combo_select(app_state.get("rainbow_combo"), profile["rainbow_value"])

        if profile.get("default_lighting"):
            app_state["default_lighting"] = profile["default_lighting"]
            _combo_select(app_state.get("default_lighting_combo"), profile["default_lighting"])

        if profile.get("light_effect"):
            app_state["selected_effect"] = profile["light_effect"]
            radios = app_state.get("effect_radios", {})
            if profile["light_effect"] in radios:
                radios[profile["light_effect"]].set_active(True)

        mapping = profile.get("button_mapping")
        if isinstance(mapping, dict):
            app_state.setdefault("button_mapping", {})
            valid = set(caps.button_keys)
            for key, value in mapping.items():
                if not valid or key in valid:
                    app_state["button_mapping"][key] = value
            if "redraw_buttons" in app_state:
                app_state["redraw_buttons"]()

        if profile.get("led_brightness") is not None:
            app_state["led_brightness"] = int(profile["led_brightness"])

        strip = app_state.get("_rgb_strip")
        if strip is not None:
            strip.queue_draw()
        editor_refresh = app_state.get("_color_editor_refresh")
        if editor_refresh is not None:
            editor_refresh()
    finally:
        app_state["_loading_profile"] = False


def apply_all_to_device():
    """Apply the whole current profile to the device as ONE ordered plan.

    Canonical order lives in ``device_core.build_full_plan`` (zone colors ->
    reactive -> default lighting -> rainbow last). Fixes W1/W2/W3/P1.
    """
    save_active_profile()
    caps = get_device_caps()
    plan = device_core.build_full_plan(caps, current_apply_state())
    _queue_plan(plan)
    _sync_hyprland_to_active_dpi()


def create_settings_page():
    """Create Settings page."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    page.set_margin_start(24)
    page.set_margin_end(24)

    title = Gtk.Label(label=_("Settings"))
    title.get_style_context().add_class("page-title")
    title.set_halign(Gtk.Align.START)
    page.pack_start(title, False, False, 0)

    # --- BEHAVIOR CARD ---
    behavior_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    behavior_card.get_style_context().add_class("card")
    page.pack_start(behavior_card, False, False, 0)

    behavior_title = Gtk.Label(label=_("BEHAVIOR"))
    behavior_title.get_style_context().add_class("card-title")
    behavior_title.set_halign(Gtk.Align.START)
    behavior_card.pack_start(behavior_title, False, False, 0)

    # Startup Minimize
    sm_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    sm_row.get_style_context().add_class("setting-row")

    sm_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    sm_text.set_hexpand(True)
    sm_label = Gtk.Label(label=_("Startup Minimize"))
    sm_label.get_style_context().add_class("setting-label")
    sm_label.set_halign(Gtk.Align.START)
    sm_desc = Gtk.Label(label=_("Launch minimized to system tray"))
    sm_desc.get_style_context().add_class("setting-desc")
    sm_desc.set_halign(Gtk.Align.START)
    sm_text.pack_start(sm_label, False, False, 0)
    sm_text.pack_start(sm_desc, False, False, 0)
    sm_row.pack_start(sm_text, True, True, 0)

    sm_switch = Gtk.Switch()
    sm_switch.set_active(app_state["settings"]["startup_minimize"])
    sm_row.pack_start(sm_switch, False, False, 0)

    def on_startup_minimize(s, *a):
        val = sm_switch.get_active()
        app_state["settings"]["startup_minimize"] = val
        logging.info("Settings: startup_minimize = %s", val)
        save_settings()
    sm_switch.connect("notify::active", on_startup_minimize)
    behavior_card.pack_start(sm_row, False, False, 0)

    # Auto-Apply on Change
    aa_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    aa_row.get_style_context().add_class("setting-row")

    aa_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    aa_text.set_hexpand(True)
    aa_label = Gtk.Label(label=_("Auto-Apply on Change"))
    aa_label.get_style_context().add_class("setting-label")
    aa_label.set_halign(Gtk.Align.START)
    aa_desc = Gtk.Label(label=_("Apply settings immediately when changed"))
    aa_desc.get_style_context().add_class("setting-desc")
    aa_desc.set_halign(Gtk.Align.START)
    aa_text.pack_start(aa_label, False, False, 0)
    aa_text.pack_start(aa_desc, False, False, 0)
    aa_row.pack_start(aa_text, True, True, 0)

    aa_switch = Gtk.Switch()
    aa_switch.set_active(app_state["settings"]["auto_apply"])
    aa_row.pack_start(aa_switch, False, False, 0)

    def on_auto_apply(s, *a):
        val = aa_switch.get_active()
        app_state["settings"]["auto_apply"] = val
        logging.info("Settings: auto_apply = %s", val)
        save_settings()
    aa_switch.connect("notify::active", on_auto_apply)
    behavior_card.pack_start(aa_row, False, False, 0)

    # --- LANGUAGE CARD ---
    language_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    language_card.get_style_context().add_class("card")
    page.pack_start(language_card, False, False, 0)

    language_title = Gtk.Label(label=_("LANGUAGE"))
    language_title.get_style_context().add_class("card-title")
    language_title.set_halign(Gtk.Align.START)
    language_card.pack_start(language_title, False, False, 0)

    lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    lang_row.get_style_context().add_class("setting-row")

    lang_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    lang_text.set_hexpand(True)
    lang_label = Gtk.Label(label=_("Language"))
    lang_label.get_style_context().add_class("setting-label")
    lang_label.set_halign(Gtk.Align.START)
    lang_desc = Gtk.Label(label=_("Select application display language"))
    lang_desc.get_style_context().add_class("setting-desc")
    lang_desc.set_halign(Gtk.Align.START)
    lang_text.pack_start(lang_label, False, False, 0)
    lang_text.pack_start(lang_desc, False, False, 0)
    lang_row.pack_start(lang_text, True, True, 0)

    lang_combo = Gtk.ComboBoxText()
    languages = [
        ("en", "🇺🇲 English"),
        ("de", "🇩🇪 Deutsch"),
        ("es", "🇪🇸 Español"),
        ("fr", "🇫🇷 Français"),
        ("it", "🇮🇹 Italiano"),
        ("pl", "🇵🇱 Polski"),
        ("pt_BR", "🇧🇷 Português (Brasil)"),
        ("ru", "🇷🇺 Русский"),
        ("tr", "🇹🇷 Türkçe"),
        ("zh_CN", "🇨🇳 简体中文"),
    ]
    current_lang = app_state["settings"].get("language", "en")
    for code, name in languages:
        lang_combo.append_text(name)
        if code == current_lang:
            lang_combo.set_active(languages.index((code, name)))
    if lang_combo.get_active() == -1:
        lang_combo.set_active(0)
    lang_row.pack_start(lang_combo, False, False, 0)

    def on_lang_changed(combo):
        selected_idx = combo.get_active()
        lang_code = languages[selected_idx][0]
        app_state["settings"]["language"] = lang_code
        logging.info("Settings: language = %s", lang_code)
        save_settings()
        _set_language(lang_code)
        rebuild_ui()

    lang_combo.connect("changed", on_lang_changed)
    language_card.pack_start(lang_row, False, False, 0)

    # --- APPEARANCE CARD ---
    appearance_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    appearance_card.get_style_context().add_class("card")
    page.pack_start(appearance_card, False, False, 0)

    appearance_title = Gtk.Label(label=_("APPEARANCE"))
    appearance_title.get_style_context().add_class("card-title")
    appearance_title.set_halign(Gtk.Align.START)
    appearance_card.pack_start(appearance_title, False, False, 0)

    # Accent Color
    ac_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    ac_row.get_style_context().add_class("setting-row")

    ac_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    ac_text.set_hexpand(True)
    ac_label = Gtk.Label(label=_("Accent Color"))
    ac_label.get_style_context().add_class("setting-label")
    ac_label.set_halign(Gtk.Align.START)
    ac_desc = Gtk.Label(label=_("UI highlight and button color"))
    ac_desc.get_style_context().add_class("setting-desc")
    ac_desc.set_halign(Gtk.Align.START)
    ac_text.pack_start(ac_label, False, False, 0)
    ac_text.pack_start(ac_desc, False, False, 0)
    ac_row.pack_start(ac_text, True, True, 0)

    current_accent = app_state["settings"]["accent_color"]
    rgba = Gdk.RGBA()
    rgba.parse(current_accent)

    ac_color_btn = Gtk.ColorButton()
    ac_color_btn.set_rgba(rgba)
    ac_color_btn.set_size_request(50, 30)
    ac_row.pack_start(ac_color_btn, False, False, 0)

    ac_preview = Gtk.DrawingArea()
    ac_preview.set_size_request(60, 24)
    ac_preview.get_style_context().add_class("color-preview")
    ac_row.pack_start(ac_preview, False, False, 0)

    def on_accent_draw(widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = int(current_accent[1:3], 16) / 255.0
        g = int(current_accent[3:5], 16) / 255.0
        b = int(current_accent[5:7], 16) / 255.0
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        return False

    ac_preview.connect("draw", on_accent_draw)

    def on_accent_color_set(button):
        nonlocal current_accent
        col = button.get_rgba()
        current_accent = f"#{int(col.red * 255):02x}{int(col.green * 255):02x}{int(col.blue * 255):02x}"
        app_state["settings"]["accent_color"] = current_accent
        logging.info("Settings: accent_color = %s", current_accent)
        ac_preview.queue_draw()
        update_accent_color(current_accent)
        save_settings()

    ac_color_btn.connect("color-set", on_accent_color_set)
    appearance_card.pack_start(ac_row, False, False, 0)

    # Reset to Default button
    reset_accent_btn = Gtk.Button(label=_("Reset to Default"))
    reset_accent_btn.get_style_context().add_class("reset-btn")
    reset_accent_btn.set_halign(Gtk.Align.START)
    reset_accent_btn.set_margin_top(8)

    def on_reset_accent(btn):
        nonlocal current_accent
        current_accent = DEFAULT_SETTINGS["accent_color"]
        app_state["settings"]["accent_color"] = current_accent
        logging.info("Settings: accent_color reset to default")
        rgba = Gdk.RGBA()
        rgba.parse(current_accent)
        ac_color_btn.set_rgba(rgba)
        ac_preview.queue_draw()
        update_accent_color(current_accent)
        save_settings()

    reset_accent_btn.connect("clicked", on_reset_accent)
    appearance_card.pack_start(reset_accent_btn, False, False, 0)

    # --- HYPRLAND CARD (only visible on Hyprland) ---
    if HYPRLAND_AVAILABLE:
        hyprland_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        hyprland_card.get_style_context().add_class("card")
        page.pack_start(hyprland_card, False, False, 0)

        hyprland_title = Gtk.Label(label=_("HYPRLAND"))
        hyprland_title.get_style_context().add_class("card-title")
        hyprland_title.set_halign(Gtk.Align.START)
        hyprland_card.pack_start(hyprland_title, False, False, 0)

        # Mouse Sync
        sync_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        sync_row.get_style_context().add_class("setting-row")

        sync_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sync_text.set_hexpand(True)
        sync_label = Gtk.Label(label=_("Sync Mouse DPI"))
        sync_label.get_style_context().add_class("setting-label")
        sync_label.set_halign(Gtk.Align.START)
        sync_desc = Gtk.Label(label=_("Sync rivalcfg DPI with Hyprland mouse settings"))
        sync_desc.get_style_context().add_class("setting-desc")
        sync_desc.set_halign(Gtk.Align.START)
        sync_text.pack_start(sync_label, False, False, 0)
        sync_text.pack_start(sync_desc, False, False, 0)
        sync_row.pack_start(sync_text, True, True, 0)

        sync_switch = Gtk.Switch()
        sync_switch.set_active(app_state["settings"].get("hyprland_mouse_sync", False))
        sync_row.pack_start(sync_switch, False, False, 0)
        hyprland_card.pack_start(sync_row, False, False, 0)

        def on_hyprland_sync_toggled(s, *a):
            val = sync_switch.get_active()
            app_state["settings"]["hyprland_mouse_sync"] = val
            logging.info("Settings: hyprland_mouse_sync = %s", val)
            save_settings()
            if val:
                _hyprctl_sync_mouse_to_rivalcfg(_dpi_active_value())
        sync_switch.connect("notify::active", on_hyprland_sync_toggled)

        # Follow Mouse
        follow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        follow_row.get_style_context().add_class("setting-row")

        follow_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        follow_text.set_hexpand(True)
        follow_label = Gtk.Label(label=_("Follow Mouse"))
        follow_label.get_style_context().add_class("setting-label")
        follow_label.set_halign(Gtk.Align.START)
        follow_desc = Gtk.Label(label=_("Enable Hyprland follow_mouse (cursor warping)"))
        follow_desc.get_style_context().add_class("setting-desc")
        follow_desc.set_halign(Gtk.Align.START)
        follow_text.pack_start(follow_label, False, False, 0)
        follow_text.pack_start(follow_desc, False, False, 0)
        follow_row.pack_start(follow_text, True, True, 0)

        follow_combo = Gtk.ComboBoxText()
        follow_options = [("0", _("Disabled")), ("1", _("Enabled")), ("2", _("On Release"))]
        for val, label in follow_options:
            follow_combo.append_text(label)
        current_follow = str(app_state["settings"].get("hyprland_follow_mouse", 1))
        for i, (v, _label) in enumerate(follow_options):
            if v == current_follow:
                follow_combo.set_active(i)
                break
        follow_row.pack_start(follow_combo, False, False, 0)
        hyprland_card.pack_start(follow_row, False, False, 0)

        def on_follow_mouse_changed(combo):
            idx = combo.get_active()
            if 0 <= idx < len(follow_options):
                val = int(follow_options[idx][0])
                app_state["settings"]["hyprland_follow_mouse"] = val
                logging.info("Settings: hyprland_follow_mouse = %s", val)
                save_settings()
                _hyprctl_set_follow_mouse(val)
        follow_combo.connect("changed", on_follow_mouse_changed)

        # Hyprland status
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_row.set_margin_top(8)
        hypr_status_dot = Gtk.Label(label="●")
        hypr_status_dot.get_style_context().add_class("status-ok")
        hypr_status_label = Gtk.Label(label=_("Hyprland detected"))
        hypr_status_label.get_style_context().add_class("setting-desc")
        status_row.pack_start(hypr_status_dot, False, False, 0)
        status_row.pack_start(hypr_status_label, False, False, 0)
        hyprland_card.pack_start(status_row, False, False, 0)

    # --- DIAGNOSTICS CARD ---
    diagnostics_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    diagnostics_card.get_style_context().add_class("card")
    page.pack_start(diagnostics_card, False, False, 0)

    diagnostics_title = Gtk.Label(label=_("Mouse Settings"))
    diagnostics_title.get_style_context().add_class("card-title")
    diagnostics_title.set_halign(Gtk.Align.START)
    diagnostics_card.pack_start(diagnostics_title, False, False, 0)

    diag_btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    diag_btn_box.set_halign(Gtk.Align.START)

    check_btn = Gtk.Button(label=_("CHECK MOUSE"))
    check_btn.get_style_context().add_class("apply-btn")

    def on_check_mouse(btn):
        def on_detect(success, msg):
            if success and is_steelseries_connected(msg):
                caps_now = get_device_caps(force=True)
                label = _caps_device_name(caps_now)
                GLib.idle_add(set_status, "ok", "✓ " + label)
            else:
                GLib.idle_add(set_status, "error", "✗ " + _("Mouse not found"))

        run_rivalcfg(["--print-debug"], on_detect)

    check_btn.connect("clicked", on_check_mouse)
    diag_btn_box.pack_start(check_btn, False, False, 0)

    caps_now = get_device_caps()
    if caps_now.has_firmware:
        fw_btn = Gtk.Button(label=_("FIRMWARE VERSION"))
        fw_btn.get_style_context().add_class("apply-btn")

        def on_firmware(btn):
            def cb(success, msg):
                if success:
                    GLib.idle_add(set_status, "ok", "✓ " + _("Firmware: %s") % msg)
                else:
                    GLib.idle_add(set_status, "error", "✗ " + _("Could not read firmware"))
            run_rivalcfg(["--firmware-version"], cb)

        fw_btn.connect("clicked", on_firmware)
        diag_btn_box.pack_start(fw_btn, False, False, 0)

    reset_btn = Gtk.Button(label=_("FACTORY RESET"))
    reset_btn.get_style_context().add_class("danger-btn")

    def on_factory_reset(btn):
        dialog = Gtk.MessageDialog(
            parent=app_state.get("window"),
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format=_("All settings will be restored to factory defaults. Are you sure?")
        )
        dialog.set_title(_("Factory Reset"))
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        logging.info("Factory reset executed")
        caps_now = get_device_caps()
        orange = "ff6600"
        zones = {}
        for zone in caps_now.lighting.zones:
            zones[zone.key] = orange
            btn_widget = app_state.get("color_buttons", {}).get(zone.key)
            if btn_widget is not None:
                btn_widget.set_hex(orange)
        app_state["zones"] = zones
        app_state["reactive_hex"] = "off"
        if "rainbow_check" in app_state:
            app_state["rainbow_check"].set_active(False)
        app_state["rainbow_enabled"] = False
        app_state["selected_effect"] = caps_now.lighting.light_effect_default or "steady"
        if caps_now.lighting.has_default_lighting:
            app_state["default_lighting"] = caps_now.lighting.default_lighting_default or "rainbow"
            _combo_select(app_state.get("default_lighting_combo"), app_state["default_lighting"])
        effects = app_state.get("effect_radios", {})
        if app_state["selected_effect"] in effects:
            effects[app_state["selected_effect"]].set_active(True)
        if "redraw_buttons" in app_state:
            app_state["redraw_buttons"]()

        # One canonical plan: --reset, then zone colors -> reactive off ->
        # default lighting -> rainbow last (W5).
        plan = device_core.ApplyPlan("Factory reset")
        plan.add(["--reset"])
        reset_state = {
            "zones": zones,
            "reactive": "off",
            "default_lighting": app_state.get("default_lighting"),
            "rainbow": False,
            "light_effect": app_state.get("selected_effect"),
        }
        plan.steps.extend(device_core.build_lighting_plan(caps_now, reset_state).steps)
        _queue_plan(plan)
        save_active_profile()

    reset_btn.connect("clicked", on_factory_reset)
    diag_btn_box.pack_start(reset_btn, False, False, 0)

    diagnostics_card.pack_start(diag_btn_box, False, False, 0)

    return page


def update_accent_color(accent):
    """Update the CSS accent color dynamically."""
    r = int(accent[1:3], 16) / 255.0
    g = int(accent[3:5], 16) / 255.0
    b = int(accent[5:7], 16) / 255.0
    rgb = f"{r:.2f}, {g:.2f}, {b:.2f}"
    css_accent = f"""
    .nav-active {{
        background: rgba({rgb}, 0.13);
        color: {accent};
    }}
    .apply-btn {{
        background: {accent};
    }}
    .apply-btn:hover {{
        background: rgba({rgb}, 0.8);
    }}
    .card-title {{
        color: {accent};
    }}
    scale trough highlight {{
        background: {accent};
    }}
    .danger-btn {{
        border: 1px solid {accent};
        color: {accent};
    }}
    .danger-btn:hover {{
        background: {accent};
    }}
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css_accent.encode('utf-8'))
    context = Gtk.StyleContext()
    screen = Gdk.Screen.get_default()
    context.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


def rebuild_ui():
    """Rebuild the entire UI with the new language."""
    logging.info("UI rebuilt (language changed)")
    window = app_state["window"]
    current_page = app_state["stack"].get_visible_child_name()
    current_status_text = app_state["status_label"].get_text()
    current_status_classes = app_state["status_dot"].get_style_context().list_classes()

    for child in window.get_children():
        window.remove(child)

    window.set_wmclass("rivalcfg-gui", "RivalCFG GUI")
    icon_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "logo.png")
    if os.path.exists(icon_path):
        icon_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 128, 128, True)
        window.set_icon(icon_pixbuf)
        Gtk.Window.set_default_icon_from_file(icon_path)

    app_state["nav_buttons"] = []
    app_state["is_rebuild"] = True
    create_window_content(window)

    window.show_all()

    active = app_state["settings"].get("active_profile", "Default")
    profile_data = load_profile_data(active)
    if profile_data:
        apply_profile_to_ui(profile_data)

    # Language switch is UI-only: no device writes (W4).
    set_status("ok", "✓ " + _("Loaded profile {name} — press Apply to send").format(
        name=app_state["settings"].get("active_profile", "Default")))

    if app_state["settings"]["startup_minimize"]:
        window.iconify()

    def restore_page():
        app_state["stack"].set_visible_child_name(current_page)
        nav_labels = {
            "sensitivity": _("SENSITIVITY"),
            "rgb": _("RGB"),
            "buttons": _("BUTTONS"),
            "devices": _("DEVICES"),
            "power": _("POWER"),
            "settings": _("SETTINGS"),
            "about": _("ABOUT"),
        }
        target_label = nav_labels.get(current_page, current_page)
        for btn in app_state["nav_buttons"]:
            btn.get_style_context().remove_class("nav-active")
            if btn.get_label() == target_label:
                btn.get_style_context().add_class("nav-active")

        app_state["status_label"].set_text(current_status_text)
        for cls in current_status_classes:
            app_state["status_dot"].get_style_context().add_class(cls)

        return False

    GLib.idle_add(restore_page)


def create_window():
    """Create the main window and its contents."""
    app_state["settings"] = load_settings()
    app_state["is_rebuild"] = False
    lang = app_state["settings"].get("language", None)
    _set_language(lang)

    window = Gtk.Window(title="RivalCFG GUI")
    window.set_default_size(1280, 720)
    window.set_resizable(True)

    def on_destroy(*a):
        queue = app_state.get("command_queue")
        if queue is not None:
            queue.stop()
        Gtk.main_quit()

    window.connect("destroy", on_destroy)
    window.set_wmclass("rivalcfg-gui", "RivalCFG GUI")

    icon_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "logo.png")
    if os.path.exists(icon_path):
        icon_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 128, 128, True)
        window.set_icon(icon_pixbuf)
        Gtk.Window.set_default_icon_from_file(icon_path)

    app_state["window"] = window

    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(CSS.encode('utf-8'))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    update_accent_color(app_state["settings"]["accent_color"])

    _init_command_queue()

    create_window_content(window)

    window.show_all()

    active = app_state["settings"].get("active_profile", "Default")
    profiles = list_profiles()
    if active not in profiles:
        active = "Default"
        app_state["settings"]["active_profile"] = "Default"
        save_settings()
    profile_data = load_profile_data(active)
    if profile_data:
        apply_profile_to_ui(profile_data)

    # Never write to the device on startup (W4): load into the UI only.
    set_status("ok", "✓ " + _("Loaded profile {name} — press Apply to send").format(name=active))

    if app_state["settings"]["startup_minimize"]:
        window.iconify()


def create_window_content(window):
    """Create the main window content. Separated for rebuild_ui."""
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    window.add(vbox)

    content_grid = Gtk.Grid()
    content_grid.set_column_spacing(0)
    content_grid.set_row_spacing(0)
    content_grid.set_column_homogeneous(False)
    content_grid.set_vexpand(True)
    content_grid.set_hexpand(True)
    vbox.pack_start(content_grid, True, True, 0)

    sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    sidebar.set_size_request(180, -1)
    sidebar.get_style_context().add_class("sidebar")
    sidebar.set_margin_top(16)
    sidebar.set_margin_bottom(16)
    sidebar.set_margin_start(16)
    sidebar.set_margin_end(16)
    sidebar.set_vexpand(True)
    sidebar.set_hexpand(False)
    sidebar.set_halign(Gtk.Align.START)

    content_grid.attach(sidebar, 0, 0, 1, 1)

    icon_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "logo.png")
    if os.path.exists(icon_path):
        logo_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 100, 100, True)
        logo_img = Gtk.Image.new_from_pixbuf(logo_pixbuf)
        logo_img.set_margin_bottom(8)
        sidebar.pack_start(logo_img, False, False, 0)

    brand = Gtk.Label(label="RivalCFG GUI")
    brand.get_style_context().add_class("page-title")
    brand.set_margin_bottom(20)
    brand.set_halign(Gtk.Align.CENTER)
    brand.set_hexpand(True)
    sidebar.pack_start(brand, False, False, 0)

    def update_profile_selector_label(name):
        label = app_state.get("profile_label")
        if label:
            label.set_text(name)

    def close_profile_popover():
        popover = app_state.get("profile_popover")
        if popover:
            popover.popdown()

    def select_profile(name, close_popover=False):
        if app_state.get("_loading_profile"):
            return
        profile_data = load_profile_data(name)
        if profile_data:
            apply_profile_to_ui(profile_data)
        app_state["settings"]["active_profile"] = name
        save_settings()
        update_profile_selector_label(name)
        # Loading a profile must NOT write to the device (W4).
        set_status("ok", "✓ " + _("Loaded profile {name} — press Apply to send").format(name=name))
        logging.info("Profile loaded (no write): %s", name)
        if close_popover:
            close_profile_popover()

    def on_delete_profile_named(name):
        if not name:
            return
        close_profile_popover()
        profiles = list_profiles()
        if len(profiles) <= 1:
            dialog = Gtk.MessageDialog(
                parent=app_state["window"],
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                message_format=_("Cannot delete the last profile."),
            )
            dialog.run()
            dialog.destroy()
            return
        dialog = Gtk.MessageDialog(
            parent=app_state["window"],
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format=_('Delete profile "%s"?') % name,
        )
        dialog.set_title(_("Delete Profile"))
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        was_active = app_state["settings"].get("active_profile") == name
        delete_profile_file(name)
        logging.info("Profile deleted: %s", name)
        refresh_profile_selector()
        if was_active:
            remaining = list_profiles()
            if remaining:
                select_profile(remaining[0])

    def on_rename_profile_named(name):
        if not name:
            return
        close_profile_popover()
        dialog = Gtk.Dialog(
            title=_("Rename Profile"),
            parent=app_state["window"],
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_buttons(
            _("Cancel"), Gtk.ResponseType.CANCEL,
            _("OK"), Gtk.ResponseType.OK,
        )
        dialog.set_default_size(300, 130)
        box = dialog.get_content_area()
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        lbl = Gtk.Label(label=_("Profile name:"))
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 4)
        entry = Gtk.Entry()
        entry.set_text(name)
        box.pack_start(entry, False, False, 4)
        dialog.show_all()
        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_name or new_name == name:
            return
        if not rename_profile_file(name, new_name):
            err = Gtk.MessageDialog(
                parent=app_state["window"],
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format=_('Could not rename to "%s". Name may already exist.') % new_name,
            )
            err.run()
            err.destroy()
            return
        logging.info("Profile renamed: %s → %s", name, new_name)
        if app_state["settings"].get("active_profile") == name:
            app_state["settings"]["active_profile"] = new_name
            save_settings()
            update_profile_selector_label(new_name)
        refresh_profile_selector()

    def refresh_profile_selector():
        profile_list = app_state.get("profile_list")
        if not profile_list:
            return
        active = app_state["settings"].get("active_profile", "Default")
        profiles = list_profiles()
        for child in profile_list.get_children():
            profile_list.remove(child)
        for p in profiles:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            row.set_margin_start(4)
            row.set_margin_end(4)
            row.get_style_context().add_class("profile-popover-row")
            name_btn = Gtk.Button(label=p)
            name_btn.set_relief(Gtk.ReliefStyle.NONE)
            name_btn.set_halign(Gtk.Align.START)
            name_btn.set_hexpand(True)
            name_btn.get_style_context().add_class("profile-menu-select")
            name_btn.connect("clicked", lambda _w, profile_name=p: select_profile(profile_name, close_popover=True))
            edit_btn_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "pencil.svg"),
                16, 16, True)
            edit_btn = Gtk.Button()
            edit_btn.set_image(Gtk.Image.new_from_pixbuf(edit_btn_pixbuf))
            edit_btn.set_relief(Gtk.ReliefStyle.NONE)
            edit_btn.get_style_context().add_class("profile-menu-action")
            edit_btn.connect("clicked", lambda _w, profile_name=p: on_rename_profile_named(profile_name))
            delete_btn_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "trash.svg"),
                16, 16, True)
            delete_btn = Gtk.Button()
            delete_btn.set_image(Gtk.Image.new_from_pixbuf(delete_btn_pixbuf))
            delete_btn.set_relief(Gtk.ReliefStyle.NONE)
            delete_btn.get_style_context().add_class("profile-menu-action")
            delete_btn.get_style_context().add_class("profile-menu-delete")
            delete_btn.connect("clicked", lambda _w, profile_name=p: on_delete_profile_named(profile_name))
            row.pack_start(name_btn, True, True, 0)
            row.pack_start(edit_btn, False, False, 0)
            row.pack_start(delete_btn, False, False, 0)
            profile_list.pack_start(row, False, False, 0)
        profile_list.show_all()
        if active in profiles:
            update_profile_selector_label(active)
        elif profiles:
            select_profile(profiles[0])
        else:
            update_profile_selector_label("Default")

    def on_new_profile(btn):
        dialog = Gtk.Dialog(
            title=_("New Profile"),
            parent=app_state["window"],
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_buttons(
            _("Cancel"), Gtk.ResponseType.CANCEL,
            _("OK"), Gtk.ResponseType.OK,
        )
        dialog.set_default_size(300, 130)
        box = dialog.get_content_area()
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        lbl = Gtk.Label(label=_("Profile name:"))
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 4)
        entry = Gtk.Entry()
        box.pack_start(entry, False, False, 4)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and name:
            save_profile(name)
            logging.info("Profile created: %s", name)
            app_state["settings"]["active_profile"] = name
            save_settings()
            refresh_profile_selector()
            select_profile(name)

    profile_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    profile_section.set_margin_bottom(16)
    profile_section.set_hexpand(True)
    profile_section.set_halign(Gtk.Align.FILL)

    profile_title = Gtk.Label(label=_("PROFILES"))
    profile_title.get_style_context().add_class("card-title")
    profile_title.set_halign(Gtk.Align.START)
    profile_section.pack_start(profile_title, False, False, 0)

    combo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    profile_selector_btn = Gtk.Button()
    profile_selector_btn.set_hexpand(True)
    profile_selector_btn.set_halign(Gtk.Align.FILL)
    profile_selector_btn.set_relief(Gtk.ReliefStyle.NONE)
    profile_selector_btn.get_style_context().add_class("profile-selector")
    profile_selector_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    profile_label = Gtk.Label(label=app_state["settings"].get("active_profile", "Default"))
    profile_label.set_halign(Gtk.Align.START)
    profile_label.set_hexpand(True)
    profile_arrow = Gtk.Label(label="▾")
    profile_arrow.get_style_context().add_class("profile-arrow")
    profile_selector_box.pack_start(profile_label, True, True, 0)
    profile_selector_box.pack_start(profile_arrow, False, False, 0)
    profile_selector_btn.add(profile_selector_box)
    profile_popover = Gtk.Popover.new(profile_selector_btn)
    profile_popover.set_position(Gtk.PositionType.BOTTOM)
    profile_popover.get_style_context().add_class("profile-popover")
    profile_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    profile_list.set_size_request(200, -1)
    profile_popover.add(profile_list)
    def toggle_profile_popover(_btn):
        if profile_popover.get_visible():
            profile_popover.popdown()
        else:
            profile_popover.popup()

    profile_selector_btn.connect("clicked", toggle_profile_popover)
    combo_row.pack_start(profile_selector_btn, True, True, 0)
    new_btn = Gtk.Button(label="+")
    new_btn.set_size_request(32, -1)
    new_btn.get_style_context().add_class("reset-btn")
    new_btn.connect("clicked", on_new_profile)
    combo_row.pack_start(new_btn, False, False, 0)
    profile_section.pack_start(combo_row, True, True, 0)

    sidebar.pack_start(profile_section, False, False, 0)
    app_state["profile_popover"] = profile_popover
    app_state["profile_list"] = profile_list
    app_state["profile_selector_btn"] = profile_selector_btn
    app_state["profile_label"] = profile_label
    refresh_profile_selector()

    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
    stack.set_hexpand(True)
    stack.set_vexpand(True)

    stack_scroll = Gtk.ScrolledWindow()
    stack_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    stack_scroll.set_vexpand(True)
    stack_scroll.set_hexpand(True)
    stack_scroll.add(stack)
    content_grid.attach(stack_scroll, 1, 0, 1, 1)
    app_state["stack"] = stack

    pages = [
        ("sensitivity", _("SENSITIVITY"), create_sensitivity_page()),
        ("rgb", _("RGB"), create_rgb_page()),
        ("buttons", _("BUTTONS"), create_buttons_page()),
        ("devices", _("DEVICES"), create_devices_page()),
        ("power", _("POWER"), create_power_page()),
        ("settings", _("SETTINGS"), create_settings_page()),
        ("about", _("ABOUT"), create_about_page()),
    ]

    for name, title, page in pages:
        stack.add_named(page, name)

    app_state["nav_buttons"] = []

    def on_nav_clicked(button, page_name):
        for btn in app_state["nav_buttons"]:
            btn.get_style_context().remove_class("nav-active")
        button.get_style_context().add_class("nav-active")
        stack.set_visible_child_name(page_name)

    nav_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

    _start_page = os.environ.get("RIVALCFG_GUI_START_PAGE", "sensitivity")
    if _start_page in ("dpi", "polling"):        # merged into one page
        _start_page = "sensitivity"
    for i, (name, title, __page) in enumerate(pages):
        btn = Gtk.Button(label=title)
        btn.get_style_context().add_class("nav-btn")
        if name == _start_page:
            btn.get_style_context().add_class("nav-active")
        btn.set_hexpand(True)
        btn.set_halign(Gtk.Align.FILL)
        btn.connect("clicked", on_nav_clicked, name)
        nav_box.pack_start(btn, False, False, 0)
        app_state["nav_buttons"].append(btn)

    if _start_page != "sensitivity":
        GLib.idle_add(stack.set_visible_child_name, _start_page)

    nav_scroll = Gtk.ScrolledWindow()
    nav_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    nav_scroll.set_vexpand(True)
    nav_scroll.add(nav_box)
    sidebar.pack_start(nav_scroll, True, True, 0)

    status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    status_bar.set_size_request(-1, 32)
    status_bar.get_style_context().add_class("status-bar")
    vbox.pack_end(status_bar, False, False, 0)

    dot = Gtk.Label(label="●")
    dot.get_style_context().add_class("status-running")
    status_bar.pack_start(dot, False, False, 0)
    app_state["status_dot"] = dot

    status_label = Gtk.Label(label=_("Starting..."))
    status_label.set_halign(Gtk.Align.START)
    status_bar.pack_start(status_label, False, False, 0)
    app_state["status_label"] = status_label

    bug_report_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "bug-report.svg"),
        16, 16, True
    )
    bug_report_btn = Gtk.Button()
    bug_report_btn.set_image(Gtk.Image.new_from_pixbuf(bug_report_pixbuf))
    bug_report_btn.set_relief(Gtk.ReliefStyle.NONE)
    bug_report_btn.set_tooltip_text(_("Bug Report"))
    bug_report_btn.get_style_context().add_class("status-bar-btn")
    bug_report_btn.connect("clicked", lambda _: Gtk.show_uri(
        None, "https://github.com/MrGodzilla38/rivalcfg-gui/issues",
        Gtk.get_current_event_time()
    ))
    status_bar.pack_end(bug_report_btn, False, False, 0)

    app_state["no_save"] = False
    no_save_check = Gtk.CheckButton(label=_("Don't save (--no-save)"))
    status_bar.pack_end(no_save_check, False, False, 0)

    def on_no_save_toggled(button):
        val = button.get_active()
        app_state["no_save"] = val
        logging.info("Settings: no_save = %s", val)

    no_save_check.connect("toggled", on_no_save_toggled)

    if not app_state.get("is_rebuild"):
        def startup_check():
            def cb(success, msg):
                if success and is_steelseries_connected(msg):
                    logging.info("Startup: mouse connected")
                    DEVICE_MANAGER.invalidate()
                    caps_now = DEVICE_MANAGER.get_caps(refresh=True)
                    set_status("ok", "✓ " + _caps_device_name(caps_now))
                else:
                    logging.warning("Startup: mouse not found")
                    set_status("error", "✗ " + _("Mouse not found"))
            run_rivalcfg(["--print-debug"], cb)

        GLib.idle_add(startup_check)

        def _poll_hotplug():
            try:
                sig = DEVICE_MANAGER.device_signature()
            except Exception:
                return True
            if sig != app_state.get("device_signature"):
                app_state["device_signature"] = sig
                DEVICE_MANAGER.invalidate()
                logging.info("Device set changed: %s", sig)
                if sig:
                    caps_now = DEVICE_MANAGER.get_caps(refresh=True)
                    set_status("ok", "✓ " + _caps_device_name(caps_now))
                else:
                    set_status("error", "✗ " + _("Mouse not found"))
                refresh = app_state.get("_refresh_devices")
                if refresh is not None:
                    refresh()
            return True

        GLib.timeout_add_seconds(3, _poll_hotplug)


def main():
    GLib.set_prgname("rivalcfg-gui")
    setup_logging()
    logging.info("Application started")
    create_window()
    Gtk.main()


if __name__ == "__main__":
    main()