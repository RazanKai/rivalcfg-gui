# RivalCFG GUI
<img width="1291" height="750" alt="image" src="https://github.com/user-attachments/assets/98d0daf0-7061-481e-a4a7-ab4bcfa5f7a6" />

A Linux desktop configuration tool for SteelSeries mice (via the `rivalcfg` CLI).

## Features

- **DPI Settings** — up to 5 adjustable presets via sliders (range auto-detected from `rivalcfg --help`, e.g. 200–8500 Rival 3, 100–18000 Aerox 5 Wireless, in 100-step increments)
- **Polling Rate** — 125 / 250 / 500 / 1000 Hz selection
- **RGB Lighting** — device-aware zones and effects: Rival 3 (top/middle/bottom/logo + steady/breath/rainbow-shift/rainbow-breath/disco), Aerox family (top/middle/bottom, rainbow flag, reactive color, default lighting)
- **Button Mapping** — remap buttons with an interactive mouse diagram (Aerox 5 Wireless: 9 buttons + scroll up/down preserved)
- **Auto-Clicker** — software auto-clicker with configurable CPS (1–50), trigger key (keyboard or mouse), toggle/hold modes, and toggle key shortcut
- **Profiles** — save, load, rename, and delete named profiles containing all settings
- **Auto-Apply** — optionally apply settings immediately on change
- **Device Info** — list connected devices and check firmware version
- **Mouse Status** — real-time connection status in the status bar
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
> DPI range, RGB zones/effects and button count are auto-detected from `rivalcfg --help`.

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
| python-cairo (`cairo`) | Python Cairo bindings |
| `rivalcfg` | SteelSeries CLI tool |
| python-evdev (`evdev`) | Linux input event monitoring |
| python-pynput (`pynput`) | Mouse control and event capture |
| python-xlib (`Xlib`) | X11 keycode resolution |

If no mouse is connected or `rivalcfg` is not found, the application will exit immediately with an error message.

## License
GPL-3.0-or-later
