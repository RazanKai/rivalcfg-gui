# WORKLOG.md — rivalcfg-gui overhaul

Append-only record of what has been implemented, verified and left open.
Source of truth for intent is `PLAN.md`; this file tracks execution status.

Test hardware: SteelSeries Aerox 5 Wireless (`1038:1852` wireless / `1038:1854` wired).
Test OS: CachyOS + Hyprland/Wayland.

---

## Status at a glance

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Baseline correctness (order, double-write, reset, KeyError) | **Done** |
| 1 | `device_core.py` (caps + CommandQueue + plans) | **Done** |
| 2 | RGB page redesign | **Done** (reworked twice after feedback) |
| 3 | Buttons page redesign | **Done** (Cairo → 3D wireframe after feedback) |
| 4 | Devices + Power pages | **Done** |
| 5 | Behavior: consent, profiles, macro/setuid removal | **Partially done** (see below) |
| 6 | Packaging, tests, CI, i18n | **Not started** |

Tests: **66 passing** (`python -m pytest tests/`), no hardware required.

---

## New / removed files

Added:
- `device_core.py` — device identity, capabilities, `CommandQueue`, `ApplyPlan`,
  plan builders, profile migration.
- `mouse3d.py` — 3D wireframe model of the Aerox 5 (shell + surface buttons + projection).
- `widgets.py` — GTK widgets: `ColorSwatch`, `ColorEditor` (HSV gradient + hue + hex + palette).
- `colorutil.py` — pure colour helpers (hex ↔ RGB ↔ HSV), GTK-free so they test headlessly.
- `tests/` — `test_device_core.py`, `test_mouse3d.py`, `test_widgets.py`.

Removed:
- `evdev_helper.c` (setuid input helper).
- `layouts.py` (old 2D Cairo button registry), `tests/test_layouts.py`.
- `MacroEngine`, `_WL_KEYCODE_TABLE`, X11/Wayland keycode resolution,
  `_find_keyboard_device`/`_find_mouse_device`, `pynput`/`evdev`/`Xlib` imports.
- `Gtk.ColorButton` usage (replaced by the custom picker).

---

## Phase 0 — Baseline correctness (done)

All write ordering now flows through one place (`device_core`):

- **W1/W3/W5 — canonical apply order** locked as
  *zone colours → reactive colour → default lighting → rainbow last*, enforced by
  `build_lighting_plan()` / `build_full_plan()`.
  Note: each order-sensitive phase is emitted as its **own rivalcfg invocation**,
  because rivalcfg processes flags in profile-definition order, not CLI order.
- **W2 — double-write** removed (one plan per user action).
- **W8 — rainbow-off** re-applies all zones via the plan (not just z1).
- **W5 — factory reset** re-sends `--reactive-color off` as part of one plan.
- **P1 — KeyError** eliminated: single `build_buttons_arg(caps, mapping)` used by
  every apply path, `.get()`-safe and capability-driven.

---

## Phase 1 — Device core (done)

`device_core.DeviceManager`:
- library-first via `rivalcfg.devices.list_plugged_devices()` / `get_profile()`;
- CLI `--help` fallback parser kept as the safety net;
- `DeviceCaps` model: DPI range/step/presets, polling choices, zones, reactive,
  rainbow (flag or choice), default lighting, light effect, led brightness,
  buttons (ids/offsets/defaults/keyboard/multimedia), sleep/dim timers, battery,
  firmware.

`device_core.CommandQueue`:
- one background worker; **all** rivalcfg invocations go through it;
- atomic `ApplyPlan`s (stop on first failure, report the failing step, one status
  update per plan);
- `enqueue_debounced()` coalesces slider/spin ticks (~300 ms);
- `_run_rivalcfg_sync()` is the only place a subprocess is spawned.

GUI wiring:
- status bar reflects queue state;
- every page capability-gated;
- hotplug: `list_plugged()` polled on a 3 s GLib timer, updates status + Devices page.

---

## Phase 2 — RGB page (done, reworked)

Capability-driven layout:
- zone colour rows with a vertical **strip preview** below the picker that mirrors
  the real mouse (three LEDs stacked top→bottom); click a segment to select that zone;
