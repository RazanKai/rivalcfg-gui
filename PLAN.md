# PLAN.md — rivalcfg-gui Overhaul

Supersedes `OVERHAUL.md` (deleted): its hardware-verified findings and locked
decisions are inlined below as §1, making this the single source of truth.
Concrete, file-level work against the current tree (`rivalcfg_gui.py`,
4,230 lines; rivalcfg 4.17.0 installed and inspected).

Test hardware: SteelSeries Aerox 5 Wireless (`1038:1852` 2.4GHz / `1038:1854` wired).
Test OS: CachyOS + Hyprland/Wayland.

---

## 0. Current-state audit (from codebase crawl)

### 0.1 What the fork already has (PR #1 baseline — do not re-litigate)

- Plugged-in check is generic: `is_steelseries_connected()` (`rivalcfg_gui.py:188`)
  regexes `1038:xxxx` from `--print-debug`. The old `184c` hardcode is gone.
- `get_device_caps()` (`:121`) parses `rivalcfg --help` per family: aerox vs
  rival3 flags, DPI range (`100–18000` for Aerox 5W), button7–9 detection,
  device label.
- `build_buttons_arg()` (`:195`) emits 7–9 passthrough for extra-button devices.
- Buttons popover passes scrollup/scrolldown through.

### 0.2 Verified defects (line numbers current to this fork)

**Device handling**
- D1. `get_device_caps()` shells out to `rivalcfg --help`. rivalcfg's CLI picks
  the *first* plugged mouse; with several devices (or none) the caps describe
  the wrong mouse, or silently fall back to Rival 3 defaults (`:131-149`).
  `--help` also exits non-zero with no device → whole caps layer degrades.
- D2. Devices page dumps `rivalcfg --list` — the catalogue of every supported
  model ever, not what is plugged (`create_devices_page`, `:2859-2903`).
- D3. FIRMWARE VERSION button (`:3553-3565`) can never succeed on the Aerox 5
  Wireless: the rivalcfg profile has no `firmware_version` key, so the CLI
  never offers `--firmware-version`; the button reports the misleading
  "Mouse not connected" instead of "unsupported".
- D4. No battery, sleep-timer, dim-timer, or brightness UI even though the
  Aerox 5W supports `--battery-level`, `-t/--sleep-timer (0–20 min)`,
  `-T/--dim-timer (0–1200 s)`. (`led_brightness` is *not* in the
  `aerox5_wireless` profiles — must be capability-gated, not assumed.)

**Writes & sequencing (canonical order = zone colors → reactive → default lighting → rainbow LAST, per §1)**
- W1. `on_apply_rgb()` Aerox chain sends rainbow *before* default-lighting
  (`:2023-2035`) — violates the locked order (rows 2/6 of the §1 matrix:
  `--default-lighting` sent after `--rainbow-effect` kills the rainbow).
- W2. `apply_all_to_device()` **writes the whole argument vector twice**
  (`:3155-3163`): once through the `if extra:` callback chain (or the `else`
  branch), then again unconditionally at `:3163`. Every profile switch issues a
  full duplicate write burst — explains observed HID contention and slowness.
- W3. Its `extra` follow-up (`:3108-3113`) also sends rainbow before
  default-lighting (same order violation as W1).
- W4. Launch writes to the mouse: `create_window_content()` →
  `refresh_profile_selector()` (`:3978`) auto-selects `profiles[0]` when the
  active profile is missing from the list (`:4022`) → `select_profile()` →
  `apply_all_to_device()` (`:3887`). Same path fires on every language switch
  (`rebuild_ui`, `:3691`) since the whole UI is rebuilt.
- W5. Factory-reset Aerox chain (`:3607-3623`) re-applies zone colors and
  default-lighting but never re-sends `--reactive-color off`, and again sends
  default-lighting without honoring the canonical order (rainbow would be last
  if enabled).
- W6. No serialization: `run_rivalcfg()` (`:686`) spawns a daemon thread per
  call. Slider auto-apply fires one process per tick (`on_dpi_changed`,
  `:1639-1663`); concurrent threads race the status bar and the HID device
  (interleaved `OSError: open failed` observed upstream).
- W7. Status honesty: each step in a callback chain sets its own
  "Processing…/Done"; a mid-chain failure leaves the bar at the *previous*
  step's error, and the chain simply stops (`after_z1` etc. `return` on
  failure) — no summary, no indication of what completed.
