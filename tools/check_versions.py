#!/usr/bin/env python3
"""Check that every version string in the tree agrees.

The version lives in four files that nothing links together:
``rivalcfg_gui/__init__.py`` (the source of truth), ``setup.py`` reads it, and
``dist/aur/PKGBUILD``, ``dist/aur/.SRCINFO`` and ``dist/debian/changelog`` each
carry their own copy.  Three releases' worth of drift is why this exists:
``debian/changelog`` sat at 1.4.0 while the package was at 1.6.0.

``.SRCINFO`` is generated from the ``PKGBUILD`` by ``makepkg --printsrcinfo``,
so a disagreement between those two is not a version bump that was missed but a
generated file that is out of date -- it needs regenerating, not editing.

Usage: ``python tools/check_versions.py``.  Exit status is 0 when the four
agree, 1 when they do not, 2 when a file cannot be read.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

#: (label, path, regex) for the version's copy in each file.  The regexes
#: anchor on their file's own syntax rather than searching for a bare number,
#: so a reformat cannot silently stop matching and report agreement.
SOURCES = (
    ("rivalcfg_gui/__init__.py", "rivalcfg_gui/__init__.py",
     re.compile(r'^__version__ = "([^"]+)"', re.M)),
    ("dist/aur/PKGBUILD", "dist/aur/PKGBUILD",
     re.compile(r"^pkgver=(\S+)", re.M)),
    ("dist/aur/.SRCINFO", "dist/aur/.SRCINFO",
     re.compile(r"^\s*pkgver = (\S+)", re.M)),
    ("dist/debian/changelog", "dist/debian/changelog",
     re.compile(r"^rivalcfg-gui \(([^)\s]+)\)", re.M)),
)

#: The reference everything else is compared against -- the package's own
#: ``__version__``, which setup.py already reads by regex.
REFERENCE = "rivalcfg_gui/__init__.py"


class CheckError(Exception):
    """A version that could not be read at all; reported, never swallowed."""


def read_version(label, path, pattern):
    try:
        with open(path, encoding="utf-8") as handle:
            match = pattern.search(handle.read())
    except OSError as exc:
        raise CheckError(f"{label}: {exc.strerror}") from exc
    if match is None:
        raise CheckError(f"{label}: no version found -- did the file's format change?")
    return match.group(1)


def collect():
    found = {}
    for label, relative, pattern in SOURCES:
        value = read_version(label, os.path.join(ROOT, relative), pattern)
        # Debian carries a revision on top of the upstream version
        # ("1.6.0-1"); everything else is the bare version.
        found[label] = value.split("-")[0] if "-" in value and label.endswith("changelog") else value
    return found


def main(argv=None):
    try:
        found = collect()
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    reference = found[REFERENCE]
    for label, _relative, _pattern in SOURCES:
        mark = "ok  " if found[label] == reference else "FAIL"
        print(f"{mark} {label:26} {found[label]}")

    disagree = sorted(label for label, value in found.items()
                      if value != reference and label != REFERENCE)
    if disagree:
        print(f"\n{len(disagree)} file(s) disagree with {REFERENCE} ({reference}):",
              file=sys.stderr)
        for label in disagree:
            print(f"  {label}: {found[label]} (expected {reference})", file=sys.stderr)
        if "dist/aur/.SRCINFO" in disagree:
            print("  note: .SRCINFO is generated -- run `makepkg --printsrcinfo > .SRCINFO`",
                  file=sys.stderr)
        return 1

    print(f"\nall {len(found)} version strings agree at {reference}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
