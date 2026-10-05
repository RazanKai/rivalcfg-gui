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

---

## 2026-10-04 — the click panels are a mirrored pair; the centre spine is filled

Reported from the app's top view: the two keycaps did not match -- the right
one was visibly larger -- and the centre strip between the wheel well and the
CPI button was not filled in properly.

**The right cap was traced from a wider shoulder.**  `cap_plan_curves` gave
each panel its own outer edge, and the two groups in the source model are *not*
mirrored: the model's flank break is asymmetric (`zlim` ramps on one side,
constant on the other -- see `aerox5_3d_files/model.py`), so the right cap's
part line runs out to 32.09 mm from the centreline while the left one stops at
27.09 mm, a difference of up to 6 mm through the shoulder (y 10-26 mm).  The
left panel was the one that matches the part, so **both panels are now drawn
from the left one's plan curve, mirrored about the centreline**; heights are
still taken per side from that side's own top faces, so each outline sits on
the surface underneath it.  The right cap is now 27.08 mm -- the pair is
symmetric to the resampling (the `_smooth_closed` loop centres differ by
~0.001).

**The spine between the well and the CPI pill was not traced.**  The two panels
do not simply flank the centre all the way back: between the wheel well (ends
~37.5 mm) and the CPI pocket (~46 mm) they *abut* along a narrow crowned strip
-- the v3 mesh splits it between the two groups, each one's top faces reaching
to within a few tenths of a millimetre of the centreline (|x| <= 0.81 mm over
y 39.45-45.02).  That strip is thinner than `CAP_MODE_WIN` (0.9 mm), the
majority filter that exists to take the *outer* boundary's lattice feather off,
so it was erased from the mask and the traced inner edge ran straight past it
at ~5 mm, leaving only a 2 mm spike where a few cells survived.
`cap_top_mask` is now split into `cap_top_raw` + the filter, and
`cap_plan_curves` reads the spine off the *unfiltered* mask
(`spine_halfwidth`: the cap-top cell nearest the centreline in each row, `inf`
where the row has none -- i.e. where the cap's real boundary is its own well
wall).  The inner edge therefore follows the part: ~5.0 mm along the well,
pinching to ~0.2 mm a side over the spine, and back out around the pill.  The
smoothed outline's tightest point is a 0.13 mm channel -- the panels never
cross the centreline (no point of either cap lies on the other's side).

**Test.**  `test_keycaps_have_split_channel_after_the_wheel` asserted the
channel stayed wider than ~1 mm (it was written against the 2 mm spike).  It
now asserts what the part does: the panels never merge (left cap entirely left
of the right), the channel beside the wheel is several mm wide, and the pinch
between the well and the pill is under 0.5 mm a side.
`test_click_panels_are_symmetric`'s docstring no longer claims "near-mirrored".

**66 tests pass.**

---

## 2026-10-04 — DPI and Polling become one Sensitivity page, with an active preset

Requested: the DPI and Polling pages each held one short card and left most of
the page empty; they are now one **SENSITIVITY** page holding both sections in
one card, and each DPI row carries a radio that marks which preset is the
active one.

**Merge.**  `create_dpi_page()` + `create_polling_page()` are replaced by
`create_sensitivity_page()`; the nav entry `("sensitivity", _("SENSITIVITY"))`
replaces the two former entries, and `RIVALCFG_GUI_START_PAGE=dpi|polling` is
mapped onto it so the dev aid keeps working.  The two sections keep their own
capability-driven construction (DPI rows from `caps.dpi_*`, polling radios from
`caps.polling_choices`) and one **APPLY** now sends both as a single
`ApplyPlan` — `--sensitivity` then `--polling-rate`, the same relative order
`build_full_plan` uses.  **RESET** restores both sections to their defaults.

**Active preset.**  Each DPI row gained a label-less `Gtk.RadioButton` at its
left edge (the delete button moved to the row's right end).  The choice is
app-side only — `rivalcfg` is write-only and its CLI never exposes the
multidpi handler's `selected_preset`, so the mouse's live CPI stage cannot be
read back and is not what the radio shows.  It is stored in the profile as
`dpi_active_index` (`migrate_profile` copies it, defaulting to 0) and drives
**Hyprland mouse sync**, which previously always used preset 1: `on_apply_*`
and `apply_all_to_device()` now call `_sync_hyprland_to_active_dpi()`, and
picking a radio re-syncs when auto-apply and Hyprland sync are both on.

Rebuilds are signal-safe: `rebuild_dpi_ui()` holds `_dpi_selecting` for the
whole build, so the programmatic `set_active()` calls cannot look like a user
selection; deleting a preset shifts `dpi_active_index` down when the hole is
above it; loading a profile clamps the stored index into range.

**Test.**  A radio, and the readout under the rows (`Active: DPI 1 — 400`),
were verified out of tree: page build, click, slider edit, add/delete, and a
profile round trip (including a clamped out-of-range index), plus the merged
apply plan and the reset path.  `test_migrate_keeps_active_dpi_preset` covers
the new profile key.  **67 tests pass.**

**Note (pre-existing).**  The RGB page's minimum width is 1017 px, so the
window's content needs 1229 px; below that the right edge of *every* page is
clipped rather than scrolled (`stack_scroll` is `NEVER`/`AUTOMATIC`).  At the
app's 1280 px default this is invisible, but a narrowly tiled window cuts the
row's delete buttons.  Unchanged here — recorded so it is not mistaken for new.

## 2026-10-04 (cont.) — the active DPI preset is now actually sent (library write)

Reported: *"if i change dpi on the app it doesnt change it on the mouse. if i
change it on the mouse with the dpi button it doesnt update on the app."*
Both directions were investigated; one of them is fixable and now is.

**The packet, and what the CLI can and cannot send.**  The Aerox 5's
sensitivity write is `[count, selected_preset, dpi_codes…]`, confirmed by
calling the handler directly —

```
multidpi_range_choice.process_value(si, [400, 3200], selected_preset=1)
  -> 0x02, 0x01, 0x04, 0x17        # count=2, selected preset=1
```

`multidpi_range_choice.add_cli_option()` only ever parses the DPI *list*, and
`Mouse.__getattr__._exec_command()` calls
`process_value(setting_info, *args)` with the single CLI argument, so
`selected_preset` falls through to `None` and the handler substitutes
`first_preset`.  **Every `--sensitivity` invocation therefore drags the mouse
back to preset 1** — rivalcfg's own docs say as much ("When you set the
sensitivity through the CLI, the selected preset always back to the first
one").  The previous entry's claim that the radio "is app-side only" was right
about the CLI and wrong about the API: `mouse.set_sensitivity(values,
selected_preset)` reaches the same byte.

**The mouse → app direction has no path at all.**  `sensitivity` declares
`readback_length: 64`, but the live readback is an **ACK, not state**: 64 zero
bytes with the command byte `0x6d` echoed back.  Only `battery_level`
(2-byte response, real `level`/`is_charging` decoders) is a query, and the
profile exposes no DPI query command.  The hardware DPI button is
unobservable from the host, in this app or any other that goes through
rivalcfg.  Battery polls round-tripping every 60 s do prove the interface
works, so the write path was never the problem.

**Seam.**  `ApplyPlan.steps` was argv-only, so a library call could not be a
plan step.  `Step` is now `Union[list[str], Callable[[], tuple[bool, str]]]`,
`ApplyPlan.add()` keeps a callable as-is, and `CommandQueue._execute()` calls
it on the same single-writer worker (with its own `try/except`, since a step
must never kill the queue).  On top of that:

* `sensitivity_library_step(values, active_index, save, mouse_factory)` opens
  via `rivalcfg.get_first_mouse()` — matching the CLI runner, which never
  passes `--device` — calls `set_sensitivity`, then `save()` unless told not
  to, and always `close()`s.  It **never raises**: a missing mouse, a `set_*`
  error and an import failure all become `(False, message)`, so the failure
  surfaces in the status bar like any CLI error.
* `add_sensitivity(plan, values, active_index, save)` is the single entry point
  for all three DPI write paths (page APPLY, auto-apply, Apply-all), falling
  back to `["--sensitivity", …]` when the library is unavailable.
* `USE_LIBRARY_WRITES` (env `RIVALCFG_GUI_FORCE_CLI=1`) forces the CLI
  everywhere.  This is the escape hatch for the one new risk: the in-process
  HID write has no equivalent of the subprocess runner's 30 s timeout, so a
  wedged device could stall the queue.

`build_full_plan()` now emits the DPI write as its **own first step**, ahead of
the polling/buttons/zones batch — the preset needs the library, the rest is
happier as one CLI invocation.  Cost: one extra operation per Apply-all.
`--no-save` is honoured via the explicit `save=` argument (the library path
bypasses the GUI runner that appends the flag).

**UI honesty (not a fix — a correction).**  The readout reads *"App preset: N —
<value> DPI"* with a tooltip saying the mouse's DPI button is not reported
back, and the radio tooltip says the choice is sent on Apply.  Picking a radio
still writes nothing on its own: with auto-apply off it is recorded and sent
on APPLY, with auto-apply on it switches the mouse immediately — the
no-writes-without-consent rule (`README.md:18`) is intact.  Also fixed
`on_hyprland_sync_toggled()`, which still fed the sync from `dpi_values[0]`
instead of the active preset — a leftover from before the radio existed.

**i18n.**  Three new msgids (readout, radio tooltip, readout tooltip) in all 10
catalogs; the two superseded ones are now `#~` obsolete.  `msgfmt --check`
passes everywhere and the compiled `.mo`s were re-verified by resolving each
string through `gettext.translation`.

**Tests.**  **84 pass** (from 67): library step (`set_sensitivity` args,
`save()`/skip, `close()` on every path, factory/`set_*`/`save` failures, `None`
when disabled), `add_sensitivity` on both paths, callable steps in
`ApplyPlan.add` and through the queue (success, reported failure, and a
raising step), DPI-step-first ordering, and `test_full_plan_order` pinned to
the CLI fallback via a `cli_only` fixture so its argv assertions stay exact.
`test_library_step_preset_byte_matches_handler_contract` builds the packet with
rivalcfg's own handler and the device's real profile, and asserts the CLI
packet's byte 1 is `first_preset` — the bug, captured as a test.

**Verified on hardware.**  Confirmed on the Aerox 5 Wireless (`1038:1852`,
wireless): changing the active preset and pressing APPLY moves the pointer —
this firmware *does* honour the `selected_preset` byte, so the selection is
real and not just a record in the profile.  The log from the run is the whole
mechanism in three lines, once per APPLY:

```
Profile saved: Default (dpi=[400, 800, 1000, 1800, 2200], polling=1000)
set_sensitivity (library): values=[400, 800, 1000, 1800, 2200] selected_preset=2 save=True
rivalcfg rivalcfg --polling-rate 1000
```

Two things that run also settles.  The preset index sent matches the radio
that was picked, and repeats cleanly across a full cycle of the group
(3 → 0 → 1 → 2 → 3 → 2, one write each, no duplicates or storms).  And
**every** sensitivity write is accompanied by a `--polling-rate` invocation —
i.e. every one of them came from APPLY, never from a bare radio click, so the
no-writes-without-consent rule held on real hardware.  Direction mouse → app
is still impossible: the hardware DPI button changes the stage and the app's
readout correctly does not follow it.

**Remaining (pre-existing).**  The 10 `.po` catalogs are stale against
the source: ~49 msgids present in `xgettext` output are absent from every
catalog, including page titles that were renamed at some point (`BATTERY`,
`KEYBOARD`, `MOUSE`, `POWER`, `HYPRLAND`, `SLEEP TIMER`, …), and one `es`
string is untranslated.  Those strings have been silently falling back to
English; they predate this change and a `msgmerge` + translation pass is
needed to close the gap.  Recorded here so it is not mistaken for new.

## 2026-10-04 (cont.) — the buttons popover opens empty

Reported: *"clicking any of the buttons' labels open an empty menu list and
you cannot actually set anything."*  The hit test was fine — the click found
the facet and `_build_popover(key)` ran and filled the list.  The popover
itself was the problem: it had **no preferred size**, so GTK laid it out as a
60×84 sliver and nothing inside was drawn.

`Gtk.Bin` takes its preferred size from its direct child, and a *hidden* child
contributes nothing.  The popover tree is

```
Gtk.Popover  (a Gtk.Bin)
└── Gtk.ScrolledWindow        <- direct child, never shown
    └── Gtk.Viewport          <- added by ScrolledWindow.add()
        └── Gtk.Box           <- the assignment list, built per click
```

`_build_popover()` ended with `popover_box.show_all()`, which recurses
*downward only*: it showed the box and its rows but never the ScrolledWindow
above them, so `gtk_widget_show_all` never ran on the popover's direct child
and the popover reported zero.  Instrumenting the running app settled it —
before: `popover alloc 60x84, scrolled window visible = False`, with the 25
assignment rows present and visible underneath it.

Fix, in `create_buttons_page()`:

* `popover.show_all()` instead of `popover_box.show_all()`, so the whole
  subtree including the ScrolledWindow is shown.
* `popover_scroll.set_propagate_natural_height(True)`.  Without it the
  scrolled window reports only `min_content_height` (120) no matter how much
  is in the list, so the popover grew to a fixed 150 px and scrolled a
  long list through a slot instead of opening to `max_content_height` (420).

After both: `popover alloc 259x450, scrolled window visible = True`, and a
screenshot shows the populated list ("MOUSE / Left click / Right click /
Middle click / Button 4–9 / DPI cycle").  No other page uses a popover
holding a scrolled window, so this is the only site with the pattern; the
colour picker's popover builds its child inline and was always shown by
`show_all()` on the popover itself.

**Not covered by a test.**  `tests/` is deliberately GTK-free (84 passing,
no `gi` import anywhere), and reaching `_build_popover` needs a realized
drawing area with a non-zero allocation — i.e. a display — plus a synthetic
button-press event.  Guarding the invariant in a test would mean making the
suite require a display for one case, so the check here is the manual one
above.  Worth revisiting if the page gets refactored.

## 2026-10-04 (cont.) — button mapping: drop mouse→mouse, add key combinations

Reported: *"mouse buttons having the option to be set as another mouse button
seems like a dumb idea, i was just able to set mouse button 8 as button 4 …
remove this. just keyboard key, multimedia, and i was think combos of 2-3
keyboard keys"* — then, on the mouse targets: *"why do you wanna keep any mouse
keys as the functions? we already have the mouse keys on the mouse"*.  Right:
the popover offered `button1`…`button9`, so a side button could emulate a
button that is already physically on the mouse.

**A claim of mine that was wrong.**  I had said that dropping
`scrollup`/`scrolldown` from the target list would make a remapped wheel
direction a one-way trip.  It does not: `mouse3d.BUTTONS` holds only
`button1`…`button9` and `create_buttons_page()` intersects it with the device's
keys, so scroll up/down are **not** clickable facets and have no chips.  They
were only ever offerable as targets *for other buttons*.  The per-element
**Default** row is still wanted, but for a different reason: `button1`/`2`/`3`/`5`
default to mouse buttons and `button6` to `dpi`, so once mouse and dpi targets
are gone, "Default (Left click)" is the only way back for those short of a full
RESET.

**What the hardware can do (primary sources, not assumption).**  rivalcfg's
buttons handler writes exactly one key byte per button
(`packet[offset] = button_keyboard; packet[offset + 1] = keyboard_layout[value]`,
`handlers/buttons/buttons.py`), and the maintainer confirms combos are
unsupported — issue #251, 2025-08-06: *"It is theoretically possible to press up
to 4 buttons at once on most devices but currently this is not supported by
Rivalcfg."*  The field is `<BindingType 1B> <Param 4B>` and Param is a list of
HID usage codes, so the device can.  flozz's own captures on a Rival 650 —
issue #171, 2021-12-13 — show the encoding exactly:

```
LCtrl + C          → 51 E0 06 00 00
LCtrl + RShift + C → 51 E0 E5 06 00
```

`0x51` is the profile's `button_keyboard`; the rest are HID usage codes, left to
right, unused slots zero.

**Probe, because that capture was on a Rival 650, never on an Aerox 5
Wireless.**  A throwaway script built the real 11-button packet with rivalcfg's
own `process_value`, overwrote button 8's field with `51 E1 68 00 00`
(LeftShift + F13), sent it through `mouse._hid_write(report_type=…,
data=merge_bytes(si["command"], packet))`, and read the mouse's own keyboard
input node (`/dev/input/event4`) — so nothing depended on window focus.  Four
presses produced four `KEY_LEFTSHIFT` down/up pairs interleaved with four
`KEY_F13` down/up pairs: the extra Param bytes are honoured.  Button 8 was
restored to `disabled` in a `finally`.  Gate passed; the feature is possible on
this device.

**Implementation.**

* `device_core.py` — combination values stay **strings** in canonical form
  (`"LeftCtrl+LeftShift+C"`, modifiers first) so they round-trip through
  profiles and the existing `button_mapping` dict, which validates the button
  key but not its value.  New: `is_combo` / `parse_combo` / `format_combo` /
  `combo_codes` / `combo_label`, `COMBO_MODIFIERS`, `COMBO_MAX_KEYS = 4`;
  `action_label()` renders a combination, so chips and the popover needed no
  special case.
* **The write seam.**  `buttons_library_step()` mirrors
  `sensitivity_library_step()`'s contract exactly: `None` when library writes
  are off or the mapping holds no combination, never raises, `_library_error_hint`
  on failure, `close()` in `finally`, one `logging.info`.  The packet comes from
  rivalcfg's own `process_value` — each combination is fed as its **last key**, a
  valid placeholder — and then only that button's 5 bytes are overwritten with
  `[button_keyboard, *codes]`.  Everything else is still the handler's output.
* `add_buttons()` is the single entry point for all three buttons write paths:
  library step iff the mapping holds a combination, otherwise the unchanged
  `["--buttons", …]` argv.  `build_full_plan()` passes its shared batch as
  `merge_into` so a combination-free apply keeps the byte-identical invocation
  `test_full_plan_order` asserts; a combination instead becomes its own step
  ahead of the batch.  `build_buttons_arg()` now raises `ValueError` on a
  combination rather than emitting a string the CLI validator would reject later.
* **Forced-CLI mode says so.**  `RIVALCFG_GUI_FORCE_CLI=1` cannot express a
  combination, so `add_buttons` adds a step that fails with *"Key combinations
  need the rivalcfg library, but library writes are disabled
  (RIVALCFG_GUI_FORCE_CLI=1)"* — a loud error instead of a silent wrong write.
* **Deleted as dead** (grep-confirmed no callers): `MOUSE_BUTTON_ACTIONS`,
  `SPECIAL_ACTIONS` (which described exactly the targets being removed),
  `DeviceCaps.button_action_values()`, and the `rivalcfg_gui.build_buttons_arg()`
  pass-through.
* **Popover** (`create_buttons_page`): no mouse targets; only `disabled` of the
  old specials, plus a **Default (…)** row skipped when the default *is*
  `disabled`.  MULTIMEDIA unchanged.  KEYBOARD is now key dropdown +
  `[Ctrl][Shift][Alt][Super]` toggles + Assign.  Modifiers are emitted first so
  the bytes match the capture; picking a modifier as the key does not pair it
  with its own toggle.  Assigning a lone key yields the plain key name — today's
  behaviour, not regressed.  With all four modifiers plus a key the Assign button
  goes insensitive and an inline hint appears (four codes is the field's ceiling).
  The `popover.show_all()` / `propagate_natural_height` fix from the entry above
  is untouched, and the new hint label is `set_no_show_all(True)` so it cannot
  re-introduce the empty-popover bug.
* The page's auto-apply and APPLY button now route through `add_buttons` +
  `_debounce_plan` / `_queue_plan` instead of building an argv inline.

**i18n.**  Six new msgids (`Default (%s)`, `Ctrl`, `Shift`, `Alt`, `Super`,
`At most %d keys at once`) in the English source and all 10 catalogs, `.po` plus
recompiled `.mo`, every one `msgfmt --check` clean and each verified to resolve
through `gettext` (German *Strg* / *Umschalt*, French *Maj*, Spanish *Mayús*,
Italian *Maiusc*).  Note the catalogs cover 77 of the source's 128 msgids, and
none of the popover's *existing* strings (`Assign`, `KEYBOARD`, `MULTIMEDIA`,
`Layout: qwerty`, `MOUSE`) are in any catalog — so the popover is now partly
translated where it used to be uniformly English.  Adding those neighbours would
be a small, separate change.

**Tests.**  84 → 106, still GTK-free.  The captures are pinned as tests —
`LeftCtrl+C → 51 E0 06 00 00` and `LeftCtrl+RightShift+C → 51 E0 E5 06 00` — so
the encoding rests on hardware evidence rather than on my reading of it, and the
bytes are checked **in the packet the mouse actually receives** (command prefix
and profile offset included).  Also covered: the other fields still equal the
handler's output, save/save=False, `close()` on every path, factory/`_hid_write`
failures, a rivalcfg without `_hid_write`, a profile without `button_keyboard`,
more than four keys, an unknown key (both as the last key, caught by
`process_value`, and as a modifier, caught by `combo_codes`), `add_buttons`
routing, `build_buttons_arg` raising, and `build_full_plan` putting a combination
ahead of the batch while a plain mapping stays in the shared invocation.

**Not covered by a test.**  The popover itself — same reason as the entry above
(it needs a realized widget with a non-zero allocation, i.e. a display).  The
`_selected_keys()` dedupe and the 4-key over-limit UI are therefore checked by
hand.

## 2026-10-05 — the RGB page preview is the mouse itself, stood upright and lit

Requested: *"on the rgb/lighting page we have a verticle 3 block display
undderneath the color picker that simulates the three rgb zones on the mouse.
we have a 3d mesh we use for the buttons page. we could use the 3 mesh instead
of the 3 square bloacks, and light up the 3d mesh according to the set rgb
values"*, refined to *"use the 3d view from the button mapping page but make it
vertice instead of horizontal. the RGB is actually all throughout the mouse.
the strip on the bottom is just one part of it. basically the zones are
underneath the keycaps. the middle section of the mouse and the bottom part
that has the strip."*

The three flat blocks are gone.  The page now draws the same mouse the Button
Mapping page draws, rolled upright, with the three zones lighting **regions of
the body** in their live colours.

**Why the zones are regions, not a strip.**  The old display implied the LED
was only the visible bottom strip.  The mesh says otherwise: the light runs the
length of the mouse under the shell.  So the band boundaries come from real
geometry, not from even thirds — `_ZONE_EDGES_MM = (50.0, 88.0)`, i.e. 50 mm
where the keycap outlines end (`Button1`/`Button2` span 1.0–50.3 mm) and 88 mm
to hand the tail — the zone whose strip you can actually see — to z3.  Bands
crossfade over ±13 mm with a **sequential** smoothstep, `w1 = t1*(1−t2)`,
`w2 = t1*t2`, so the three weights always sum to exactly 1; the obvious
`w2 = t2` does not.

**Standing it up.**  A bare 90° roll is wrong.  At the page's angles
(yaw −120°, pitch 42°) the body axis already projects ~21° off horizontal, so a
quarter turn leaves the mouse visibly tilted.  The axis direction in projection
is `(−sin yaw, cos yaw · sin pitch)`, and `upright_roll()` is the turn that
sends it straight down — **111.12°** here, landing the projected axis at
`du = −5.6e-17`, exact to floating point.  `View` gained a `roll` field applied
as a screen-space rotation *after* projection and *before* the offset, guarded
by `if self.roll:` so `roll = 0.0` is bit-for-bit the projection the Buttons
page has always used.  `camera_dir()` stays roll-free on purpose: roll is about
the view axis, so facing and depth culling must not change.  The one positional
`View(...)` in the tree — `fit_view`'s probe — was updated to carry the roll, so
framing accounts for it.

**The renderer.**  A separate path from `render()`, whose return contract the
Buttons page and its tests pin.  `build_lighting_geometry()` projects the body
once — 50 slab ribbons (each ring gap subdivided twice, taken from the
camera-facing arc of the ring, filled and stroked in the same colour so shared
edges do not crack), the strip ribbon, the honeycomb rims, the structural lines
— and `render_lighting()` only fills.  That split matters: the colour editor
fires on every pointer motion while a colour is being dragged, so the geometry
is cached on `(w, h)` in `create_rgb_page`.

Facing uses a **Newell** normal, not `_seg_facing`'s radial approximation — the
radial form has no `y` component and misclassifies the strongly y-facing nose
and tail.  The Newell sign is arbitrary (it depends on ring winding), so it is
fixed radially; the *shading* dot takes the magnitude, since a slab that passed
the cull is front-facing by construction.  That last part was a real bug caught
in the preview tool: without it every slab shaded at exactly 0.30 and the body
rendered flat.  Slabs are drawn back-to-front by depth so a nearer one wins an
overlap near the silhouette.

**Tuned after first feedback** (*"the glow is way too bright and drowns out the
3d mesh of the mouse making it look like a blob and the zones colours dont blend
into each other"*).  Two changes, both structural rather than a brightness knob:

- *The blob was flat shading, not brightness.*  A slab took its normal from its
  own polygon, and a slab spanning the camera-facing arc — up to half the
  circumference — averages out to pointing straight at the camera, so every slab
  landed within a few percent of the same value.  The arc is now capped at
  `_MAX_SLAB_ARC = 6` ring-point indices (~200 slabs instead of 50), which keeps
  each normal local.  The shading then genuinely varies across the body, and the
  shell is drawn dark on purpose (`_SHADE_FLOOR + _SHADE_RANGE·lam`, 0.13–0.61,
  mean ≈ 0.42) with the glow left to the strip and the honeycomb.  `_BLOOM` also
  dropped 0.18 → 0.06 — past about a tenth it stops reading as glow and starts
  lifting the whole shell off the background.
- *The blend is now ±13 mm* (`_ZONE_FADE_MM = 26`), roughly a fifth of the body,
  instead of ±5 mm — which at 48 ring points across 128 mm was only about four
  slabs wide and read as the same hard-edged blocks this display replaced.

The mesh is also drawn to be read rather than to decorate: honeycomb rims at
0.70 alpha in the zone colour pulled 45% towards white, rings in the zone tint,
and the longitudinals/seams/button outlines in a neutral light grey (they run
across zones, so a zone tint would be a lie).  Pinned by tests: the shade spread
must exceed 0.25 and the top of the range stay under 0.8, and sampling the blend
at 400 points must show no step above 0.05 between neighbours with a genuine mix
at the boundary.

The strip is drawn from the ring points with mid-`z` < 6 mm and mid-`y` inside
45–122 mm, at `mix(zone, white, 0.35)`.  It needs a **permissive cull bias
(−0.25)**: the camera sits above the terminator at 42° pitch, so the strip at
z ≈ 1–5 mm is at the silhouette and would otherwise be culled — drawn this way
it reads as the bright rim an edge-on diffuser actually has.

**Clicking** a region still selects that zone in the editor, as the blocks did.
Hit-testing scans the cached slab polygons back-to-front and takes the first
containing one (the slabs are already back-face culled, so the facing ones tile
the silhouette) — *not* the boundary-line scheme sketched in the plan: that
assumed a constant-`y` plane projects to a screen line, which is false under
this projection (for fixed `y` the map `(x,z) → (u,v)` has Jacobian determinant
`cos yaw · cos pitch ≠ 0`, so it covers the whole plane, not a line).

**Bug fixed on the way.**  A factory reset rebound `app_state["zones"]` to a
fresh dict and updated the swatches but never repainted the preview, and the
draw closure kept the stale dict.  The preview now reads through `app_state`
and the reset queues a redraw.

**Only `zones[:3]` band.**  A device with `--logo-color` has a fourth zone
under the palm rather than along the length; it keeps its swatch row and stays
selectable, but gets no band.  The label is now `MOUSE PREVIEW (front → back)`
— deliberately left untranslated in all 10 catalogs, like most of the page's
neighbours (the catalogs cover 77 of 128 msgids), rather than adding `.po`/`.mo`
churn for one string.

**Tests.**  212 → 223, still GTK-free: `roll = 0.0` equals the classic
projection exactly; the rolled axis is vertical with the nose above the tail;
`fit_view` keeps the mouse in frame at 260×340; the zone weights sum to 1 and
order nose→tail with a 50/50 blend at the first boundary; the boundaries map
back to 50/88 mm; every zone lights slabs, the strip stays in its own length
band, and the slabs are genuinely shaded rather than flat; `zone_at` maps each
zone's centroid to that zone and returns `None` off the body; `render_lighting`
runs against the fake context, emits an additive pass, and leaves the cached
geometry byte-identical across recolours.

**Verified by hand** (no display): page build under broadway, the DrawingArea
drawn through its real `draw` handler, and synthetic button presses at each
band's centroid selecting z1/z2/z3 respectively with a press off the body
ignored; editing a colour repainted the corresponding region
(`z2 → ff00ff` showed magenta in the middle band).  `tools/preview_mouse3d.py`
gained `lighting_vertical.png` and a single-colour `lighting_shading.png` at the
page's own size.

**Not covered by a test.**  That the DrawingArea path itself works — same reason
as the other GTK entries above: it needs a realized widget with a non-zero
allocation.

---

## 2026-10-05 — Button Mapping: key grouping and press-a-key capture

**The user's report.** Two complaints about the keyboard section of the
button-assignment popover: the key list was *"haphazard because its sorted by
alphabet"*, and *"i cant just press the key on the keyboard for it to be
detected in the setting"*.

**Grouped key list.**  The picker no longer sorts alphabetically.  It lists
`device_core.keyboard_groups()` — letters, digits, the function row, punctuation,
navigation, and so on — in HID order, each group under its own heading
(`LETTERS`, `DIGITS`, …).  Modifiers stay in their own group and are also
available as toggles.

**Press-a-key capture.**  A `Press a key…` toggle arms a capture; pressing a key
fills the picker with it and switches on the toggles of whatever modifiers were
held (Ctrl / Shift / Alt / Super), then disarms.  Three separate defects had to
be found and fixed before it worked in the real app.

**1. A press never reached the popover.**  With the pointer resting on the
popover — which is where it always is, having just clicked `Press a key…` — a
`Gtk.EventControllerKey` attached to the popover never fires and the press goes
to the toplevel's `key-press-event` instead, so an armed capture saw nothing.
`Gtk.Widget.add_controller` does not exist in this GTK build, so the controller
had to go on through the constructor's `widget` property and cannot be detached;
building one per open would stack another every time.  The capture now hangs off
a `key-press-event` **signal** handler on the toplevel window, hooked from the
popover's `show` signal (at page-build time the popover is still unparented, so
`get_toplevel()` returns the popover itself).

**2. Escape never reached the toplevel at all.**  A key event travels
popover → toplevel, but GtkPopover takes Escape to dismiss itself: instrumenting
the chain showed `d` arriving at the popover's signal *and* the window's, while
Escape arrived **only** at the popover's own `key-press-event` — the window never
saw it, and the popover closed underneath an armed capture.  So Escape is now
consumed on the popover itself.  The popover handler runs first for ordinary keys
too, fills the capture and disarms, and the window handler then finds nothing
armed and does nothing — which is why a key is filled in exactly once.  Verified
in-process: Escape with the capture armed disarms it and leaves the popover
**open** (`capture='Press a key…' active=False popover_visible=True`); a letter
after a fresh arm still fills it.

**3. Active modifiers were invisible.**  The page's blanket `button { background:
… }` rule also covers toggles and overrode the theme's `:checked` styling, so a
Ctrl/Shift/Alt/Super toggle looked byte-identical whether it was on or off — a
capture filled them in with no visible result.  Pixel sampling confirmed the
inactive and active backgrounds were the same `(14,14,26)`.  Added a
`button:checked` rule (static CSS plus the dynamic accent block in
`update_accent_color`, so it follows the profile's accent colour); the toggles
now take an accent background and border when on.

**Verified by hand** (real clicks and real keys via ydotool, screenshots at each
step): sidebar → Button Mapping → the Button 8 chip opens the popover; arm;
Ctrl+Shift+J arrives and the picker shows `J` with **Ctrl** and **Shift**
highlighted and Alt / Super plain; the capture button returns to `Press a key…`.
Debug tracing confirmed GDK delivers the modifier state the handler needs
(`Control_L state=0x0`, `Shift_L state=0x4`, `J state=0x5` = Ctrl|Shift).

**Tests.**  Still **223 passing** (`python -m pytest tests/`), GTK-free; the
capture's key-delivery and Escape behaviour need a display and are covered by the
hand runs above instead.

---

## 2026-10-05 — the RGB preview follows the lighting effect (rainbow), and shifts

The 3D preview lit the body from the three zone colours and nothing else, so
turning the rainbow on left it showing three steady colours.

**First pass: a spectrum.** `render_lighting`'s `colors` now accepts *either*
three zone triples *or* a `y -> (r, g, b)` callback, resolved by a new
`_colour_at`.  The rainbow is `rainbow_colour(y, phase=0.0)`: a saturated HSV
sweep along the body with a phase that rotates it.  That split — the callback
form, and the phase — is what survived; the first pass's spread and rate did
not, see the third pass.  All five colour sites (bloom, shell, strip,
honeycomb, rings) go through the callback, so the rainbow is the whole mouse,
not just the body.  Geometry still carries no colour, so the rainbow draws
through the same cached projection and clicking a region still selects a zone.

**Second pass: it had to move.**  The first version drew the spectrum frozen at
phase 0, and the user's reply was the correct objection: *"thats not what the
rainbow effect is now is it?  the colours are continously changing on the mouse
when rainbow effect is on."*  A static gradient is not the effect.  So `phase`
is now driven: `_RAINBOW_PERIOD_MS` / `_RAINBOW_TICK_MS = 80` (a step of 0.02
turns per tick at the 4 s period first tried; the period was raised to 15 s in
the third pass), read at draw time from an `anim` dict that a `GLib` timeout
advances and then `queue_draw()`s.

**The timer lifecycle is the fiddly part.**  A `GLib` timer that is never
stopped would wake the process for the rest of the session, so `on_shift_tick`
stops itself (`return False`, clearing `anim["timer"]` first so `sync_shift`
cannot double-`source_remove`) the moment the rainbow is off **or** the widget
is unmapped — "a frame nobody is looking at is not worth drawing ~200 slabs
for".  `sync_shift()` starts it only when the rainbow is on *and*
`strip.get_mapped()`, and is re-armed from the widget's `map` signal, so
switching back to the page resumes it.  Both entry points (`on_effect_toggled`,
`on_rainbow_toggled`) go through `repaint_rgb_preview()`, which now also calls
the sync hook stored as `app_state["_rgb_sync_shift"]`.

**Cost, measured.**  One animated frame is a full `render_lighting` redraw:
**17 ms** at 260x340 (profiled: shell 6.5, structure 4.1, honeycomb 3.4, bloom
2.5, strip 0.4).  At 12.5 fps that is **~21 % of one core, and only while the
RGB page is the visible page with a rainbow selected** — 0 draws/s otherwise
(verified: 0 draws in 0.6 s when steady, 7 when shifting, back to 1 — the
single repaint from the toggle — when switched off again, from either the
checkbox or the effect radio).  Caching each polygon's cairo `Path` and
replaying it with `append_path()` was tried and **only saved 8 %**
(16.8 -> 15.4 ms for identical pixels), so it was not taken: the time is in
rasterisation, not path construction.  The tick interval is therefore the knob
that trades smoothness against CPU.

**Deliberately unchanged:** `breath` / `disco` / `steady` keep the zone colours
(they *are* the zone colours, pulsing); disco is not treated as a rainbow.

**Verified** (headless GTK at 260x340, real widget draw onto a cairo surface,
pixels sampled): with zones set red/green/blue, `breath`, `disco` and `steady`
render **byte-identical** to the zone-colour pass, while `rainbow-shift` and
`rainbow-breath` — and the checkbox on its own — shift **45 % of pixels**, take
yellow pixels from 93 to 6282 and put violet (r and b high, g low) at the tail
where the steady pass has none.  Switching back to `steady` restores the
original pixels exactly, so the repaint hook works in both directions.  For the
animation: running the real main loop and sampling frames, **8 of 8 frames
differ** while shifting and **1 of 5** while steady; unmapping the page freezes
it (identical frames 400 ms apart) and remapping resumes it.

**Tests: 228 -> 230** (`python -m pytest tests/`, GTK-free): the sweep spans
exactly `_RAINBOW_TURNS` from nose to tail and no more, the three zone stations
stay within ~30 deg of each other (the "two zones look the same" property),
no jump between neighbouring stations, every station passes through all three
primaries over one period, the phase rolls the sweep *nose to tail* (and the
nose leads the tail by exactly the sweep width), `_colour_at` agrees with
`zone_colour` for triples, and `render_lighting` samples a callback across the
whole body while a flat grey through the zone path leads on one channel only.
`_FakeCR` now records `set_source_rgb` fills separately from
`set_source_rgba`, since the latter also carries the neutral structure greys.
`tools/preview_mouse3d.py` writes `lighting_rainbow.png` (at phase 0).

**Third pass: the direction, the rate, and the width were all wrong.**  The
user: *"still the wrong effect, the effect rolls from top to bottom and the its
slower, ususally two zones are the same color."*  Three separate faults, and
the third was a design error rather than a tuning one:

- **Width.** `_RAINBOW_TURNS = 0.85` laid most of the hue circle along the mouse,
  so the zones came out 105/93 deg apart — three visibly different colours at
  once.  The effect is mostly *one colour* that drifts.  It is now **0.2**: nose
  to tail ~72 deg, and across the three zone centres (y = -0.30, 0.04, 0.35) the
  gaps are **25 and 22 deg**, which is why "usually two zones are the same
  colour" is now true by construction rather than by luck.
- **Direction.** `(y + 0.5)` put the nose *behind* the tail in the sweep, so the
  pattern slid upward.  Flipped to `(0.5 - y)`, so a phase advance puts on a
  station the colour that sat nearer the nose a moment ago and the sweep travels
  top to bottom.
- **Rate.** 4 s for a full hue cycle flashed.  Now **15 s (24 deg/s)**.

The width and the rate are coupled, which is worth knowing before touching
either: a *phase* turn translates the pattern `1/_RAINBOW_TURNS` body lengths, so
one colour crosses the mouse in `_RAINBOW_TURNS * _RAINBOW_PERIOD_MS` = **3 s**.
Narrowing the sweep for free is not possible — it speeds the roll up in
proportion.

**Verified live** (real window at 2540x1385, three `grim` frames of the preview
1.2 s apart): each frame is one ~72 deg slice, the hue advances ~24 deg/s, and
frame 2's magenta at the nose is at the *tail* by frame 3 — the roll is top to
bottom.  **29.2 %** of the preview's pixels differ between frames.  230 tests
pass; the animation's own cost is unchanged (17 ms/frame, above).

Still open: the *breath* effects show no pulse — the same machinery would drive
them (a brightness multiplier over the same frame), it just has not been asked
for.  Nothing in `tests/` can cover the timer: it needs a realized widget and a
running main loop, so it is covered by the in-process runs above.  The preview
label remains untranslated in all 10 catalogs, as before.

---

## 2026-10-05 (cont.) — the click panels and the side buttons were the wrong shape

The user, with a hand-annotated screenshot (`aerox5_3d_files/error_shape.png`):
*"the keycap shape is slightly wrong and the sidde top buttons are as well. i
have marked the shapes with red lines."*  Two faults, and both turned out to be
the *drawn outlines* rather than the projection: `_cap_on_surface()` and
`_cap_on_flank()` were still re-seating the extracted outlines through the
2 mm `top_surface_z` / `side_surface_x` lookup grids.

**Root cause: the re-seating was written for the old extraction.**  Those
helpers (and their docstrings, which still said the outlines "are planar -- they
come off the plane the source OBJ built them on") date from
`tools/extract_aerox5_mesh.py`, whose `cap_outline()` *did* return a convex hull
on a constant-``x`` plane.  The v3 extractor that actually produced
`aerox5_mesh.py` stores **real 3D outlines** -- the side buttons are the groups'
own free-edge rims and the keycaps carried per-point cap-top heights -- so
re-seating threw the real geometry away and replaced it with a bilinear lookup
whose gradient steps at every cell edge.  Measured against the exact OBJ
surface, on `main_button_right` that is 0.84 mm mean error (vs 0.21 mm for the
stored heights) and 3.18 mm at the shoulder, moving the drawn edge by up to
**17 px** right where the user circled it.  On the side buttons the grid pulled
every outline 0.7-1.8 mm inside its own rim (mean 5-12 px, up to **23 px** on
the flick rocker).  Both helpers now apply only their `lift`; the shape is the
extracted one.  `_snap_to_shell()`, whose only caller was `_cap_on_surface()`,
is gone.

**The keycap outlines were also the wrong *curve*.**  `CAP_OUTLINES` came from
`cap_plan_curves()` -- a trace of a mask of the caps' **top faces** -- which is
not the part line: it sits inside the real rim by the width of the shoulder
skirt (4-5 mm where the cap curls over the shoulder) and, knowing nothing about
the wheel, overruns the centre channel by ~4.5 mm and cuts across the wheel
well.  It also drew *both* panels from the left one's plan curve, on the
assumption that the model's wider right-hand shoulder was the broken side.  The
measurements say the reverse: the left rim stops ~4 mm short of the shell
silhouette (the thumb side is taken up by the side-button housing) while the
right rim reaches it, so mirroring the left was exactly what left the right
panel visibly narrow.  Each panel now keeps **its own** free-edge rim.  The raw
rim is the lattice staircase, so it is arc-length resampled to 200,
circular-smoothed (`circ_mean`, window 5) and resampled to 128 -- within 0.24 mm
of the rim on average and under 1 mm at worst, against the 4-5 mm it is fixing.

`CAPS`/`CAP_OUTLINES` are regenerated output: the extractor is byte-reproducible
(5.6 s, verified by regenerating to a temp path and diffing), and the new run
changes **only** the four keycap lines.  The superseded mask tracer
(`cap_top_mask` / `cap_plan_curves` / `cap_loop`) is left in place, unused and
labelled as such -- it is a working technique, just not the part line.

`test_keycaps_have_split_channel_after_the_wheel` had pinned the artefact: it
asserted the channel "pinches to a hairline between the wheel well and the CPI
pill" (`min |x| < 0.5 mm`).  The real rim holds a steady **5.3-5.5 mm** stand-off
the whole way back -- clear of the wheel, whose half-width is 4.25 mm -- so the
assertion now requires the split to stay open (`> 4.5 mm`) instead.

**Verified** by overlaying the drawn outlines on the OBJ free-edge rims in the
buttons page's exact camera: the keycaps track their rims (under 1 mm) and the
side buttons sit uniformly `lift` outside theirs.  **281 tests pass.**  No
`.po`/`.mo` touched.

---

## 2026-10-05 (cont.) — the RGB page becomes one lighting mode, and the two effects the mouse cannot store

The user: *"there is a drop down list of effects wich are consingly named and
seem to do the same things. there is a button to enable rainbow effect and
thats it."*  The page was *correct* -- PLAN.md §2's four independent groups, and
it passes the §1 semantics matrix -- but it was a bad interface, because the
four groups genuinely interact in the hardware and the page never said so.

**The dropdown really was an effects list that wasn't one.**  It maps to
`--default-lighting` (`off | reactive | rainbow | reactive-rainbow`), which is
*wake and startup* behaviour only and does nothing at the moment it is set
(§1).  Of its four effect-looking names, `rainbow` and `reactive-rainbow` are
observationally identical on the device and `reactive` is inert unless a
reactive colour is set.  Rainbow was a bare checkbox in its own section,
disconnected from the zone colours it silently overrides -- and rainbow-on with
wake `off` is a completely dark mouse (§1 matrix row 2), which is exactly the
trap the old page let a user build.

**What GG offers, checked against the installed profiles rather than the web.**
GG's Aerox 5 Wireless panel offers Steady, ColorShift, Multi Color Breathe and
Disable Illumination over three zones, plus a Reactive layer limited to a steady
flash colour or off.  Steady/Disable, Rainbow and Reactive are onboard and
rivalcfg reaches them; **ColorShift and Multi Color Breathe are not** -- GG
animates those on the PC and never writes them to the mouse (its own guide calls
the colour customisations "software driven").  Two web claims had to be
discarded: `--led-brightness` and rainbow *zone subsets* are real, but only for
the **wired** `aerox5.py`/`aerox3.py`/`rival3_gen2.py` profiles, whose
`rainbow_effect` is a `choice` and which carry `led_brightness`.  This device's
`aerox5_wireless_wired.py` has `rainbow_effect` as a `value_type: "none"` flag
and no brightness setting, which is what the live caps say:
`has_light_effect False`, `rainbow_kind 'flag'`, `has_led_brightness False`.

**One effect choice.**  The left card is now LIGHTING MODE -- a radio list,
Steady / Rainbow / ColorShift / Color Breathe / Off, each with a one-line
description -- and everything else is visibly subordinate to it: ZONE COLORS
goes insensitive with a one-line reason ("Rainbow replaces these colours." /
"The palette replaces these colours." / "The LEDs are off."), PALETTE + Speed
appear only for the host-side pair, and CLICK FLASH is renamed and states
outright that it is independent of the mode.  The raw
`default-lighting` combo is demoted into a collapsed `Gtk.Expander` (the repo's
first), labelled as wake-only: "Normally set by the lighting mode above; change
it here only if you want the two to differ."  APPLY is unchanged, and so is the
right column -- the colour editor and the 3D preview are untouched.

`device_core.lighting_mode_state(mode, zones, flash_hex, palette, wake)` is the
single pure translation from the one visible choice to the four device flags,
kept beside `build_lighting_plan()` and tested without a display.  It fixes the
dark-mouse trap by construction: **Rainbow sets `default_lighting=rainbow`**, so
the animation survives sleep instead of being forgotten; `Off` is three black
zones *and* wake `off`; `steady` deliberately keeps the user's wake value rather
than deriving one, because a wake value the user cannot influence is the
"dropdown that does nothing" defect this page exists to fix.  §1's send order
(zones -> reactive -> default lighting -> rainbow last) is untouched.

**The two effects the mouse cannot store are now animated here.**  `lighting_fx.py`
is the maths and nothing else -- pure functions of time, so ColorShift's palette
walk, the zone spread and the breathe envelope test without GTK or a device;
`colours_at()` returns one colour per zone for a given instant, and the preview
and the hardware are driven by the same call, so they cannot disagree.
`device_core.LightingAnimator` owns **one** `Mouse` from
`rivalcfg.mouse.get_mouse()` on a worker thread and writes the three zones at a
conservative tick.  It never calls `save()` -- that is the separate, explicit
call that writes onboard flash, and an animation frame must not touch it -- so
the effect stays out of the mouse's memory exactly as GG's does, and `stop()`
writes the resting colours rather than leaving the device frozen mid-fade.

**Two bugs the live window found**, both of which only exist because of the
redesign and neither of which any unit test could have caught:

*The preview ignored the mode.*  `_rainbow_active()` asks two controls -- the
Rainbow mode, and the Rival-3-class Rainbow *light effects* -- so it read
`selected_effect`.  This device has no `--light-effect`, but `Default.json` was
saved by the old page and carries `"light_effect": "rainbow"`; profile load
restores it, so the check was true in **every** mode.  Steady and Off drew the
rainbow's hue slice, and its timer never stopped.  Reproduced by rendering both
candidates offline and comparing with the capture: the on-screen preview was
`rainbow_colour(y, phase)`, not the red/green/red the swatches held.  The
`selected_effect` branch is now taken only when the device actually has
`has_light_effect`, where those radios are the effect control; on this device
the mode radios are the only control, so nothing else can ask for the rainbow.
Steady now draws red -> green -> red (the swatches), Off draws nothing
(preview mean falls from `(39, 31, 21)` to `(15, 15, 21)`, the bare shell).

*The battery poll fought the animator for the device.*  The animator holds the
HID handle open for as long as a host-side effect runs, so the 60 s
`--battery-level` subprocess could not open it and the status bar filled with an
`OSError: open failed` traceback that reads as the mouse being broken.  A/B on
the live app: `open failed` while ColorShift animates, "Is the mouse turned on?"
the moment it stops -- the difference is exclusive access, not the mouse's power
state.  `read_battery()` now returns early while `animator.animating`, the same
guard `auto_apply_lighting()` already used, and picks the reading up on the next
tick once the effect stops.  `~/.config/rivalcfg-gui/logs/app.log` records every
spawn, and the guard is visible in it: the 60 s tick fires at `:56`, and
`11:01:56` and `11:02:56` -- both inside a ColorShift run opened at `11:01:31`
-- are absent, while `11:03:56` and `11:04:56`, after the effect stopped, both
appear.  Suppressed while animating, resumed when not: not merely silenced.

**Verified in the real window** (1900x1025 at +10+45, driven through AT-SPI
because `ydotool` click delivery was unreliable in this session, captured with
`grim`): the page opens on the derived mode (this profile's raw flags derive to
Rainbow, so the old profile does not silently lose it); the mode sweep greys and
ungreys the zone rows with the right reason line, shows PALETTE (4 swatches) +
Speed only for ColorShift/Breathe, and the preview follows the mode in every
case -- the host-side Breathe pulse was measured rather than assumed, across
three frames ~0.9 s apart, its preview mean rising `(39, 28, 23)` ->
`(50, 39, 22)` -> `(51, 45, 21)`, so it is really animating and not drawn once
at phase 0; the Advanced expander opens to the raw wake combo reading `rainbow`;
CLICK FLASH stays live in all five modes; and the colour editor, the hue strip
and the preview's camera are unchanged.  **288 tests pass** (the 230 above plus
`lighting_fx`'s 20 and the mode -> raw-state cases).  The 30 new `_()` strings
are appended to `locales/en/.../rivalcfg_gui.po` in the file's append style; the
other nine catalogs and the `.mo` files are untouched, as before.

**Still open, and it is the hardware half.**  The redesign changes *how* a
setting is chosen, not what is sent, so §1's 8/8 matrix wants re-running against
the connected 1038:1852; whether `--top-color 000000` truly extinguishes the
LED decides whether `Off` should instead be the verified rainbow-flag-on +
wake-off state; whether Rainbow's `default_lighting=rainbow` survives a real
sleep/wake cycle; and the animator's rate wants an hour on the device to
confirm it does not drop the 2.4 GHz link or hang the mouse.  That rate is
`ANIM_COMMAND_DELAY = 0.016` (a frame is three writes, so ~48 ms of sleeping
and `ANIM_FRAME_INTERVAL = 0.05` caps it near 20 fps) -- deliberately between
the library's own `0.001` floor and its warnings about hanging the device, and
its `0.05` default, which would cap the animation at ~6 fps.  It is the knob to
raise if the hardware proves unhappy, and it is the one number here that a
screenshot cannot check.  None of those can be settled from a screenshot.

The mouse answered nothing at all this session, so the hardware half is blocked
rather than merely un-attempted: with the page switched back to the stored
Rainbow mode -- which stops the animator -- `rivalcfg --battery-level` still
returns *"Unable to get the battery level.  Is the mouse turned on?"*.  That is
the off-or-out-of-range answer, not the `open failed` collision the animator
causes, so it isolates the two: the receiver enumerates and the handle is free,
it is the mouse itself that is not replying.  It follows that `animator opened
(3 zones, ...)` in the log proves the HID interface was acquired, not that
anything lit up -- what the preview showed this session is what was verified,
and the LEDs still want a powered, in-range mouse.

---

## 2026-10-05 (cont.) — the click-panel fix, re-applied; and the paddles' square tails

The user: *"well thats a lot worse than what it used to be, the whole shape is
wobbly the right keycap is so big again both problems we fixed a few sessions
ago.  and you dint take the error_shape.png image i drew to indicate the correct
shape it should in consideration at all."*

**The fix above was not in the working tree.**  The entry before this one
describes the seated-outline and keycap-rim repair, and every symptom the user
names is a symptom of the *pre-fix* state: `_snap_to_shell()` was still defined
and still called by `_cap_on_surface()`, `tools/extract_aerox5_v3_mesh.py` still
built both panels from `cap_plan_curves()`, and
`test_keycaps_have_split_channel_after_the_wheel` still asserted the hairline
(`min |x| < 0.5 mm`) that only the mask trace produces.  A later edit had
reverted the four files to what they looked like before that entry, so the mouse
on screen was the wobbly one with the over-wide right panel.  Nothing here is a
new idea: it is the same repair, put back.

**Re-applied, identically.**  `_cap_on_surface()` and `_cap_on_flank()` apply
only their `lift` again and `_snap_to_shell()` is gone; each keycap keeps its own
free-edge rim, arc-length resampled to 200, circular-smoothed (`circ_mean`,
window 5) and resampled to 128; `CAPS`/`CAP_OUTLINES` are regenerated and again
only the four keycap lines move.

**The wobble is now measured, not eyeballed.**  Turning angle over a ~1.2 mm
window, on the drawn outlines at the buttons page's camera:

| outline | pre-fix | now |
|---|---|---|
| `button1` | max **70.6°**, 20 turns > 35° | max 50.6°, 3 turns > 35° |
| `button2` | max **180.0°** (a degenerate backtrack), 31 turns > 35° | max 50.7°, 3 turns > 35° |

The three survivors are the real rectangular tab in the inboard edge, which is
in `aerox5_mesh.py` itself.  The channel stand-off is a steady 5.43-5.46 mm on
both panels, clear of the wheel's 4.25 mm half-width.

**The paddles' tails are a genuine second fault, and they are the mark that `]`
in the annotation brackets.**  `model.py`'s side buttons are rounded rectangles
whose corner radius is *half their height* -- `side_button_2` is
`rrect(75.3, 25.4, 23.5, 4.8, 2.4)`, centred, so both ends are semicircles.
`side_button()` builds its patch by keeping a lattice cell when *any* of its four
corners falls inside the polygon, so the part line the OBJ carries is that
outline dilated by one 0.45 mm cell, and the extractor then decimates the
staircase to 64 points -- the arcs do not survive, and both ends come back as a
flat face with two shallow chamfers.  The drawn bbox still holds the model's
corners, so `_round_ends()` rebuilds the rounded rectangle inside it at the
model's radius (`_SIDE_FILLET_MM`: 2.4 / 2.4 / 2.6 mm for buttons 4 / 5 / 9),
refusing any loop that is not that rectangle (a rounded-box SDF test, 0.5 mm of
slack) and so leaving 7 and 8 alone -- they are the artificial halves of one
`ROCKER`, not shapes the model draws.  Each rebuilt point takes its `x` from the
nearest point of the extracted loop, so the flank's own curvature is kept.

Span across the tail at depth `d` from the tip, against the model's semicircle
`(h - 2r) + 2*sqrt(r² - (r - d)²)`:

| d (mm) | 0.05 | 0.20 | 0.50 | 1.00 | 2.40 |
|---|---|---|---|---|---|
| button4 drawn | 1.22 | 1.99 | 2.93 | 3.88 | 4.76 |
| model | 0.97 | 1.92 | 2.93 | 3.90 | 4.80 |

button5 agrees to the same order.  Before the change the same bands gave the
full 5.15 mm width at `d = 0.5`, i.e. a square cut.

**Verified** by registering the annotation back onto a render (9/9 label anchors
matched by ICP, max residual 1.67 px, `scale 1.39689`, `t = (-300.5, -338.2)`),
then drawing the buttons page's outlines through that mapping: the red S-stroke
in cluster A lies on `button1`'s inboard edge, and the `]` bracket lies across
`button4`'s tail.  The bracket's station reads as 80.8 mm on the paddle and
79.1 mm on the rocker, i.e. it sits where the rocker's own tail ends (80.0 mm);
the reading taken is the plain one -- the end is cut square, make it the
semicircular cap the model draws -- because that is the only defect at that end
and `_round_ends` reproduces the model to 0.25 mm.  **288 tests pass**; the four
keycap lines are still the only diff in the regenerated mesh; no `.po`/`.mo`
touched.

**Still open:** the bracket is ~5.5 mm inboard of the drawn tip, so if the user
meant "the paddle should *end* there" rather than "round it off", buttons 4/5
are 5.5 mm too long and `model.py` is what would have to move.  The rocker halves
(7/8) are still cut square where they meet the flank's front edge, which
`_round_ends` deliberately skips; whether the model wants them rounded is
unsettled.  And none of this has been seen in the running GUI with a real
pointer -- it is all measured off the renderer.

Seen in the running GUI, though, is a **pre-existing layout fault on the same
page**: launched at its default 945x1025, the notebook allocates the canvas
~1095 px wide while only ~685 px of it are on screen, so the mouse is cut off at
the right edge and the whole right chip column (`DPI`, `Right click`,
`Middle click`) is drawn past the window and never appears.  Nothing here
touches it -- the allocation is set in `create_buttons_page`, which is the other
session's file region -- but it is why the buttons page looks truncated when the
window is not near-maximised.

## 2026-10-05 (cont.) — the right keycap was 4.2 mm too wide; the pair is mirrored again

**The user reported the keycaps were wrong and, on seeing the result, that
"the right keycap is so big again" — a phrase that appears verbatim in the
2026-10-04 entry above.**  That was the clue: this was not a new defect.  The
2026-10-04 work had made the two click panels a mirrored pair by drawing both
from the *left* panel's plan curve (`cap_plan_curves`).  The later
2026-10-05 free-edge-rim extraction replaced that with a per-panel rim taken
from the mesh itself, and **`model.py` breaks the flank asymmetrically** —
`zlim` ramps on one side and is constant on the other — so the two `click_*`
groups in the v3 mesh are not mirrors of each other.  Tracing each cap from
its own rim silently reintroduced the 2026-10-04 defect.

Measured on the current code: the left cap spans x -27.55 .. -5.31 mm and the
right x 5.29 .. 31.76 mm -- **26.46 mm wide against 22.24 mm**, i.e. 4.2 mm
wider through the shoulder.  In the top view the right cap's outer edge bulges
out to the shell's own silhouette while the left one sits well inboard of it.

**The fix** is in `mouse3d._cap_on_surface`, which grew `source_key` and
`mirror_x` parameters: the right cap is now drawn from the left panel's rim
with its x negated.  Pure mirror -- y and z are carried across unchanged.
Every attempt to re-seat the mirrored cap on the *right* side's own surface was
abandoned as unreliable: `top_surface_z` reads up to 3.98 mm above the left
rim's own z and 6.72 mm above the right's; a `_mesh.RINGS` upper-envelope
lookup is off by up to 36 mm; there is no loop-index correspondence between the
two rims (best cyclic offset leaves a 19.13 mm median plan distance); and a
KNN-median z lookup on the right rim is contaminated where the outline doubles
back near the nose sliver (58/128 points differ by >1 mm from the mirrored-left
z, some at 0.14 mm plan distance).  The mirror keeps the left cap's own heights,
which are the ones measured off the part.

**Verified** numerically after the edit: left x -27.55 .. -5.31, right x
5.31 .. 27.55 mm, **both 22.24 mm wide**; centroid sum 0.00000; worst
point-wise mirror residual 0.0000 mm.  Re-rendered plan views
(`/tmp/top_buggy.png` vs `/tmp/top_fixed.png`, comparison at
`/tmp/keycap_before_after.png`) show the bulge gone.  **288 tests pass.**

`test_click_panels_are_symmetric` had been letting this through: it asserted
only the centroid with a 0.03 tolerance (3.85 mm), while a 4.2 mm width error
moves the centroid just 1.6 mm.  It now checks the outline itself -- every left
point must have a right point within 0.005 (0.64 mm) of its mirror image, and
the two caps' outer extents must agree to the same tolerance.  Confirmed it is
a real regression test by restoring the pre-fix `mouse3d.py`: it fails with a
residual of 0.054 (6.92 mm), and passes at 0.0000 with the fix in place.

**Still open:** what the red S-stroke in the user's `error_shape.png` asks for.
Raw pixel measurement put cluster A down the **middle of the dark channel**
between the two caps (y=300 x≈476-479, y=380 x≈479-491, y=460 x≈467-473,
y=500 x≈431-439) with a branch arm to the right at y≈360-370, not on either
cap's edge -- so it is *not* satisfied by mirroring, which leaves the left cap
untouched.  If it is the inboard edge of the left keycap that is wrong, the fix
is a change to `main_button_left`'s rim, not to the right cap, and the user
should say which edge the stroke is on.  The app is running on the fixed module
(pid 693478); it has not yet been looked at with a real pointer on the buttons
page.

## 2026-10-05 (cont.) — the click panels rolled back to the committed mesh

**This supersedes the entry above.**  After that entry the user asked why the
code was not simply rolled back, and whether this was even a git repository.
It is: branch ``overhaul``, and there had been **no commit since ``dbafeab``**
with 30 files modified, 2 untracked and 5078 insertions sitting in the working
tree.  (Earlier in the session I had repeated a claim that this was not a repo;
that was wrong — it came from the harness banner, which had evaluated the
question against ``/home/nazar`` after a stray ``cd`` of mine, not the project.)

**First, a recovery point.**  ``git add -A`` → ``write-tree`` → ``commit-tree``
→ ``update-ref refs/heads/wip-2026-10-05``, then unstaged, leaving the working
tree, the index and ``overhaul`` untouched: ``70bffa1`` holds the whole tree at
15:2x including the untracked ``lighting_fx.py``.  A dropped stash from 10:13
today (``822409b``) was also found still dangling in the object store.  Nothing
was thrown away to make room for the rollback.

**The root cause of the regression, exactly.**  The mirroring was never in
``mouse3d.py`` — it was in ``tools/extract_aerox5_v3_mesh.py``.  Commit
``be022ad`` ("mirror the click panels off one plan curve") traced *both* panels
from the **left** panel's top-face plan curve and reflected it about the
centreline.  The later uncommitted work replaced that with each panel's own
free-edge rim off the OBJ, which dropped the mirror; the model breaks its flank
asymmetrically, so the right cap came back 4.2 mm wider.  The same edit deleted
``_snap_to_shell``, so the panels stopped being seated on the crown.

**The rollback, to the mesh only** (the user's instruction: leave the RGB work
alone).  The click-cap block in ``main()`` is back to ``cap_plan_curves`` +
``cap_loop`` from the left panel, mirrored; the unused ``CAP_RIM_*`` constants
keep a "superseded" note in the file's existing idiom.  ``aerox5_mesh.py`` was
regenerated with ``tools/extract_aerox5_v3_mesh.py`` — reproducible, confirmed
byte-identical beforehand — and every ``CAPS`` entry now matches ``HEAD``
exactly.  In ``mouse3d.py``, ``_snap_to_shell`` is restored and
``_cap_on_surface`` is back to its committed form; the ``source_key`` /
``mirror_x`` parameters and the mirror hack on ``button2`` are gone, because the
mesh carries the mirroring again.

**Kept:** the whole RGB lighting path (``render_lighting``, ``upright_roll``,
``zone_*``, ``build_lighting_geometry``), the honeycomb rework, ``_round_ends``
on the flank paddles, and ``_cap_on_flank``'s extracted-loop seating.  The two
renders bear this out: after the revert the buttons view is **pixel-identical
to the committed state above y = 387** — that is, the keycaps match exactly —
and the only differing pixels are the flank paddles, which keep their rounded
ends.

**Tests.**  Two had been written against the per-panel rim and were encoding the
approach being reverted.  ``test_click_panels_are_symmetric`` now checks the
mirror in **plan only**: the heights are taken per side, from that side's own
top faces, so a correct pair differs in ``z`` by up to 4.8 mm and a 3D mirror
test fails on it.  ``test_keycaps_have_split_channel_after_the_wheel`` asserted
the split stays >4.5 mm wide between 40.5 and 44.5 mm; the restored outline
shares the channel's inner edge and closes onto the centreline at the wheel
well, so it is replaced by ``test_keycaps_leave_a_centre_channel``, which holds
the inner edge ~4.9-5.1 mm off the centreline over the body and asserts neither
cap crosses onto the other's side.

**Verified.**  ``CAPS`` byte-identical to ``HEAD``; the restored plan pair
mirrors to 0.1855 mm and the outer extents agree to 0.02 mm; the buttons render
matches the committed one above y = 387; **288 tests pass**; and the reverted
mesh file is caught by the new test at a 4.67 mm residual, so the regression is
locked.  The app is running on the reverted code (pid 696013).

**Still open:** the git state is still what the user was angry about — 30 files
uncommitted on ``overhaul``; they now exist only in the ``wip-2026-10-05``
snapshot commit.  A stale ``.git/AUTO_MERGE`` from an aborted merge is still
lying around.  And the red S-stroke in ``error_shape.png`` is still unexplained:
its raw pixels run down the middle of the centre channel (y=300 x≈476-479,
y=380 x≈479-491, y=460 x≈467-473) rather than along either cap's edge.

## 2026-10-05 (cont.) — the keycap seam the user drew is now geometry

**What the annotation is.**  ``aerox5_3d_files/error_shape.png`` is a
screenshot of the button page with two strokes drawn over it *by hand*.  The
long one runs down the seam the left click panel (``button1``, the lower of
the two keycaps) leaves for the two top paddles -- ``button7``/``button8``.
It is not a marker pointing at a defect; it is the shape that edge should
have.  The short bracket further back sits across ``button4``.

To read it in the app's own frame the screenshot has to be registered against
a headless render of the same view.  The transform is
``ann = 1.27 * render + (-352, -210)`` at ``fit_view(1400, 900)``: silhouette
IoU **0.9705**, chamfer 6.85 px annotation→render / 3.73 px render→annotation.
The offsets in the earlier scratch file were the same two numbers
*transposed* -- ``(-210, -352)`` -- which is what made every overlay line up
with nothing, and cost a lot of this session.  The annotation is also clipped
at its right edge (mask bbox 878 of 879), so its width cannot be used to
estimate the scale; the height gives 1.270.

**Fitting it** (``tools/fit_seam_correction.py``, new).  The red pixels are
two connected components; each is read as a centre line (per row, the
left-most ink, stepped in by half the nib -- the long stroke throws a branch
to the right around the paddles' ends, and tracking the left edge ignores
it), then smoothed to drop the hand's tremor.  Every outline vertex is
projected, put into annotation pixels through the transform above, and pulled
onto the nearest point of the stroke.

The weight is what makes it a correction rather than a mess: a vertex
follows the stroke only while the outline and the stroke run *along* each
other, ``|cos|**4``, cut off past :data:`NEAR` = 45 px.  Two earlier rules
both drew a wobble.  A hard distance band ``(12, 30)`` px leaves a ridge --
it moves only the vertices 12-30 px out, so the edge steps off the band at
both ends.  A quadratic taper in distance alone refuses the widest part of
the correction (the seam is genuinely ~41 px off the edge there).  The
parallelism weight takes the correction back off exactly where it should:
where the stroke leaves the seam and runs along a paddle.

**What moved.**  ``button1``: 317 of 960 outline vertices, one run from
vertex 346, peak **4.83 mm**.  ``button2`` is the mirror of ``button1``
re-seated on its own side's crown (``_mirror_panel``), so the pair stays an
exact mirror as ``test_click_panels_are_symmetric`` requires -- building it
from the mesh's own right outline would have broken that the moment the left
panel carried a correction.

The paddles are **not** moved.  Where the stroke runs along their ends it is
0-4 px from their edges already -- there is nothing to correct -- and the
hooks at either end of it, 50-80 px out, are where the pen left the seam.
Fitting them (an earlier attempt) pulled ``button8``'s end into a point.

**Verified.**  Mean distance from the drawn stroke to ``button1``'s outline
falls 24.4 px → **2.8 px** (median 25.6 → 1.7), and the vertex spacing stays
continuous -- no ridge.  ``button7``/``button8``/``button4``/``button5``/
``button9`` are bit-identical to ``HEAD``.  The channel between the panel's
edge and the paddles is preserved (it closes to a seam where the drawing
says it should).  **290 tests pass**, including two new ones: the table
displaces only the traced run and leaves the ends of each run on the
untouched outline, and the side buttons are untouched.

**Still open:** the bracket on ``button4`` is read but deliberately not
fitted.  It spans the pill exactly from its top edge to its bottom edge
(~42 px tall) about 5.6 mm before the pill's end, and its two hooks curl back
along the long edges -- which reads as "this paddle's tail should be cut
square here", i.e. ~5.6 mm shorter, not as an edge to snap to.  That is a
structural change (the outline has to be rebuilt, not nudged) and needs the
user's confirmation of the reading.  The git state is also still what the
user was angry about: 30 files uncommitted on ``overhaul``, plus the stale
``.git/AUTO_MERGE``.

## 2026-10-05 (cont.) — the bracket on button 4 was the cut, and it is now made

The user confirmed what the short red stroke on ``button4`` means: it is the
end they want, not a marker pointing at a defect.  *"the red lines/pixels
were drawn on by me with my fucking mouse ... they are corrections to the
shapes of the keycaps and side buttons i wanted."*  Asked how to read it,
they chose **cut the tail back to it** -- clip the outline at the drawn line
and close it there.

**The reading the earlier entry flagged as needing confirmation held up.**
The two ends of the stroke land within 3 annotation px of the paddle's own
long edges, and every row of it read back onto the surface the pen was over
lands at essentially one station.  So it is a cross-section: the paddle is
cut, not nudged.  The tail tip is drawn at model ``y`` 22.95 mm; the line is
at **16.07 mm** (median of 31 stations on the bracket), so the paddle is
**7.1 mm shorter** -- 23.5 mm long becomes 16.4 mm.

**Implemented as a rigid move of the end the outline already draws**, not a
rebuild (``_cut_tail`` in ``mouse3d.py``, driven by ``_TAIL_CUT_MM``).
The pen's arc bulges only ~0.55 mm -- it traces the outline's own flat face
with its corners rolled into the long edges, not the 2.4 mm semicircular
capsule the mesh's rounded rectangle would give -- so inventing a new end
would be worse than moving the one that is there.  Every point that comes
back is a point that went in, moved or dropped; nothing is interpolated,
which keeps the seating's ``x`` fallback meaningful.  ``_round_ends``
(the capsule rebuild) stays unused.  ``button5``/``button7``/``button8``/
``button9`` are untouched.

**Two bugs in the fitter, both mine.**  (1) The seam fit was not
reproducible: it measured against ``mouse3d.BUTTONS[key][1]``, which is the
*corrected* outline, so it was fitting its own residual and a re-run would
have applied the correction twice.  It now fits against the uncorrected
seating (``_cap_on_surface``/``_cap_on_flank`` of the mesh cap); the
regenerated ``_SEAM_FIX`` is **byte-identical** to the committed one.  (2)
The first cut estimator held ``x`` at the *planar* extracted value, but the
stroke was drawn over the *seated* surface, so it solved to a point up to
2 mm off the part.  ``on_flank`` now inverts the whole seating map
(``x = side_surface_x(y, z) - lift``) by Newton; the rows then walk the
drawn height 27.73 → 23.01 mm and overshoot to 22.61, exactly as drawn.

**Verified.**  button4's outline goes 384 → 287 points, max ``y`` 22.95 →
**16.10 mm**, z range unchanged (the cut face's z values are identical
before and after).  Re-running ``tools/fit_seam_correction.py --write`` is a
no-op (byte-identical output).  **293 tests pass**, including three new ones:
the cut lands on the named station and carries the same end profile; a loop
the plane does not cross twice (including one already at or in front of the
station, and one made only of long edges) comes back untouched; and every
flank button not named in ``_TAIL_CUT_MM`` is exactly the plain re-seating of
its mesh cap.

**Still open:** the ``_TAIL_CUT_MM`` comment quoted the hand measurement
(15.8 mm) rather than the fitted station until this pass; the GUI has not
been restarted on this code, so the shortened paddle has not been seen on
screen yet; and the git state is unchanged -- ~30 files uncommitted on
``overhaul`` plus the stale ``.git/AUTO_MERGE``.

## 2026-10-05 (cont.) — the seam is estimated, not traced; and the flick lever stops crossing the keycap

The user's verdict on the traced seam was exact: *"you have traced my keycap
red lin perfectly but that was wrong. its hand drawn and has errors, you were
supposed to use it to estimate the proper lines instead you copied it
directly. now its wobbly and a droop near the bottom."*  Both symptoms were
the trace following the pen rather than the edge.  The wobble was the hand's
own +-2-4 px row-to-row tremor, copied exactly.  The droop was the pen
running off the seam at the bottom onto the keycap's bottom edge -- it dives
about 49 degrees over the last rows (y 462 -> 517 ann, x 471.5 -> 408.5) --
and the trace chased it, dragging the drawn edge ~46 px down and left.

``tools/fit_seam_correction.py`` no longer uses the stroke's coordinates at
all; it estimates the line they describe.  ``seam_estimate`` median-filters
the stroke's ``x`` (which is also what kills the branch the pen threw off at
y ~345-375, where ``stroke_centre_lines`` takes the row midpoint and ``x``
spikes ~14 px -- that jump had been driving every polynomial fit until the
clipping ate it), measures the stroke's direction angle from vertical, keeps
only the run that follows the seam, cut at the first station past 30 degrees,
fits a **sigma-clipped cubic** through it in the stroke's own arc length, and
carries the polyline straight on past each seam end by ``SEAM_FADE`` px so the
correction can ease out over that distance instead of being yanked sideways at
the last knot.  ``nearest_on`` now reports whether the closest point is one of
the line's ends, and those vertices are skipped outright.  The estimate's own
turn is 0.17 degrees max, 0.148 rms -- the line is smooth by construction --
and the fitted edge sits 0.0-1.5 ann px from it through the seam body, against
2.78 rms / 14.41 max for the raw ink.  The regenerated ``_SEAM_FIX`` starts at
vertex 342 and peaks at 5.07 mm.

**The flick lever was never longer than the thumb buttons.**  All four paddles
are drawn from the same leading station (``y`` ~-23 mm) because the mesh's
lever and both thumb buttons start at ``BTN_Y0 + 50``.  The lever clips anyway,
and the reason is height, not length: it sits ~7 mm further up the flank
(``z`` 29.5-34.1 mm against 22.9-27.9 mm), and at the buttons page's pitch its
leading corners project onto the keycap's silhouette -- about 24 px *inside*
the panel's rear edge, which the seam correction has just put at screen
``x`` ~657.  Nothing occludes it there: the renderer draws every outline, back
faces included, so the leading end is painted straight over the keycap as a
thin wedge aimed up and to the left.

**That wedge is also the whole of the "trend up on the left".**  The drawn
screen tilt of the lever is not out of line with its neighbours -- 80 px of
screen ``y`` nose-to-tail against 61 px for button5 and 72 px for button4 --
and almost all of it is the view, not the model: button4's two ends differ by
0.01 mm in ``z`` and still tilt 72 px.  The lever's own rise is 0.31-0.56 mm
across 39 mm.  What the eye reads as the paddle lifting toward the nose is the
clipped leading end, and it goes with the cut.

**Implemented as the tail cut's counterpart** (``_LEAD_CUT_MM`` and
``_cut_lead``): the loop is handed to ``_cut_tail`` mirrored in ``y``, station
and all, and mirrored back, so there is one copy of that geometry and the two
ends cannot drift apart.  The station is the first one where neither half's
outline crosses the seam line at all -- ``-19.0`` mm, a little over 4 mm off a
39 mm paddle, chosen from a sweep of the seated outlines rather than picked by
hand.

**Verified.**  In the buttons page's own view (1400x900, yaw -120, pitch 42),
both halves go from 29 and 53 vertices inside the keycap's outline to **zero**,
with the leading corner 6 px clear of the seam line -- and button5's corner is
4 px behind that, so the three ends still read as a staircase.  Every other
side button was already at zero and stays there.  The cut end walks out as a
clean flat face (``y`` constant at -19.000 over z 32.15-33.26 mm for one half)
with the original corner roll above it and a 0.18 mm step at the lower corner,
which is inside that corner's own 1.9 mm roll and under a pixel on screen.
**296 tests pass**, including three new ones and three rewritten: the lever
still spans both thumb buttons though it now starts behind button5; the lead
cut lands on its station and carries the drawn profile rather than resampling
it; a loop the plane does not cross twice comes back untouched; and no side
button may put a vertex inside the click panel in the page view -- with the
mesh's own outline asserted to *fail* that, so the test cannot decay into
testing an already-clear shape.  ``tools/fit_seam_correction.py --write``
still rewrites only ``_SEAM_FIX`` and ``_TAIL_CUT_MM``, whose regexes stop at
the first unindented ``}``, so the new table is not swallowed.  The GUI was
restarted on this code and the rocker and seam were checked on screen.

**Still open:** the lever's leading corner is now the *only* thing about its
shape that is not the mesh's -- if the seam correction is ever revised
outward, ``_LEAD_CUT_MM`` wants re-deriving from the same sweep; and the git
state is unchanged -- ~30 files uncommitted on ``overhaul`` plus the stale
``.git/AUTO_MERGE``.

## 2026-10-05 (cont.) — the keycap corner gets a second fit, and button 5 is cut back off it

The user marked up a screenshot of the running page once more
(``aerox5_3d_files/error_shape2.png``): an arc down the left click panel's
rear-lower corner, and a bowed arc across button 5 with an **X** on the
material forward of it.  Same reading as the first annotation -- the ink
*shows* the edge, it is not the edge -- so both went through
``tools/fit_seam_correction.py`` and neither is copied.

**The plane Jacobian was the wrong map, and that was the whole difficulty.**
The cap is not a flat plate: the page lays every outline vertex on the shell
(``z = top_surface_z(x, y) + lift``), so the map the solve has to invert is
``(x, y) -> project(x, y, seat(x, y))``, whose Jacobian folds in the surface
slope ``dz/d(x, y)``.  Differencing the bare projection with ``z`` held fixed
omits that term, and at the rear-lower corner -- where the crown falls away --
the missing piece is up to 2.3x the ``y`` column.  Measured at ann2's
registration, one plane-Jacobian step put the vertex at ``(4.45, 0.03)`` mm
where a full Newton solve puts it at ``(1.69, -1.56)`` mm: the solve walked
the *wrong way*, and the field it returned had no relation to the stroke.
``seated_jacobian`` perturbs ``x`` and ``y`` and lets the seating recompute
``z``; its single step then lands within 0.00-0.12 mm of the Newton answer.
``screen_jacobian`` is gone.

**The two strokes were drawn over different pages, so the fields are chained.**
``error_shape.png`` corrects the outline the *mesh* seats; ``error_shape2.png``
corrects the outline *as the first fit leaves it*.  Each stroke is therefore
fitted against the page the strokes before it draw -- the second's
displacements are measured from the corrected outline, and the two fields are
summed -- rather than both being measured from the mesh.  The margin is not
marginal: ann2's arc sits **15.47 px** (3.16 mm) from the mesh's own panel
edge, **5.95 px** (1.22 mm) from the table the app was actually running when
the screenshot was taken, **2.28 px** (0.47 mm) from a fresh ann1-only refit,
and **0.51 px** from the chained result.  So the two strokes agree with each
other to well inside the ink once the fitter is run fresh, and the droop the
user complained about was the stale shipped table's own error.  Chaining is
also the only composition that is a pure derivation -- fitting ann2 against
the module's shipped table would inherit that table's error, which no longer
reproduces a fit of its own stroke.

**button 5's mark is an X beside an arc, not one zigzag.**  The mark was read
as a single per-row leftmost run, which sampled the **X** -- the removal mark,
drawn *left* of the arc because the paddle's leading end is the left of its
cap -- and returned a bogus ``-22.59`` mm station, a 0.46 mm cut that would
have left the button visibly long.  Splitting the two structures by rendering
them (cyan = X, magenta = bowed arc across the paddle at ``y`` -19.0..-20.4)
settled it, and ``bracket_rows`` gained the mirror of its ``"leftmost"`` rule:
``"rightmost"`` takes the row's last run outright.  The station is now
**-20.34 mm**, a **2.71 mm** cut -- and the arc's own corners land at
``-19.00``, the same station the lever's two halves are cut to, which is the
cross-check that the reading is right.

**A 0.009 mm tail was splitting the emitted table in two.**  ``segments``
called any vertex below its floor dead, and the smoothing left a ripple just
under that floor six vertices from the end of the decay: the field was a
single smooth hump but the table came out as two runs, 58 knots from 345 and 4
more from 677.  Two rules now, both structural rather than tuned: a dip
*narrower than the knot spacing* is kept as part of the run it interrupts (the
interpolation steps straight across something that short, so it is not a break
-- and zeroing it would punch a hole in the correction), and a stretch the
floor does call nothing emits zeros, so the padding knots land on a flat tail
instead of on whatever ripple the decay left behind.  One run, 60 knots from
vertex 345.

**Verified.**  Against the user's two strokes, in each annotation's own
pixels: the arc goes from 5.95 px mean / 4.78 median / 15.71 max (the shipped
table) to **0.51 / 0.52 / 1.58** -- 0.10 mm mean, all fifty samples inside
1.58 px; the long stroke goes from 3.99 / 1.51 / 24.65 (mean / median / max)
to **2.42 / 1.87 / 17.67**, and the count of its 280 rows landing within 5 px
of the drawn edge rises from 222 to **268**, the remainder being the rows
where the pen left the seam and ran round the keycap.  That median is the one
number that gets slightly worse, and it is the chained composition doing what
it is for: the corner now sits between the two strokes, 0.5 px off the arc
and 1.9 px off the long one, where they disagree by about 2 px.  Button 5's
leading end moves ``-23.05 -> -20.30`` mm, carrying its drawn face whole (43
samples across the same 0.0325 model span, none resampled), and no side button
puts a vertex inside the panel in the buttons page's view: button 5's least
clearance is 23.4 px (3.53 mm), and button 8's 6.0 px (0.91 mm) is the
tightest of the three and unchanged from before.  The two marks were also
rendered back over the user's own crop, before and after, and the panel edge
lies on the red arc while the shortened paddle stops at it with the X's
material gone.  ``python3 tools/fit_seam_correction.py --write`` rewrites only
``_SEAM_FIX`` (345, 60 knots) and ``_LEAD_CUT_MM`` (``'button5': -20.3`` added
after 7 and 8), leaves ``_TAIL_CUT_MM`` and the hand-derived ``-19.0``
stations alone, and a second ``--write`` produces no diff.  **296 tests
pass**; ``test_lead_cut_moves_the_end_and_carries_its_profile`` now covers all
three cuts with a per-key "not a nudge" floor (button 5's 2.7 mm against the
lever's 4.1 mm) and pins that the lever's two halves stay on one station.

**Still open:** the lever's ``-19.0`` station was chosen from a sweep of the
seated outlines "rather than picked by hand", and the seam it was derived
against has just moved; it still clears (zero vertices inside, 15.9 px / 2.4
mm of margin to button 7), but the sweep wants re-running against the new
panel if the seam is ever revised outward again.  The ``-8.8e-05`` knot at
vertex 681 is the last two-hundredths of a millimetre of the fit's own tail
ripple and is left in deliberately -- it is 0.01 px on screen -- but it is the
one number in the table that is not a description of anything the user drew.
And the git state is unchanged: ~30 files uncommitted on ``overhaul`` plus the
stale ``.git/AUTO_MERGE``; nothing was committed or rolled back this window.
