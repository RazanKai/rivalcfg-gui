#!/usr/bin/env python3
"""List catalog msgids that no string literal in the package can produce.

This is a **diagnostic, not a gate** -- it always exits 0.  It exists because
``tools/check_locales.py`` deliberately checks only what can be checked safely:
that the ten catalogs agree with each other.  Agreement says nothing about
whether a msgid is *reachable*, and gettext can only ever look up a string
literal, so a msgid whose literal appears nowhere in the source is dead weight no
runtime path can ask for.

It found the 20 unreachable msgids recorded in ``WORKLOG.md``: the button-action
labels (``Left Click`` and friends), the four RGB zone names, two labels whose
wording moved on, and four stale device messages.  They are the reason a
button-label translation never fires -- ``device_core.action_label()`` builds
those strings at runtime, so no literal matches the catalog's spelling.

Read-only, stdlib only, no gettext needed::

    python tools/find_unreachable_msgids.py

Two traps it gets right, both of which produce false positives otherwise:

* Python source splits a long msgid across adjacent literals (``"a"\n"b"``);
  those are rejoined before searching.
* A ``.po`` escapes an embedded quote as ``\\"`` while the source spells it
  bare, so msgids are unescaped before searching -- ``Delete profile "%s"?``
  looks unreachable until you do.

A ``.pot`` drift check built on this is a recorded follow-up (see WORKLOG); it
was left out of the first pass on purpose, because an ``xgettext`` sweep would
find only a minority of these msgids (most are composed at runtime from device
and button names) and would mark the rest obsolete.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PACKAGE = os.path.join(ROOT, "rivalcfg_gui")
CATALOG = os.path.join(PACKAGE, "locales", "en", "LC_MESSAGES", "rivalcfg_gui.po")

#: One msgid is a run of lines after its `msgid` keyword, each `"..."`, up to the
#: blank line that separates entries.  `#~` blocks are obsolete and skipped.
_ENTRY = re.compile(r"\n\s*\n")
_KEYWORD = re.compile(r"^([A-Za-z_]+)\s+(.*)$")
_QUOTED = re.compile(r'"(.*)"')


def unescape(text):
    """Undo the escapes a `.po` applies to a string."""
    return (text.replace('\\"', '"').replace("\\n", "\n")
            .replace("\\t", "\t").replace("\\\\", "\\"))


def active_msgids(text):
    """Every non-obsolete msgid in a `.po`, unescaped, header excluded."""
    out = []
    for block in _ENTRY.split(text):
        if block.lstrip().startswith("#~"):
            continue
        value, keyword = None, None
        for line in block.splitlines():
            if line.startswith("#"):
                continue
            head = _KEYWORD.match(line)
            if head:
                keyword = head.group(1)
                # `msgid_plural` shares the keyword prefix check, so compare the
                # whole word; only the first `msgid` of an entry is the key.
                if keyword == "msgid" and value is None:
                    value = _QUOTED.search(head.group(2)).group(1)
            elif line.startswith('"') and keyword == "msgid" and value is not None:
                value += _QUOTED.search(line).group(1)
        if value:
            out.append(unescape(value))
    return out


def package_source():
    """All of the package's Python source, wrapped literals rejoined."""
    names = sorted(name for name in os.listdir(PACKAGE) if name.endswith(".py"))
    text = "\n".join(_read(os.path.join(PACKAGE, name)) for name in names)
    return re.sub(r'"\s*\n\s*"', "", text)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def unreachable():
    source = package_source()
    return [msgid for msgid in active_msgids(_read(CATALOG))
            if msgid not in source]


def main():
    found = unreachable()
    total = len([m for m in active_msgids(_read(CATALOG)) if m])
    for msgid in found:
        print(repr(msgid))
    print(f"\n{len(found)} of {total} msgids are unreachable "
          f"(no matching string literal in rivalcfg_gui/)")
    print("diagnostic only -- exit status is always 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