- W8. Rainbow-off handling re-sends only `--top-color` (`:1891-1900`), which
  per §1 does clear the rainbow, but leaves an inconsistent device state if
  z2/z3 differ; canonical re-apply (all zones) is the safe path.

**Buttons page**
- B1. One hardcoded `assets/rival3.png` top view for every mouse (`:2118-2132`),
  scaled to 400×400; per-button coordinates hardcoded in pixels (`:2155-2164`).
- B2. Hit areas are the *label boxes*, not the mouse buttons themselves
  (`on_button_press`, `:2305-2319`) — you click the floating chip, not the
  schematic. Buttons 7–9 have no anchors/labels at all.
- B3. Popover offers only button1–6 / dpi / scrollup / scrolldown / disable
  (`:2085-2096`). rivalcfg's buttons handler supports keyboard actions
  (`anykey(key=…; layout=qwerty)` via `layout_qwerty`) and multimedia
  (`play`, `pause`, `prev`, `next`, `vol+`, `vol-`, `mute` via
  `layout_multimedia`) — none exposed.

**Profiles & state**
- P1. `apply_all_to_device()` non-aerox branch uses direct indexing
  `mapping['button1']` (`:3147-3151`) → KeyError crash on old/hand-edited
  profiles missing keys. (Aerox branch was already `.get()`-fixed.)
- P2. No profile schema version; `save_profile` (`:317`) writes a flat mix of
  device settings + macro settings; `apply_profile_to_ui` (`:2956`) papering
  over missing keys case-by-case.
- P3. `app_state` is one global dict mutated from GUI threads, worker
  threads, and `GLib.idle_add` callbacks without locks.

**Auto-clicker / setuid helper (DECIDED: drop entirely)**
- A1. `MacroEngine` (`:984-1538`, ~560 lines) + `evdev_helper.c` (65 lines) +
  `_ensure_helper_setup()` pkexec `chown root && chmod u+s` flow (`:1136-1171`)
  + `_WL_KEYCODE_TABLE` (`:754-793`) + X11/Wayland keycode resolution
  (`:796-814`, `:1042-1105`) + `_find_keyboard_device`/`_find_mouse_device`
  (`:918-983`).
- A2. Downstream packaging builds/installs the helper and the macro deps:
  `dist/aur/PKGBUILD` (gcc makedep, helper build, `rivalcfg-gui.install`
  scriplets), `dist/debian/rules` (gcc + wget + bundled rivalcfg binary),
  `dist/flatpak/io.github.MrGodzilla38.rivalcfg_gui.yml` (evdev_helper
  module) and `python3-requirements.json` (evdev/pynput/python-xlib/six
  modules). `requirements.txt`/`setup.py` list the same three deps.
- A3. Macro state is entangled with profiles (`save_profile` persists
  `macro_*`; `apply_profile_to_ui` restarts listeners) — removal touches
  profile I/O too.
- A4. Hyprland card (`:3395-3517`): `hyprland_macro_bind` is macro-only →
  drop with A1; `hyprland_mouse_sync` (DPI→sensitivity sync) and
  `hyprland_follow_mouse` are window-manager conveniences unrelated to the
  clicker → keep (see Open Questions Q3).

**Packaging / quality**
- K1. `setup.py`: no `console_scripts` entry point, no `package_data`
  (assets + locales never ship) → `pip install rivalcfg-gui` is broken.
- K2. No tests, no CI config anywhere in the tree; locales are hand-maintained
  `.po`+`.mo` pairs committed per-language (en has 140 msgids; de translates
  only 132 → drift already started).
- K3. UI rebuild-on-language-change (`rebuild_ui`) re-runs
  `create_window_content` and re-triggers W4's write path.

### 0.3 rivalcfg 4.17 library surface we will build on (Phase 1)

- `rivalcfg.devices.list_plugged_devices()` → generator of
  `{vendor_id, product_id, name}` for *plugged, supported* devices (respects
  `RIVALCFG_PROFILE` env for testing). Fixes D1/D2 directly.
- `rivalcfg.devices.get_profile(vid, pid)` → full structured profile dict:
  `settings` (each with `label`, `cli`, `value_type`, `value` metadata like
  `input_range`, `choices`, `buttons` incl. per-button offsets/ids/defaults,
  `max_preset_count`), `battery_level`, `save_command`, `firmware_version`
  (optional — absent for Aerox 5 Wireless, present for Rival 3), `endpoint`.