- **reactive** row is standalone (on/off switch + colour), decoupled from everything;
- **on-wake** dropdown (`default-lighting`) with a wake-only hint;
- **rainbow** toggle, always sent last in the plan;
- light-effect radios rendered from caps (Rival 3 class);
- hint about the dim timer ("set to 0 before comparing colours").

Colour editing was rebuilt twice after user feedback:
1. A palette row of 8 true LED primaries (Red/Green/Blue/White/Y/M/C/Orange).
2. Replaced the OS `Gtk.ColorButton` and then the popover with an **embedded
   `ColorEditor` panel** occupying the empty right column of the page:
   - proper Cairo gradients (saturation/value area + hue strip),
   - **hex entry**,
   - **shortcuts palette**,
   - live preview swatch; click a zone/reactive swatch to choose the edit target.
   Verified: gradient→`ff0000`, palette→`0000ff`, typed hex→`abcdef`, all applied
   to the selected target.
- Removed the redundant per-row preview swatches (the selectable swatch is the preview).

---

## Phase 3 — Buttons page (done, reworked)

First attempt used Cairo 2D schematics (top + left views). Rejected by the user as
not looking like the mouse. Replaced with a **3D wireframe**:

`mouse3d.py`:
- shell modelled as 17 cross-section rings (16 points) lofted along the length +
  longitudinal lines; proportions traced from the SteelSeries manual/product
  schematics (low pointed front, broad rounded palm hump; ~128.8 × 68.6 × 42.1 mm);
- buttons are polygons that **follow the top/side surface**;
- orthographic projection with yaw/pitch; `fit_view()` scales/centres to the area;
- screen-space hit-testing (`point_in_polygon`, smallest overlapping polygon wins).

Rebuilt from an extracted OBJ mesh (later sidebar session):
- `aerox5_mesh.py` holds `RINGS` (shell loft), `RIMS` (real honeycomb holes /
  cut-outs), `CAPS` (buttons that exist as separate mesh groups), `WHEEL`, and
  `GRID_TOP` / `GRID_SIDE` bilinear lookup grids (see
  `tools/extract_aerox5_mesh.py`).
- `mouse3d.render()` draws shell + details (wheel cylinder, seams, hole rims) +
  buttons with **backface culling** (`_seg_facing` / `_stroke_facing`); the GUI
  calls it directly. Remaining parametric buttons (click panels, scroll zones,
  7/8 rocker) are snapped onto the real surface via the grids.

Dev aid: `RIVALCFG_GUI_START_PAGE=<page>` opens the app on a given page
(`dpi`/`polling`/`rgb`/`buttons`/`devices`/`power`/`settings`/`about`); default `dpi`.

Follow-up fix (side buttons):
- side-surface features now use heights as **fractions of the local shell height**
  (`_side_point`/`_side_frac_quad`/`_side_poly`) instead of absolute z —
  absolute heights wrapped over the crown where the shell gets lower towards
  the nose (the rocker quad reached the top ridge and distorted into the click
  panel area), and the thumb-rest crease had the same defect;
- side buttons re-seated per the manual layout: thumb row (4/5) in the lower
  third of the flank, up/down rocker (7/8) above the front thumb button,
  forward trigger (9) low at the nose; creases re-drawn to frame that panel;
- label chips now **track their anchor height** (order-preserving gap
  relaxation) instead of spreading across the full canvas height, so leader
  lines stay short and never cross.

Follow-up fix (click panels vs. side buttons):
- the click panels' outer skirt used absolute x extents that descended the
  flank to ~50 % of the local height, overlapping the side-button cluster in
  both the 3D and side views.  The skirt now follows a **constant dome-height
  fraction** (`_top_edge_x()`, ~0.79 ≈ the shell parting line; 0.62 at the
  nose where the shell is all button), keeping clear of every side button;
- the rocker (7/8) and thumb row (4/5) shifted down slightly so there is a
  visible gap between the cluster and the click panel skirt.

