# RivalCFG GUI
<img width="1291" height="750" alt="image" src="https://github.com/user-attachments/assets/98d0daf0-7061-481e-a4a7-ab4bcfa5f7a6" />

A Linux desktop configuration tool for SteelSeries mice (via the `rivalcfg` CLI).

## Features

- **DPI Settings** — up to 5 adjustable presets via sliders (range/step/preset count read from the device profile, e.g. 200–8500 Rival 3, 100–18000 Aerox 5 Wireless)
- **Polling Rate** — choices taken from the device profile (typically 125 / 250 / 500 / 1000 Hz)
- **RGB Lighting** — one capability-driven layout: per-zone colors with a strip preview, a **standalone reactive (click-flash) row**, a **wake lighting** dropdown, and a **rainbow toggle always sent last**. Works for both the Aerox family and the Rival 3 class
- **Button Mapping** — an interactive **3D wireframe** of the mouse (Aerox 5) with clickable button facets, human-readable assignment chips, and assignment popovers for mouse buttons, DPI cycle, scroll, disable, **keyboard keys**, and **multimedia keys**. Switch between 3D, top and left-side views
- **Colour picker** — a built-in HSV gradient + hue strip + **hex entry** popover (no OS colour dialog), so any exact LED colour can be entered directly
- **Connected Devices** — lists only the currently plugged, supported devices (name, VID:PID, endpoint, firmware where supported)
- **Power** — battery level (where reported), sleep timer (0–20 min), dim timer (0–1200 s, with a hint that 0 avoids dimming while comparing colors), and LED brightness only on devices that expose it
- **Profiles** — schema-versioned save/load/rename/delete; v1 profiles migrate automatically and macro data is dropped
- **Auto-Apply** — optionally apply settings immediately on change (debounced/coalesced)
- **Single-writer command queue** — every write is serialized so commands never race the HID device; a user action is one atomic ordered plan
- **No writes without consent** — launching the app, switching language, or selecting a profile only updates the UI; the device is touched on Apply (or auto-apply)
- **Mouse Status** — connection status in the status bar, refreshed on hotplug
- **Language Switching** — switch UI language at runtime
- **Accent Color** — customizable UI accent color
- **Factory Reset** — restore all mouse settings to defaults
- **Startup Minimize** — option to launch minimized to system tray
- **Logging** — daily rotating logs kept for 7 days in `~/.config/rivalcfg-gui/logs/`

## Language Support

| Language | Code |
|----------|------|
| English (reference) | `en` |
| German | `de` |
| Spanish | `es` |
| French | `fr` |
| Italian | `it` |
| Polish | `pl` |
| Portuguese (Brazil) | `pt_BR` |
| Russian | `ru` |
| Turkish | `tr` |
| Chinese (Simplified) | `zh_CN` |

To contribute a new translation, copy `locales/en/LC_MESSAGES/rivalcfg_gui.po`, translate the strings, and submit a pull request.

## Supported Devices

Works with all devices supported by `rivalcfg` — Rival 100/300/500/600/700 series,
Sensei, Kinzu, Aerox, Prime, and more.

> Full list: https://github.com/flozz/rivalcfg#supported-devices
>
> Tested against Rival 3 and SteelSeries Aerox 5 Wireless (1038:1852 2.4GHz / 1038:1854 wired).
> Capabilities (DPI, zones, effects, buttons, timers, battery) come from the installed
> `rivalcfg` device profiles, with a `rivalcfg --help` fallback parser if the library
> cannot be introspected. Button artwork is a generated 3D wireframe (GTK/Cairo
> only, no bitmaps) whose geometry is extracted from a detailed, dimensioned
> mesh model of the Aerox 5 (see `tools/extract_aerox5_mesh.py`).

## Wayland notes

The application talks to the mouse through `rivalcfg`/HID, so it works on both X11 and
Wayland. The previous software auto-clicker and its setuid helper were removed: on Wayland
a normal application cannot read or inject input events globally, so a built-in auto-clicker
is not offered. Configure such behaviour with your compositor or a dedicated tool instead.


## Installation

### Arch Linux (AUR)

```bash
yay -S rivalcfg-gui
```

**Upgrade:**
```bash
yay -Suy rivalcfg-gui
```

**Uninstall:**
```bash
yay -Rns rivalcfg-gui
```

## Requirements

| Package | Purpose |
|---------|---------|
| Python 3 | Runtime |
| GTK3 | UI framework |
| python-gobject (`gi`) | Python GTK3 bindings |
| python-cairo (`cairo`) | Python Cairo bindings (custom colour picker + 3D wireframe) |
| `rivalcfg` | SteelSeries CLI tool / device profiles |

If no mouse is connected or `rivalcfg` is not found, the application will exit immediately with an error message.

### Development

```bash
pip install pytest
python -m pytest tests/
```

## License
GPL-3.0-or-later
