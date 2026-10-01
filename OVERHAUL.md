# Overhaul Plan — rivalcfg-gui fork (Aerox 5 Wireless first)

Baseline: `fix-aerox5-wireless` (PR #1, closed as superseded — its
detection/flags/DPI/button7-9 fixes carry over as the floor).
Test hardware: SteelSeries Aerox 5 Wireless (`1038:1852` 2.4GHz / `1038:1854` wired).
Test OS: CachyOS + Hyprland/Wayland.

## 1. Verified Aerox 5 Wireless semantics (8-cell matrix, all writes exit 0)

| reactive | default lighting | rainbow flag | click flash? | base look     |
|----------|------------------|--------------|--------------|---------------|
| off      | off              | off          | no           | solid color   |
| off      | off              | on           | no           | no light      |
| off      | rainbow          | off          | no           | solid color   |
| off      | rainbow          | on           | no           | rainbow       |
| green    | off              | off          | yes          | solid color   |
| green    | off              | on           | yes          | no light      |
| green    | rainbow          | off          | yes          | solid color   |
| green    | rainbow          | on           | yes          | rainbow       |

Conclusions (locked):
- Click-flash happens iff a reactive color is programmed. Fully decoupled
  from default lighting and rainbow flag.
- `reactive-rainbow` is observationally identical to `rainbow` (+ flash iff
  reactive color set). Keep all 4 dropdown values passed through verbatim
  (wake behavior), but never couple them to the reactive row in the UI.
- Default lighting has no immediate effect on steady colors (wake-only).
- Rows 2/6 exposed an Apply-order bug: `--default-lighting off` sent AFTER
  `--rainbow-effect` kills the rainbow. Canonical send order (locked):
  **zone colors → reactive color → default lighting → rainbow flag last**.
- Dim timer (default 30s) dims LEDs on idle; this model exposes no brightness
  setting in rivalcfg. Suspected cause of "colors don't match picker"
  reports — verify with pure primaries before claiming gamut limits.

## 2. Code review findings (upstream)

Device handling:
- Plugged-in check hardcoded to Rival 3 PID `184c` (`rivalcfg_gui.py:3218,3863`).
  Any other mouse = permanent "Mouse not connected".
- RGB flags hardcoded Rival 3 (`--strip-*`, `--logo-color`, `--light-effect`);
  Aerox family needs `--top-/--middle-/--bottom-color`, `--rainbow-effect`,
  `--reactive-color`, `--default-lighting` (`exit 2` on Aerox).
- DPI range hardcoded `200–8500` (Aerox 5 Wireless: `100–18000`).
- Buttons page renders one `assets/rival3.png` top view for every mouse; no
  side view; buttons 7–9 not clickable; popover lacks keyboard/multimedia
  actions that rivalcfg supports.
- Devices page titled "Connected Devices" dumps `rivalcfg --list` (all
  supported models ever) instead of plugged devices.

Missing UI: battery level, sleep/dim timers, LED brightness (where supported),
reactive color, default lighting, rainbow flag, firmware gating.

Behavioral bugs:
- Launch writes to the mouse (Default profile auto-applied, `:3541`); same on
  every profile switch. Reads must never write without consent.
- Slider auto-apply spawns a process per tick, no debounce; parallel
  `run_rivalcfg` threads race on the status bar and contend on HID
  (interleaved `OSError: open failed` observed).
- Chained writes report per-step status; final "Done" can mask mid-chain
  failure. No atomic apply, no dry-run.
- `mapping['button1']` direct indexing — old/hand-edited profiles missing keys
  crash apply. Profiles have no schema version.
- RGB page (PR #1): rainbow checkbox + default-lighting dropdown overlap;
  reactive Off checkbox contradicts the green picker next to it; layout grew
  organically ("vibeslop"). Full redesign required (see Phase 2).

Packaging/distribution:
- `setup.py` has no `console_scripts` entry point and ships no
  assets/locales/helper — pip install is broken.
- `evdev_helper` setuid-root C binary + pkexec `chown root`/`chmod u+s` flow
  (:1002). DECIDED: drop the auto-clicker and the setuid helper entirely.
- No tests, no CI. Locales rot on every string change.

## 3. Decisions (locked)

- Device metadata: **library-import primary, CLI-scraping fallback.**
  Import rivalcfg's device profiles for structured caps; fall back to the
  `--help` parser from PR #1 when import fails.
- Button art: **Cairo-drawn schematics**, per-device layout registry
  (views, anchors in 0–1 relative coords). Start with **Aerox 5 only**
  (top + left-side views). Keep the anchor→leader-line→label mechanism;
  redesign the label look (human-readable assignment chips, no raw
  `button1`/`scrollup` internals, no overlaps).
- Drop auto-clicker + setuid helper. Document Wayland limits instead.
- PR #1 closed as superseded; its fixes are the Phase 0 baseline.

## 4. Phases

**Phase 0 — baseline.** Keep PR #1 behavior (detection, flags, DPI, 7–9
passthrough). Fix Apply order per §1.

**Phase 1 — device core** (new `device_core.py`).
- Plugged-device detection via rivalcfg library; capability model per device
  (DPI range/steps/presets, zones, effects, timers, buttons, battery,
  firmware support).
- Single-writer command queue: serialized HID access, ~300ms slider debounce,
  one honest status per user action showing the failing command.
- Capability-gated UI (hide firmware/battery/logo where unsupported).

**Phase 2 — RGB redesign (Aerox 5 first).**
- One lighting card: 3 zone colors with strip preview → reactive row
  (Off toggle + color, standalone) → wake dropdown (all 4 values, labeled
  "on wake") → single rainbow toggle (sent last). No cross-dependencies.

**Phase 3 — Buttons redesign (Aerox 5 first).**
- Top + side views, all 9 buttons + scroll labeled/clickable; popover gains
  keyboard + multimedia actions.

**Phase 4 — Devices + Power.**
- Devices: only plugged devices (name, VID:PID, endpoint, firmware, battery).
- New Power page: battery, sleep/dim timers.

**Phase 5 — behavior.**
- No writes on launch/switch; explicit Apply; profile schema v2 + migration +
  load-time validation.

**Phase 6 — quality.**
- `pytest` with mocked rivalcfg; CI; Dependabot/Renovate on the rivalcfg pin
  + CI job against latest rivalcfg to catch internal-API breakage;
  `console_scripts` entry point; packaged assets/locales; refreshed `.po`.

## 5. Open questions

- Rival 3 + other families' layouts: after Aerox 5 proves the pattern.
- Wake-behavior verification (needs sleep-timer-driven test, not yet run).
- Whether `reactive`/`reactive-rainbow` wake values differ observably at all
  on this firmware (matrix says no for the immediate state).