- `rivalcfg.mouse.get_mouse(vid, pid)` → `Mouse` with generated
  `set_<setting>()` methods, `battery()`, `firmware_version`,
  `reset_settings()`, `save()`, `close()`, context-manager support,
  `command_delay` (0.05 s default).
- Example caps derivable for Aerox 5W (from `devices/aerox5_wireless_wired.py`):
  sensitivity `input_range [100, 18000, 100]`, `max_preset_count 5`;
  polling choices `{125,250,500,1000}`; sleep timer `0–20 min` (default 5);
  dim timer `0–1200 s` (default 30); buttons 1–9 + scroll up/down with ids and
  offsets; `default_lighting` choices `off|reactive|rainbow|reactive-rainbow`
  (default rainbow); `reactive_color` default `off`.
- CLI stays as *fallback* (the PR #1 `--help` parser) when the library import
  fails or internal APIs break — wrapped behind the same caps interface.

---

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
  setting in rivalcfg. Suspected cause of "colors don't match the picker"
  reports — verify with pure primaries before claiming gamut limits.

### Decisions (locked)

- Device metadata: **library-import primary, CLI-scraping fallback.** Import
  rivalcfg's device profiles for structured caps; fall back to the `--help`
  parser from PR #1 when import fails.
- Button art: **Cairo-drawn schematics**, per-device layout registry (views,
  anchors in 0–1 relative coords). Start with **Aerox 5 only** (top +
  left-side views). Keep the anchor→leader-line→label mechanism; redesign
  the label look (human-readable assignment chips, no raw
  `button1`/`scrollup` internals, no overlaps).
- Drop the auto-clicker and the setuid helper entirely. Document Wayland
  limits instead.
- PR #1 (closed as superseded) is the Phase 0 baseline; its detection/flags/
  DPI/button7-9 fixes carry over as the floor.

---

## Phases

### Phase 0 — Baseline correctness (no new features)

Goal: make current behavior match §1's locked semantics before any
refactor lands on top.

Tasks:
1. **Fix apply order everywhere** (W1, W3, W5): canonical chain is
   zone colors → reactive color → default lighting → rainbow flag last.
   Touch: `on_apply_rgb` (`:1994-2064`), `apply_all_to_device` extras
   (`:3108-3113`), factory-reset chain (`:3607-3641`).
2. **Fix rainbow-off** (W8): toggling rainbow off re-applies all zone colors
   (not just z1) then default-lighting.
3. **Fix double-write** (W2): remove the stray unconditional
   `run_rivalcfg(args)` at `:3163`.
4. **Factory reset**: re-send `--reactive-color off` in the Aerox chain (W5).
5. **Guard profile keys** (P1): make the non-aerox buttons-arg builder use
   `.get()` with the same defaults as `build_buttons_arg()`, or better: delete
   the inline builder and call `build_buttons_arg(mapping)` from
   `apply_all_to_device()` (single source of truth).
6. Re-run the 8-cell §1 matrix on hardware after each fix; record results in
   an append-only verification log under §1.

Acceptance: 8/8 matrix rows reproduce §1; profile switch issues exactly one
command burst (verifiable via `--no-save` + log inspection); no KeyError with
a hand-trimmed profile JSON.

### Phase 1 — Device core (`device_core.py`)

Goal: one module owns device identity, capabilities, and all writes.

New file `rivalcfg-gui/device_core.py` with:

1. **DeviceManager**
   - `list_plugged()` → `[Device]` via `rivalcfg.devices.list_plugged_devices()`
     (library primary). Each `Device` = identity (vid, pid, name, endpoint) +
     capability model.
   - Capability model per device, derived from the profile `settings` dict:
     `dpi (min, max, step, max_presets, first_preset)`, `polling_choices`,
     `zones` (list of color flags + labels), `effects` (light-effect choices
     where present), `has_reactive`, `has_rainbow_flag`, `has_default_lighting`
     (+ its choices), `has_battery`, `has_firmware`, `sleep_timer (min,max)`,
     `dim_timer (min,max)`, `has_led_brightness`, `buttons` (names, ids,
     defaults, supported action classes: button/dpi/scroll/disable/keyboard/
     multimedia). Everything the UI gates on comes from here — no flag
     sniffing in UI code.
   - CLI fallback: if `import rivalcfg` fails or profile introspection raises,
     reuse the Phase-0 `get_device_caps()` help-parser as the caps provider
     (keep `get_device_caps` as the fallback shim; UI talks only to
     `device_core`).
   - Hotplug: poll `list_plugged()` on a slow GLib timer (e.g. 3 s) or udev
     event via GLib.FileMonitor on `/dev/hidraw*`; update status bar +
     capability-gated pages on change. (Polling first — simpler and
     Wayland-safe.)

2. **CommandQueue (single writer)**
   - One background worker thread + `queue.Queue`; all rivalcfg invocations go
     through it (W6). GUI never spawns `subprocess` directly; `run_rivalcfg`
     becomes a thin enqueue wrapper (keep the name during migration).
   - **Debounce/coalesce**: keyed pending-state per setting — a slider tick
     updates the pending value and (re)starts a ~300 ms timer; only the final
     value is sent. Kill per-tick `run_rivalcfg` in `on_dpi_changed`/
     `on_spin_changed` (`:1639-1663`).
   - **ApplyPlan**: an ordered list of commands (one user action = one plan).
     The queue executes plans atomically: stops on first failure, reports the
     *failing command* to the status bar, one status update per plan (W7).
     Canonical lighting order enforced in one place (`build_lighting_plan()`),
     so W1/W3/W5-class bugs cannot reappear per-page.
   - Dry-run flag for plans (`--no-save` checkbox stays, plumb through).

3. **UI wiring**
   - Status bar reflects queue state: idle / running(plan label) / error(plan
     step + stderr).
   - Capability-gate every page section (hide firmware button where
     `has_firmware` is false — fixes D3's lie; hide battery/sleep/dim until
     Phase 4 pages exist).
   - DPI page ranges/steps from caps (`:1616-1634`); polling radios from
     `polling_choices` (`:1727`).
   - Remove `get_device_caps()` call sites from pages; keep function as
     fallback only.

Deliverables: `device_core.py` (+ `tests/test_device_core.py` with a fake
rivalcfg module), migrated pages (DPI, polling, RGB, buttons apply paths).

Acceptance: with a mock rivalcfg, plans enqueue in canonical order; slider
drag produces exactly one write after release; two rapid Apply clicks produce
two serialized plans, never interleaved subprocesses.

### Phase 2 — RGB page redesign (Aerox 5 first)

Rebuild `create_rgb_page()` for the aerox family:

- One **lighting card**:
  1. **Zone colors** — 3 rows (top/middle/bottom) with a horizontal strip
     preview widget showing the 3 zones side by side.
  2. **Reactive row** — standalone: Off switch + color picker, no coupling to
     anything else (click-flash iff programmed — §1 conclusion).
  3. **Wake dropdown** ("on wake") — all 4 `default-lighting` values
     (`off|reactive|rainbow|reactive-rainbow`) sent verbatim; labeled as
     wake-only so users stop expecting immediate effect (§1: no immediate
     effect on steady colors).
  4. **Rainbow toggle** — single checkbox, always sent LAST in the plan.
- No cross-dependencies between the four groups in either direction.
- Rival 3 branch: keep current strip/logo/light-effect radios but render from
  caps (zones list, effect choices); same card layout so the page has one design.
- All sends go through `build_lighting_plan()` from Phase 1 — Apply button
  builds one plan, not a callback chain.

Acceptance: §1 matrix still 8/8 after redesign; wake dropdown changes nothing
immediately (verified by observation); rainbow survives a default-lighting
apply; zero `run_rivalcfg` calls outside the queue.

### Phase 3 — Buttons page redesign (Aerox 5 first)

Replace the `rival3.png` overlay with **Cairo-drawn schematics** (§1 Decisions):

1. `rivalcfg-gui/layouts/` (or dict registry in `device_core.py`): per-device
   layout entries — for Aerox 5 first: `views: ["top", "left"]`, each with
   button anchors in 0–1 relative coordinates (button1–9, scrollup/down,
   scroll wheel body), label anchor points, and leader-line routing hints.
2. Cairo `DrawingArea` draws the mouse outline + button shapes from the
   registry (crisp at any DPI/scale; no bitmap).
3. Clicking the *button shape* (not a label chip) opens the assignment
   popover — hit-test against drawn geometry (fixes B2). Buttons 7–9 and
   scroll up/down all clickable and labeled.
4. Labels: human-readable assignment chips ("Left click → Button 4",
   "Scroll up → Volume up"), laid out to not overlap; leader lines retained.
5. Popover gains actions rivalcfg actually supports, derived from caps
   (fixes B3): current button/dpi/scroll/disable set **+ keyboard action**
   (`anykey(key=…; layout=qwerty)`) **+ multimedia** (play/pause, prev,
   next, vol+, vol−, mute). Keyboard action needs a key-capture entry and a
   layout selector (default qwerty).
6. Side view for Aerox 5 (buttons 7–9 live on the left side); view switcher.
7. Buttons page keeps working for Rival 3 via a rival3 layout entry added in
   the *same registry* (follow-up, after Aerox 5 proves the pattern —
   Q1). Until then Rival 3 renders the top view from its registry
   entry (6 buttons, no side view).

Acceptance: all 9 buttons + scroll assignable on hardware; a keyboard action
and a multimedia action verified end-to-end on the Aerox 5W; popover options
match caps (no fake options, no missing real ones).

### Phase 4 — Devices + Power pages

1. **Devices page** (`create_devices_page` rewrite, fixes D2): list only
   plugged devices from `device_core.list_plugged()` — name, VID:PID,
   endpoint, firmware (only where `has_firmware`), battery (where
   `has_battery`). Refresh button re-enumerates. "Supported but not plugged"
   collapses into a footnote link to rivalcfg's device list.
2. **New Power page**: battery level (+ charging state where
   the profile exposes it — `battery_level.is_charging` flag), sleep timer
   (0–20 min, 0 = disable), dim timer (0–1200 s, 0 = disable). Sliders use
   the queue's debounce; spin+slider pairs like DPI.
   - Include the Q4 note in-page: dim timer dims LEDs on idle and is the
     suspected cause of "colors don't match the picker" — add a hint text
     ("Set dim timer to 0 while comparing colors").
3. LED brightness row here when `has_led_brightness` (Rival 3 does not have
   it in rivalcfg either — gate on caps, hide when absent).

Acceptance: Devices page shows only plugged hardware (test: unplug/replug);
Power page battery/sleep/dim verified on the Aerox 5W; brightness row absent
for Aerox 5W (correct — unsupported).

### Phase 5 — Behavior: consent, profiles, auto-clicker removal

1. **No writes without consent** (W4): remove the
   `refresh_profile_selector → select_profile → apply_all_to_device` path at
   startup; auto-select may update *UI state* only. Same for `rebuild_ui`.
   Explicit Apply (or auto-apply on change, user-enabled) are the only write
   triggers. On profile switch: load into UI, mark status
   "Loaded profile X — press Apply to send", never touch the device.
2. **Auto-clicker + setuid helper removal** (A1): delete `MacroEngine`,
   `evdev_helper.c`, `_WL_KEYCODE_TABLE`, X11/Wayland keycode code,
   `_find_keyboard_device`/`_find_mouse_device`, `pynput`/`evdev`/`Xlib`
   imports, macro settings keys, macro UI card, Hyprland macro-bind row
   (A4), `pkill -SIGUSR1` hook. Update README (features list, requirements
   table, Wayland limits note) per §1 Decisions ("document Wayland limits
   instead").
3. **Profile schema v2 + migration** (P2): `{"schema": 2, "device_hint":
   {vid,pid}, "settings": {...}}`; loader validates and migrates v1 files
   (drop `macro_*`, `.get()` everything, clamp DPI to caps, validate button
   targets against caps). Invalid profile → status warning, never a crash.
   Load-time validation happens before any UI state is touched.
4. Keep: `--no-save` checkbox, factory reset (now a proper plan + UI state
   reset), profiles save/load/rename/delete UI.

Acceptance: launch and language switch produce **zero** HID writes
(log-verified); v1 profiles load cleanly; deleting `evdev_helper.c` and macro
code leaves no dead references (grep: `macro|pynput|evdev|Xlib|SIGUSR1`).

### Phase 6 — Packaging, tests, CI, i18n

**Status: implemented** — see the Phase 6 entry in `WORKLOG.md` for what shipped
and what was deliberately left open. Notes on the plan as written: the package
migration in step 1 was taken (not deferred), releases are documented rather than
automated (step 7 — a version-agreement check, no tag-triggered workflow), and
step 4's distro *build* checks were not runnable on the dev machine (no
`dpkg-buildpackage`/`flatpak-builder`), so those two recipes were verified by
inspection.

**Superseded in part — distro packaging dropped.** Since this repository is a
fork and not the upstream maintainer, the `dist/` recipes (AUR, Debian, Flatpak),
`RELEASING.md` and the version-agreement checker (`tools/check_versions.py`,
`tests/test_versions.py`) and the CI `versions` job were **removed**. The checker
existed only to keep four copies of the version in sync; with three of those
files gone it had nothing left to compare. Steps 2, 4, 6 and 7 below describe the
package as it was built at the time and no longer apply. What remains — the pip
install path, the `packaging` CI job that asserts assets and catalogs land in the
installed distribution, `tools/check_locales.py` — is unaffected. See the
2026-10-06 entry in `WORKLOG.md`.

1. **setup.py**: `console_scripts` entry point
   (`rivalcfg-gui = rivalcfg_gui:main`), `package_data` for `assets/` +
   `locales/**/*.{mo}`, trim `install_requires` to `rivalcfg` (+ nothing
   else — GTK via system). Consider migrating the single-file app to a small
   package (`rivalcfg_gui/` dir) as part of this, since `device_core.py`
   already breaks the single-file assumption (§0.2/K1).
2. **Distro files**: drop helper builds from AUR PKGBUILD (gcc makedep, build
   step, `.install` scripts), debian rules (gcc/wget flow), and flatpak yml
   (evdev_helper module; evdev/pynput/python-xlib/six from
   `python3-requirements.json`). Align all three with the Phase-5 feature set
   and the new entry point.
3. **Tests** (pytest, mocked rivalcfg — no hardware in CI): queue ordering +
   serialization, debounce/coalesce, canonical lighting plan (the §1 matrix
   as table-driven tests), caps derivation from a real profile fixture
   (aerox5_wireless_wired), CLI-fallback parser, profile migration v1→v2,
   plan failure reporting.
4. **CI** (GitHub Actions): lint (ruff/flake8), pytest, `msgfmt -c` all locales,
   build check for each distro target where feasible (AUR via namcap dry-run,
   flatpak via flatpak-builder in a container). **Latest-rivalcfg job**: a
   scheduled/PR job installing rivalcfg from git master to catch internal-API
   breakage early; the CLI-fallback path is our safety net.
5. **Renovate/Dependabot** on the rivalcfg pin; fail the CI job above loudly
   when the library moves.
6. **Locales**: regenerate `.mo` in CI; refresh `.po` headers; re-translate
   the delta (de currently 132/140); add a CI check that `.po` msgid sets match
   the reference en catalog so locales can't silently rot.
7. Tag releases; AUR/deb/flatpak version bumps automated or documented.

Acceptance: `pip install .` yields a working `rivalcfg-gui` command with
assets/locales; CI green with mocked hardware; scheduled job exists; no
setuid binary anywhere in any package.

---

## Suggested sequencing & rough size

| Phase | Focus | Depends on | New/changed files |
|-------|-------|------------|-------------------|
| 0 | Order/write bugfixes | — | `rivalcfg_gui.py` (4 spots) |
| 1 | `device_core.py`, queue, caps | 0 | `device_core.py`, most pages |
| 2 | RGB redesign | 1 | `create_rgb_page` (+tests) |
| 3 | Buttons redesign | 1 | layouts registry, `create_buttons_page` |
| 4 | Devices + Power | 1 | 2 pages |
| 5 | Consent + profiles + macro removal | 1 (safe after 2–4 land) | `rivalcfg_gui.py` wide, delete `evdev_helper.c` |
| 6 | Packaging/tests/CI | all | `setup.py`, `dist/**`, `.github/`, `tests/` |

Phases 2/3/4 are parallelizable after 1; 5 is the risky wide-delete — do it
after the UI work to avoid merge churn. Every phase ends with the §1 matrix
re-run on hardware.

## Open questions (carried forward and updated)

- Q1. Rival 3 + other family layouts (Phase 3 registry entries) — after
  Aerox 5 proves the pattern. Same for side views per family.
- Q2. Wake-behavior verification (`reactive` vs `reactive-rainbow` at wake)
  needs a sleep-timer-driven test — schedule after Phase 4 makes the sleep
  timer settable from the UI (today: not run; matrix says no difference for
  the immediate state).
- Q3. Hyprland card scope after macro removal: keep `hyprland_mouse_sync` +
  `hyprland_follow_mouse` (proposed — they're WM conveniences, not clicker)?
  Or trim the whole card to reduce surface? Decision needed before Phase 5.
- Q4. ~~Dim-timer vs "colors don't match picker": verify with pure primaries
  (per §1) once the Phase 4 dim-timer row exists; if confirmed, the RGB page
  gets a permanent hint and the FAQ entry.~~ **Resolved.** Confirmed on hardware:
  the 30 s dim timer was the cause, and pure `ff0000` renders correctly at
  dim-timer 0. The in-page hint shipped ("Set 0 while comparing colors.") and the
  Power page exposes the timer; the user reports it is no longer a problem. The
  "FAQ entry" half was **not** done — the README has no FAQ section; the
  behaviour is covered in the Features list instead. See the WORKLOG's dim-timer
  note.
- Q5. Device switching UX with multiple plugged SteelSeries devices:
  Phase 1's DeviceManager must *represent* several, but which one gets
  applied? Propose: device selector in the sidebar when >1 plugged, default
  first. **Decided — deferred (future feature).** The proposal is agreed as
  written and is *not* to be built now; a single plugged device is the
  supported case. Recorded here so the next session does not re-open it as an
  unknown, and so the sidebar can be shaped to leave room for it.
- Q6. Generalising the 3D mesh pipeline to other mice: see the future-feature
  entry below. **Decided — deferred.** Not scheduled, and not started.

---

## Future features (agreed, not scheduled)

Two items are agreed in shape and deliberately **not** being built. Neither is
an open question — do not re-derive them, and do not start them without being
asked.

### 1. Device picker for several plugged mice

As Q5 above: a sidebar device selector, shown only when more than one
SteelSeries device is plugged, defaulting to the first. The sidebar should be
shaped to leave room for it. One plugged device is the supported case today.

### 2. AI-assisted mesh pipeline for further mice

Today only the Aerox 5 has geometry, and getting there was bespoke work: a
hand-written solid model (`aerox5_3d_files/model.py`), an extractor tuned to its
output (`tools/extract_aerox5_v3_mesh.py`), and hand-fitted correction tables
(`tools/fit_seam_correction.py` and the `_SEAM_FIX` / `_TAIL_CUT_MM` /
`_LEAD_CUT_MM` tables in `mouse3d.py`). `mouse3d.py` imports the Aerox 5 mesh by
name. None of the *inputs* are in the repository — `aerox5_3d_files/` is
gitignored, so the chain cannot be re-run from a fresh clone; only its output
ships.

The agreed shape is to fix the **interfaces** rather than write another
per-mouse model:

1. **A per-mouse description file** — dimensions, thumb side, button inventory,
   light-zone count and stations, wheel position. Replaces the constants now
   scattered through `mouse3d.py`.
2. **Silhouette** — the outline data, traced off reference views (what
   `profiles_smooth.npz` is) with a vision model proposing a first draft.
3. **Silhouette → solid OBJ** — one generic loft/honeycomb/seat builder. Its
   output uses a **fixed part vocabulary** (`shell`, `honeycomb`, caps by button
   number, `wheel`, skates); that vocabulary is the contract, and it is what
   lets a *scanned* mesh enter the same slot unchanged if scanning ever becomes
   available.
4. **OBJ → runtime data** — one extractor, device-driven, writing
   `rivalcfg_gui/meshes/<mouse>.py` (pure data, as today).
5. **Markup corrections** — generalise `fit_seam_correction.py` to take
   (screenshot, view, which edge) and write into the per-mouse module. This part
   cannot be automated away: a new mouse needs its own markup pass.
6. **Review loop** — parameterise `tools/preview_mouse3d.py` by mouse, and feed
   markups back into step 5.
7. **Device-parameterised `mouse3d.py`** — take (mesh data, description) instead
   of importing one mouse.

Where AI genuinely helps: reading dimensioned drawings and photos into a
first-draft description file, proposing loft control points, and flagging where a
render disagrees with a reference photo. Where it does not: producing a clean,
correctly-named, watertight OBJ in one shot.

**Order and acceptance.** Do the interfaces first (description file, vocabulary,
device-parameterised renderer) — all testable without owning a second mouse — then
port the Aerox 5 onto them, where the acceptance test already exists: the
generated mesh data must come out **byte-identical** to what is committed. Add a
second mouse (Q1's Rival 3) only after that passes.