Follow-up fix (click panels converge + wrap down the front):
- the click keys were near-rectangular slabs whose outer edge stayed high
  (~0.84–0.92 of local height) all the way to a blunt nose, so the front
  floated above the low tip (visible in the side view) and the two keys did
  not taper toward the nose (visible in the top view).
- rebuilt the outline from a shared right-half profile mirrored to the left,
  so the real shell's slight asymmetry can no longer make the two keys differ
  in height: the leading edge hugs the **nose** (high dome fraction 0.95 at
  y −0.470, wrapping down to z 0.167) and eases to the parting line (0.76–0.77)
  round the middle of the button, giving a monotonic outward taper;
- the panels now stop at the wheel slot (y ≈ −0.20), so the front assembly is
  the low ramp + wheel as in the product shots, not a tall fin over the palm
  hump; the scroll up/down arrows moved into the wheel slot to match;
- the wheel arc now reaches further past bottom-dead-centre on the front side
  (`tmax + 74°`, clamped at 150°) so the roller visibly dives toward the nose;
- new tests pin the convergence (`test_click_panels_converge_toward_the_nose`)
  and the front drop (`test_click_panels_wrap_down_the_nose`).

Mesh-based rebuild (supersedes the hand-tuned geometry above):
- `aerox5_mesh.py` (generated, ~88 KiB) is extracted from a detailed,
  dimensioned OBJ model of the Aerox 5 by `tools/extract_aerox5_mesh.py`
  (offline, numpy-only; the OBJ itself is not shipped):
  - shell = 26 cross-section rings sliced from the mesh, convex-hulled per
    slice (drops interior geometry caught by the cut) and arc-length
    resampled to 48 points;
  - honeycomb = **real hole rims** (edges where hole-wall faces meet the
    outer skin) + wheel/nose cut-out rims + the shell/base rim as a seam;
  - buttons 4/5/6/9 = the physical button cap outlines (OBJ groups);
  - click panels, scroll zones and the 7/8 rocker stay parametric but are
    snapped onto the real surface through bilinear top/flank lookup grids.
- real placements replaced the guessed ones: thumb buttons sit at the middle
  of the body (y −0.086…+0.253), the trigger is the front-low "silver bar"
  element, the wheel/DPI geometry is measured from the mesh.
- runtime stays dependency-free: `mouse3d.py` renders only from the
  extracted data; GTK/API (`View`, `fit_view`, `render`, `BUTTONS`,
  hit-testing) unchanged.

Page:
- views: **3D / Top / Left side**;
- click a button facet → assignment popover (mouse buttons, DPI, scroll, disable,
  **keyboard keys**, **multimedia keys**);
- human-readable chips with leader lines; per-side non-overlapping stacking.

Button mapping follows the official rivalcfg schema / manual legend:
`Button1/2` clicks, `Button3` wheel click, `Button6` DPI (CPI), `Button4/5` side
buttons, `Button9` forward trigger, `Button7/8` = the split up/down flick switch.

Known limitation: the 3D model is Aerox 5-specific. Other devices show this shape
but only expose their own button keys (per PLAN: "start with Aerox 5 only").

---

## Phase 3 follow-up — corrected source model (mouse3d / aerox5_mesh)

The original `aerox5_detailed.obj` (traced approximation) had three defects
that propagated into every button facet and the hole rims:

- **Main mouse button** was only implicit in the shell (no keycap split,
  no wrap down the low nose).
- **Side buttons** were wrong in shape and placement: a mid-height band of
  boxes, with the thumb pad / trigger too low and mis-sized.
- **Skates** were four separate rectangular pads instead of the real three
  PTFE pieces.

Fixed by rewriting `aerox5_model.py` (in `~/Downloads`) into a full detailed
builder that emits the same group vocabulary the extractor consumes:

- `shell` + `honeycomb` + `hole_rims` (real hexagonal cut-outs and their
  rim loops), `base` (flat underside + shell/base seam);
- `main_button_left/right` — raised keycaps that taper toward the nose;
- left-flank controls: `side_button_front/rear` (thumb pair),
  `rocker_up`/`rocker_down` (flick lever), `trigger` (front thumb) and
  `thumb_pad`;
- `skate_front`, `skate_rear`, `skate_sensor` — the three real PTFE pieces
  (wide curved front, wide curved rear, sensor ring);
