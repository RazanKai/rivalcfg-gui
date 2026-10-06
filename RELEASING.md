# Releasing

There is no release workflow. Tagging is a manual step done after the checklist
below, on purpose: the version lives in four files, and a workflow that rewrites
them can disagree with what is actually committed. `tools/check_versions.py`
exists to make that disagreement impossible to miss instead.

## Checklist

1. **Bump the version, in every place that carries it.**

   `rivalcfg_gui/__init__.py` is the source of truth:

   ```python
   __version__ = "1.7.0"
   ```

   `setup.py` reads it by regex, so it needs no edit. The other three do:

   | File | Field |
   | --- | --- |
   | `dist/aur/PKGBUILD` | `pkgver=` |
   | `dist/aur/.SRCINFO` | `pkgver =` |
   | `dist/debian/changelog` | the `rivalcfg-gui (X.Y.Z-N)` line |

   `.SRCINFO` is generated — do not hand-edit it beyond the version. Regenerate
   it if the PKGBUILD changed at all:

   ```sh
   cd dist/aur && makepkg --printsrcinfo > .SRCINFO
   ```

   `debian/changelog` needs a new entry rather than an edited one, and the
   revision (`-N`) resets to 1 for a new upstream version:

   ```sh
   cd dist/debian && dch --newversion 1.7.0-1 "Release 1.7.0."
   ```

2. **Run the version check locally.** It is the same script CI runs:

   ```sh
   python tools/check_versions.py
   ```

   It exits non-zero and names the files that disagree.

3. **Run everything else CI runs.**

   ```sh
   python -m pytest tests/ -q
   python tools/check_locales.py
   ruff check .
   ```

   `check_locales.py` is the one to pay attention to before a release: if the
   release added user-visible strings, `en.po` needs them (it is hand-maintained
   — see the README's translation section) and every `.mo` needs rebuilding.

4. **Check the package still installs from scratch.** The one thing the test
   suite cannot see, because it runs from the tree:

   ```sh
   python -m venv /tmp/relcheck
   /tmp/relcheck/bin/pip install .
   /tmp/relcheck/bin/python -c "import rivalcfg_gui, importlib.metadata as m; print(m.version('rivalcfg-gui'))"
   ```

   The `packaging` CI job asserts the assets and all ten `.mo` files are in the
   installed distribution; if it is green, step 4 is a formality.

5. **Commit, then tag.** The tag is `vX.Y.Z`, matching `__version__`:

   ```sh
   git commit -am "chore(release): 1.7.0"
   git tag -a v1.7.0 -m "1.7.0"
   git push origin HEAD --follow-tags
   ```

6. **Package for the distributions**, which are not published from here.

   - **AUR** — the `PKGBUILD` and `.SRCINFO` in `dist/aur/` are the package.
     Once the tag exists, push them to the AUR repository.
   - **Debian** — build from the tag with `dist/debian/` in place:

     ```sh
     cd dist/debian && dpkg-buildpackage -us -uc -b
     ```

     The rules file downloads the pinned `rivalcfg` release itself and verifies
     its SHA-256, so the build needs network access.
   - **Flatpak** — `flatpak-builder` against
     `dist/flatpak/io.github.MrGodzilla38.rivalcfg_gui.yml`.

   All three install the `rivalcfg_gui/` package directory and launch it with
   `python3 -m rivalcfg_gui`; if a release renames or adds a module, they need
   nothing, but a change to the *package directory name* means all three.

## What is not automated, and why

- **No tag-triggered release workflow.** The version strings are the release's
  contract with three separate packaging systems, and a workflow that rewrites
  them can push a tag that disagrees with what a distro built. The check script
  is the automation; the bump stays a deliberate edit.
- **No `.pot` generation.** Most of `en.po`'s msgids are composed at runtime
  (device and button names come from `rivalcfg`, the RGB strings are built), so
  an `xgettext` pass would find a minority of them and mark the rest obsolete.
  Adding a string to `en.po` is a hand edit. `tools/check_locales.py` explains
  this at more length.