- `wheel`, `dpi_button`, `sensor`, `onoff_switch`, `usb_c`, `rgb_strip`.

The shell silhouette is reproduced exactly from the dimensioned reference
(station table + a 96-point unit cross-section), so only the broken parts
changed.

`tools/extract_aerox5_mesh.py` now reads the explicit groups (rings from
`shell`+`honeycomb`, rims from `hole_rims`, full convex-hull cap outlines
for buttons 4/5/6/7/8/9 and both keycaps) and emits a slightly larger
`aerox5_mesh.py`.  `mouse3d.py` consumes the real outlines for buttons
1/2/4/5/6/7/8/9 (the old parametric click-panel / side-cap helpers were
removed).  All 56 tests pass.

---

## Phase 3 follow-up — honeycomb / keycap / lever corrections

User feedback on the mesh rebuild: "the mesh is wrong, it's not a matrix of
rectangles it's a honeycomb of quadrilaterals; the R/L buttons and the side
lever are shaped wrong; the lever is one button that can be pressed two ways;
there's overlap between buttons in some places."

Root cause found: the **shell end-cap fans were appended to the `hole_rims`
group**, so the extractor read the closed end-cap boundary chains as ~480
"rims" and rendered them as stray triangles over the honeycomb (the dense
horizontal lines and the chevrons at nose/tail).  The previous session's
diamond-lattice edits were otherwise in place.

Fixes:

- `aerox5_model.py`: end caps are closed with the `shell` group, never
  `hole_rims` (also `build_shell` now emits two `shell` groups, which is fine
  because the extractor concatenates group faces).
- `tools/extract_aerox5_mesh.py`:
  - `triangles()` fan-triangulates mixed tri/quad/n-gon faces; all group
    vertex selection goes through it (the OBJ now has triangles in `shell`);
  - base seam is taken from the **base-plate** outer boundary (the closed
    shell no longer has a free base loop);
  - `hull_xy_surface()` builds the keycap outline as a convex hull in plan
    view but keeps the real per-point shell height, so the keycap wraps down
    the low nose without the self-intersecting boundary walk;
  - side caps (4/5/6/7/8/9) keep the stable (y,z)/(x,y) projection hull.
- `aerox5_model.py` side controls: the up/down flick is **one rounded lever
  outline clipped at mid-height** into two exactly-tiling halves
  (`_clip_half`), so buttons 7/8 read as a single rocker; the front trigger is
  reseated (`12..42` mm) so it no longer dips below the rising shell bottom.
- `mouse3d.py`:
  - removed the oversized thumb-rest seam that crossed the side cluster;
  - removed the two scroll **arrow** facets (`scrollup`/`scrolldown`) that
    overlaid the wheel — they remain popover assignments, not drawn facets;
  - removed now-dead `_top_poly` / `_side_poly` / `_arrow_tri` helpers.
- Tests updated to the real geometry: monotonic keycap nose taper, tinylift
  tolerance on the flank bounds, and a new test that scroll directions are
  not drawn as overlapping facets.

Verification: 26 shell rings, **294 diamond rims (all 4-point)**, clean
staggered honeycomb, no stray end-cap geometry; 57 tests pass; offscreen
`Gtk.OffscreenWindow` render of the Buttons page draws correctly.

### Keycap centre-split channel

Follow-up: the centre split must only meet at the centreline until the wheel
starts, then part to clear the scroll wheel and the CPI button.

- `aerox5_model.py` `_split_u()`: the inner edge is the centreline at the
  nose, ramps out from `SPLIT_Y0=9` to a wheel half-gap `0.15` by the wheel
  slot, then eases to `0.12` behind the wheel to clear the CPI button.
- `build_main_buttons()` also emits an explicit **ordered** boundary
  (`main_button_*_outline`, a triangle fan) because the channel is concave --
  the extractor's convex hull would swallow it.
- `tools/extract_aerox5_mesh.py` `explicit_outline()` recovers that ordered
  loop (fan-hub dedup) and emits `CAP_OUTLINES`; `top_cap()` prefers it.
- `mouse3d._cap_on_surface()` uses `CAP_OUTLINES` when present.
- Tests: caps meet at the centreline in front of the wheel, clear the wheel
  and CPI footprints, and show a centre channel behind the wheel. **60 pass.**

### Honeycomb scale / gradient and side controls

Feedback: the honeycomb was too fine and uniform; the real holes are bigger,
get larger top→bottom, cover the keycap's lower edge, and have only thin
ribbing. The lever should be longer (spanning both thumb buttons), and the
thumb buttons should be adjacent.

- `aerox5_model.py` honeycomb rewritten in **top-angle space** (0 deg at the
  crown, 90 deg at the flank): a staggered diamond lattice whose spacing
  grows along both axes (`HOLE_DY0..1`, `HOLE_DT0..1`), built from a
  cumulative-spacing table so the coarse first holes never merge. Diamonds
  are `0.92x` their spacing (thin ribs).
- Holes now run from `HOLE_Y0=20` (forward, under the keycap's lower edge)
  and down to the base plate (`HOLE_FLANK=88`); forward of the keycap end
  only the lower flank is perforated (`KEYCAP_FLANK_MIN=74`).
- Shell grid raised to `NU=320, NT=180` so the coarser holes stay crisp.
- Side controls: buttons 4/5 adjacent at `y=75`, and the flick lever spans
  both of them (`54..98`, tilted to follow the tapering shell); still clipped
  at mid-height into two exactly-tiling halves.
- Tests: coarse hole count, crown→flank span, non-uniform hole size,
  perforation reaching forward under the keycap, lever spanning the thumb
  buttons, adjacent thumb buttons. **66 pass.**

---

## Phase 4 — Devices + Power (done)

- `create_devices_page()`: lists only plugged, supported devices (name, VID:PID,
  endpoint, battery/firmware where supported); Refresh re-enumerates; footnote to
  rivalcfg's full device list.
- `create_power_page()`: battery (**auto-polled**, once on open + every 60 s while
  visible), sleep timer (0–20 min), dim timer (0–1200 s, with the "set 0 to compare
  colours" hint), LED brightness row only where `has_led_brightness`.

---

## Phase 5 — Consent, profiles, macro removal (partial)

Done:
- **No writes without consent**: startup, language switch and profile selection only
  update UI state and show "Loaded profile X — press Apply to send". Explicit Apply
  or user-enabled auto-apply are the only write triggers (fixes W4/K3).
- **Auto-clicker + setuid helper removed** from code and packaging; README documents
  the Wayland limitation instead.
- **Profile schema v2 + migration**: `{"schema": 2, "device_hint": {...}, ...}`;
  `device_core.migrate_profile()` drops `macro_*`, defaults/clamps missing values and
  never raises on bad input. Load-time validation happens before UI state changes.
- Settings file no longer carries macro keys.

Not done (tracked for later):
- Full schema *validation* of button targets against caps on load is light-touch
  (keys are filtered, values are not fully validated).
- `hyprland_macro_bind` row removed; `hyprland_mouse_sync` + `hyprland_follow_mouse`
  kept (Q3 decision).

---

## Phase 6 — Packaging / tests / CI / i18n (not started)

Partially touched:
- `setup.py`: console entry point `rivalcfg-gui = rivalcfg_gui:main`, `package_data`
  for assets + locales, `install_requires` trimmed to `rivalcfg`, new modules listed.
- `requirements.txt` trimmed to `rivalcfg`.
- AUR PKGBUILD / .SRCINFO: dropped `gcc`/evdev/pynput/xlib, bumped to 1.6.0.
- Debian `control` + `rules`: dropped helper build and input deps; installs the new
  modules.
- Flatpak yml + `python3-requirements.json`: dropped the evdev_helper module and
  evdev/pynput/xlib/six.

Still open:
- GitHub Actions CI (lint, pytest, `msgfmt -c`, distro build checks, latest-rivalcfg job).
- Renovate/Dependabot on the rivalcfg pin.
- Locale refresh/regeneration and a msgid-parity check.
- Migrating the single-file app into a package layout (deferred; modules already
  break the single-file assumption).

---

## Hardware verification

- Tested against Aerox 5 Wireless in both wired and 2.4 GHz modes; `list_plugged_devices()`
  correctly reports `1038:1852` (wireless) / `1038:1854` (wired).
- The 8-cell §1 matrix (reactive × default-lighting × rainbow) is covered by
  table-driven tests (`test_device_core.py::test_matrix_*`) asserting the canonical
  order invariants. **Physical re-run of the matrix on hardware is still pending.**

### Open hardware question
"Colours look pink / washed out" was investigated. Diagnosis: the 30 s **dim timer**
dims idle LEDs, so pure `ff0000` is rendered reduced unless the dim timer is 0. The
Power page now exposes it and the RGB page links to it. A device-side write of
`--dim-timer 0` + pure primaries was issued for the user to confirm by eye; if it is
still pink at `ff0000`, the wireless command path (readback/patch) needs investigation.

---

## Test coverage (`tests/`)

- `test_device_core.py`: caps derivation from a real Aerox 5 fixture, §1 order matrix,
  buttons arg (incl. trimmed-profile safety), full-plan order, `DeviceManager`
  library/CLI fallback, queue serialization / atomic failure / debounce / one-status,
  profile migration v1→v2.
- `test_mouse3d.py`: station ordering, realistic bounds, wireframe structure,
  surface functions, button set/symmetry, projection fits in frame, hit-testing,
  view silhouettes.
- `test_widgets.py`: hex/RGB/HSV helpers and clamping (via `colorutil`, GTK-free).

---

## How to run

```bash
python -m pytest tests/      # 66 tests, no hardware
python rivalcfg_gui.py       # or: rivalcfg-gui (console script)
```

Regenerating the model data (numpy needed for both steps; the generated
module is pure data, so the app itself still has no build step):

```bash
python3 tools/aerox5_model.py build     # -> build/aerox5_detailed.obj (+ .stl)
python3 tools/extract_aerox5_mesh.py    # -> aerox5_mesh.py (byte-identical)
```

Manual smoke checks performed during development (GTK Broadway backend):
all 8 pages construct and draw; 3D facet click opens the assignment popover;
colour editor gradient/palette/hex all update the selected target.

---

## 2026-10-02 — Shell rebuild against the official views; three geometry bugs

The render "looked like a capsule" and the mesh read as a honeycomb of
quadrilaterals.  Checked the station table and the cross-sections against the
numbered official views and the photos first: both already matched to within
1–2 mm, so the *data* was not the problem — the renderer was.  Rebuilt the
perforation and the cap rendering.

**Honeycomb.**  The old `_mesh.RIMS` (144 thin 4-point slits, ~2.4 × 6 mm) was
replaced with a parametric lattice mapped onto the real shell.  Parameters
come off the official 1:1 top view (0.35543 mm/px): rounded-diamond openings
on a staggered lattice, nearest neighbours 3.45 mm apart, cell half-diagonal
2.2 mm, corner radius 0.55 mm.  Cells are laid out in arc-length coordinates
along each cross-section (`_ring_profiles`, 192 samples crown → base rim), so
they stay square under the shoulder wrap, and each is culled per cell.
Coverage: rear field from just behind the keycaps (54.5 mm) to the tail,
wrapping the crown and down the flanks — plus the two patches flanking the
wheel (29–50 mm).  Keycaps stay smooth.  257 cells.

**Three bugs found in the process.**  All three were the same class of
mistake — a profile walk that is only monotone from the *middle* of the body:

1. `_snap_to_shell` broke out of its scan on the first step of the left
   flank, because `|x|` there falls from the off-centre crown sample through
   zero before it grows again.  The left keycap collapsed onto a single
   spot: `button1` spanned x 0.018–0.037 (2.3 mm) instead of the full cap.
   Cut the scan at the widest sample, and snap both flanks against the same
   profile so the two caps stay exact mirrors.
2. `_ring_profiles` trimmed a walk only where `z` crossed `_HONEY_RIM_Z`.
   The low rings near the nose never drop that far, so they kept the *whole
   closed loop* as their profile; stations are sampled at matching fractions
   of their own length, so interpolating one against a properly trimmed
   profile paired an underside sample with a flank sample.  Keycap points
   were dragged to 6 mm off the deck and, further back, the profile shrank
   to a fraction of its real width.  Fallback: trim at the ring's own lowest
   point.
3. `_smooth_closed` (Catmull-Rom) overshoots at a sharp corner; a side
   button was bulging ~0.9 mm up the flank past the top of its own outline,
   reading as a cap climbing the crown.  The result is now clamped to the
   source outline's bounding box.

**Also.**  Longitudinals are now one every `_LOFT_EVERY` (=3) ring points —
a line per ring point read as a featureless grid.  Button fills lightened so
the translucent keycaps let the perforation through.  `tools/aerox5_model.py`
moved into the repo (was an unversioned `~/Downloads` file) and
`tools/extract_aerox5_mesh.py` now defaults to `build/aerox5_detailed.obj`,
so the checked-in `aerox5_mesh.py` regenerates byte-for-byte from a clean
checkout.

**Tests.**  66 passing.  The honeycomb tests were rewritten against the new
`{"pts", "center", "normal"}` cell shape and now pin the measured lattice
(pitch, cell extent, uniform span) rather than a magic count; the keycap
nose-taper and wheel-clearance tests were made robust to the resampled
outline rather than loosened.

---

## 2026-10-02 (cont.) — keycap rear edge and the field's flank reach

Two follow-ups after re-reading the official top view at higher zoom.  Both
supersede numbers in the entry above.

**The caps end just behind the wheel, not at 55 mm.**  The top view shows one
*continuous* perforation from the keycaps' rear edge to the tail, parted only
by the bare channel down the centreline (the wheel slot and the CPI housing).
The old model had the caps running back to 55 mm with a wedge ramp
(`BTN_WEDGE_Y0`) tapering their outer edge, so the field could only start
between the wedge and the tail.  `BTN_Y1` is now `41.5` mm and `_btn_edges()`
returns `_split_u(y), outer` with no ramp: the caps' rear edge crosses the top
surface as a nearly straight line, and the perforation takes over there.
`mouse3d._HONEY_REAR_Y` follows to 41.5 mm, the `_HONEY_DPI` ellipse and the
`_HONEY_BAND`/`_HONEY_WEDGE` rectangles are gone, replaced by the
`_HONEY_CENTRE` capsule (radius 6.4 mm, 36–72 mm) that keeps the channel bare
while the two patches flanking the wheel run back and merge with the rear
field.  The lattice is anchored on the caps' rear edge (front-anchored) rather
than on the tail, because that edge is the one boundary the drawing pins down;
`_HONEY_RIM = 3.0` mm keeps a bare strip at the tail rim.

**The field wrapped too far down the flanks.**  Overlaying the model's openings
on the drawing's top view showed the outer cells sitting *on* the outline.  The
drawing puts the outermost opening in each row at ~0.91 of the silhouette
half-width and a cell's outer vertex reaches a further 1.95 mm, so the bare rim
it leaves is only ~1 mm (3.2 px of a 93 px half-width).  Solved for the arc
fraction that lands the vertex at that rim at each station: it is nearly flat,
0.533 at the caps' rear edge rising only to 0.588 at the tail — the old value
ramped 0.62 → 0.94, which is why the cells rode over the edge.  The reach grows
so slowly because the tail's cross-section is mostly rolled shoulder: most of
its arc buys very little width.

Verified: per-row outer-edge ratio 0.83–0.90 against the drawing's 0.77–0.90
envelope, with the same stagger oscillation; the tail now stops at ~73 % of the
local height in the side view, matching the drawing's side view.  Field:
23 rows, 43.5 → 119.4 mm.  **66 tests pass.**

**Also.**  `extract_aerox5_mesh.py`'s hole-filling pass averaged four
neighbours with `np.nanmean`, which warns ("Mean of empty slice") when a cell's
neighbours are all still empty during the first iteration; it now averages over
the finite ones and leaves real holes to the next pass or the global fallback.
Numerically a no-op — the regenerated mesh is unchanged — but the build is
warning-clean under `-W error::RuntimeWarning`.
