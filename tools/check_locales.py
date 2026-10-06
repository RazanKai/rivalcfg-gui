#!/usr/bin/env python3
"""Check the shipped translation catalogs for the ways they silently rot.

The catalogs are hand-maintained: ``rivalcfg_gui/locales/en/LC_MESSAGES/``
doubles as the reference catalog (there is no generated ``.pot`` -- see below),
and each other language is a copy of it with the strings translated.  Nothing in
the build regenerates them, so nothing notices when they fall behind the code.
Two failures that have to be caught by eye otherwise:

* **Parity.**  A string added to the app and to ``en.po`` but not to the other
  catalogs shows up untranslated (falls back to English) rather than not at all,
  so the app looks fine.  The nine RGB and lighting strings the buttons/RGB
  pages added went missing this way.
* **Freshness.**  A ``.mo`` is a *build product* of its ``.po``, and nothing
  rebuilds it.  An ``en.mo`` built from an older ``.po`` still translates the
  strings that existed then -- so the app runs, in a language the catalog no
  longer describes.  Comparing the two sets is the only way to see it.
* **Labels.**  The button labels are built in the code and looked up verbatim,
  so a label spelled one way in ``device_core`` and another in the catalogs is
  simply never translated -- in all ten languages at once, silently.

None of the three is visible at runtime, which is why this is a check and not a
test of the app.

There is deliberately no ``xgettext`` extraction here.  Most of ``en.po``'s
msgids are built at runtime -- device names and button labels come out of
``rivalcfg``, and the RGB strings are composed -- so a generated ``.pot`` would
find a minority of them and mark the rest obsolete, destroying the file it was
meant to check.  Adding a msgid stays a hand edit in ``en.po``.

Usage: ``python tools/check_locales.py [--locale-dir DIR]``.  Exit status is 0
when every catalog is in order, 1 when one is not.
"""

from __future__ import annotations

import argparse
import gettext
import os
import re
import shutil
import subprocess
import sys
from collections import namedtuple

DOMAIN = "rivalcfg_gui"
REFERENCE = "en"

#: Where the catalogs live relative to the repository root.  Kept in sync with
#: ``rivalcfg_gui/app.py``'s ``_get_locale_dir()`` by hand -- but only the name;
#: if they ever drift, this check fails loudly on a missing directory.
DEFAULT_LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    "rivalcfg_gui", "locales",
)

Entry = namedtuple("Entry", "msgid msgstr flags obsolete")
Problem = namedtuple("Problem", "lang kind detail")

#: Fatal: the app would misbehave or the catalog would be silently incomplete.
FATAL_KINDS = frozenset(
    {"missing-msgid", "extra-msgid", "stale-mo", "orphan-mo", "format", "syntax",
     "missing-label"}
)


class CatalogError(Exception):
    """A catalog this checker cannot read; reported, never swallowed."""


# --------------------------------------------------------------------------
# Reading .po files
#
# Babel and polib would both do this, and neither is a dependency of the app.
# Babel additionally rejects these headers outright: `read_po` parses
# PO-Revision-Date as a datetime and the catalogs carry a bare date.
# --------------------------------------------------------------------------

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
    "f": "\f", "v": "\v", '"': '"', "\\": "\\",
}


def _unescape(text):
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _quoted(text):
    text = text.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return _unescape(text[1:-1])
    return _unescape(text)


def _field(lines, keyword):
    """The value of ``keyword`` in one entry, joining its continuation lines.

    The continuation lines are collected only while ``keyword`` is the entry's
    current field: a plain "value is set" test would append the *header's*
    ``"Language: ..."`` lines to its empty msgid, inventing an entry whose
    msgid is the whole header.
    """
    value, current = None, None
    for line in lines:
        if line.startswith("#"):
            continue
        head = re.match(r"^([A-Za-z_]+(?:\[\d+\])?)\s+(.*)$", line)
        if head:
            current = head.group(1)
            if current == keyword:
                value = _quoted(head.group(2))
        elif line.startswith('"') and current == keyword and value is not None:
            value += _quoted(line)
    return value


def _header_fields(msgstr):
    fields = {}
    for line in msgstr.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key.strip()] = value.strip()
    return fields


def parse_po(path):
    """Return ``(header, entries)`` for one catalog.

    ``entries`` includes obsolete ones, flagged: they are reported but never
    treated as part of the catalog, since ``msgfmt`` does not put them in the
    ``.mo`` either.
    """
    blocks, block = [], []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.strip():
                block.append(line)
            elif block:
                blocks.append(block)
                block = []
    if block:
        blocks.append(block)

    header, entries = {}, []
    for block in blocks:
        obsolete = any(line.startswith("#~") for line in block)
        # `#~` marks a line as obsolete; strip the marker rather than the line,
        # or an obsolete entry has no msgid left to read and silently vanishes.
        live = [line[2:].lstrip() if line.startswith("#~") else line
                for line in block]
        # `msgid ""` first: an obsolete block's own msgid may be empty too, and
        # the two must not be confused.
        if any(re.match(r"^msgid_plural\b", line) for line in live):
            # Not a silent pass: a plural catalog read as if it were singular
            # would compare the wrong strings and report itself clean.
            raise CatalogError(f"{path}: plural entries -- this checker only "
                               f"reads singular catalogs")
        flags = set()
        for line in live:
            if line.startswith("#,"):
                flags.update(flag.strip() for flag in line[2:].split(",") if flag.strip())
        msgid = _field(live, "msgid")
        msgstr = _field(live, "msgstr")
        if msgid is None or msgstr is None:
            continue                      # a block of comments and nothing else
        if msgid == "" and not obsolete:
            header = _header_fields(msgstr)
            continue
        entries.append(Entry(msgid, msgstr, flags, obsolete))
    return header, entries


def read_mo(path):
    """The msgid -> msgstr mapping a compiled ``.mo`` actually carries.

    The ``""`` key is the header, which every ``.mo`` has and no ``.po`` entry
    corresponds to -- counting it would leave every catalog with a phantom
    orphan.
    """
    with open(path, "rb") as handle:
        catalog = gettext.GNUTranslations(handle)._catalog
    return {key: value for key, value in catalog.items() if key != ""}


# --------------------------------------------------------------------------
# Format strings
#
# A translation that drops a placeholder does not fail where it is wrong; it
# fails at the call site, as a KeyError or an IndexError, in whichever language
# the user happens to run.  Both styles are in use: five msgids are marked
# `python-brace-format`, and the rest that interpolate use `%`.
# --------------------------------------------------------------------------

def brace_fields(text):
    """The ``{}`` and ``{name}`` fields of a str.format template, in order."""
    return [re.split(r"[!:.\[\]]", m.group(1), maxsplit=1)[0]
            for m in re.finditer(r"\{([^{}]*)\}", text)]


_PERCENT = re.compile(r"%(?:%|[-+ #0]*\d*(?:\.\d+)?[hlL]?[diouxXeEfFgGcrs])")


def percent_conversions(text):
    """The ``%s``-style conversions in a template, ``%%`` excluded."""
    return [m.group(0) for m in _PERCENT.finditer(text) if m.group(0) != "%%"]


def format_problems(msgid, msgstr):
    """Placeholders the translation dropped, added or reordered.

    Both comparisons are driven by the *msgid*: a translation that dropped its
    only placeholder has nothing left to trigger a "does this look formatted"
    test on, which is exactly the case worth catching.
    """
    problems = []
    want, got = sorted(brace_fields(msgid)), sorted(brace_fields(msgstr))
    if want != got:
        problems.append(f"braces {want} -> {got}")
    if percent_conversions(msgid):
        # Only when the source has one: a translation may legitimately contain a
        # bare `%` ("100 %lik"), and there is nothing to preserve when the string
        # this one is called with is not formatted at all.
        want, got = percent_conversions(msgid), percent_conversions(msgstr)
        if want != got:
            problems.append(f"conversions {want} -> {got}")
    return problems


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def catalog_path(locale_dir, lang):
    return os.path.join(locale_dir, lang, "LC_MESSAGES", f"{DOMAIN}.po")


def languages(locale_dir):
    if not os.path.isdir(locale_dir):
        raise CatalogError(f"no such locale directory: {locale_dir}")
    return sorted(
        name for name in os.listdir(locale_dir)
        if os.path.isfile(catalog_path(locale_dir, name))
    )


def action_labels():
    """Every button-action label the window layer can display, read from the code.

    Taken from ``device_core`` rather than listed here: a copy of the list in
    this file would be one more thing to keep in step, which is the drift being
    checked for in the first place.  ``device_core`` is standard-library-only,
    so this works on a bare runner.
    """
    root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from rivalcfg_gui import device_core

    labels = set(device_core.ACTION_LABELS.values())
    labels.update(label for _value, label in device_core.MULTIMEDIA_ACTIONS)
    return labels


def check(locale_dir=DEFAULT_LOCALE_DIR, with_msgfmt=True, with_labels=True):
    """Return every problem found.  An empty list means the catalogs are in order.

    Only the kinds in :data:`FATAL_KINDS` should fail a build; the rest are
    reported so they do not go unnoticed.

    ``with_labels`` compares the labels the button UI can build, as read from
    ``device_core``, against the reference catalog.  A caller handing in a
    throwaway tree -- the checker's own tests -- passes ``False``: two strings
    invented on the spot are not the app's vocabulary, and would report every
    real label as missing.
    """
    problems = []
    langs = languages(locale_dir)
    if REFERENCE not in langs:
        raise CatalogError(f"{locale_dir} has no {REFERENCE} reference catalog")

    parsed, obsolete = {}, {}
    for lang in langs:
        path = catalog_path(locale_dir, lang)
        try:
            _, entries = parse_po(path)
        except CatalogError as exc:
            problems.append(Problem(lang, "syntax", str(exc)))
            parsed[lang] = {}
            continue
        seen, obsoletes = {}, 0
        for entry in entries:
            if entry.obsolete:
                obsoletes += 1
                continue
            if entry.msgid in seen:
                problems.append(Problem(lang, "syntax",
                                        f"msgid appears twice: {entry.msgid!r}"))
            seen[entry.msgid] = entry
        parsed[lang] = seen
        obsolete[lang] = obsoletes

    for lang, count in sorted(obsolete.items()):
        if count:
            # A warning, not a failure: obsolete entries are how a reworded
            # string keeps its old translation around, and msgfmt already
            # leaves them out of the .mo.  They just must not accumulate.
            problems.append(Problem(lang, "obsolete",
                                    f"{count} obsolete entr(ies) left in the .po "
                                    f"(drop with msgattrib --no-obsolete)"))

    reference = parsed.get(REFERENCE, {})
    for lang in langs:
        entries = parsed[lang]
        missing = sorted(set(reference) - set(entries))
        extra = sorted(set(entries) - set(reference))
        if missing:
            problems.append(Problem(lang, "missing-msgid",
                                    f"{len(missing)} vs {REFERENCE}: "
                                    + ", ".join(repr(m) for m in missing[:4])))
        if extra:
            problems.append(Problem(lang, "extra-msgid",
                                    f"{len(extra)} not in {REFERENCE}: "
                                    + ", ".join(repr(m) for m in extra[:4])))

        # `msgfmt` drops entries with an empty msgstr, so what the .mo must
        # carry is exactly the translated subset -- neither more nor less.
        expected = {msgid for msgid, entry in entries.items() if entry.msgstr}
        mo_path = os.path.join(locale_dir, lang, "LC_MESSAGES", f"{DOMAIN}.mo")
        if not os.path.isfile(mo_path):
            problems.append(Problem(lang, "stale-mo", f"missing {DOMAIN}.mo"))
        else:
            actual = set(read_mo(mo_path))
            stale = sorted(expected - actual)
            orphan = sorted(actual - expected)
            if stale:
                problems.append(Problem(lang, "stale-mo",
                                        f"{len(stale)} msgid(s) in the .po but not "
                                        f"the .mo, e.g. " + ", ".join(repr(s) for s in stale[:3])))
            if orphan:
                problems.append(Problem(lang, "orphan-mo",
                                        f"{len(orphan)} msgid(s) in the .mo but not "
                                        f"the .po, e.g. " + ", ".join(repr(s) for s in orphan[:3])))

        for msgid, entry in sorted(entries.items()):
            if not entry.msgstr:
                continue
            for detail in format_problems(msgid, entry.msgstr):
                problems.append(Problem(lang, "format", f"{msgid!r}: {detail}"))

    if with_labels:
        # A label with no msgid is not a broken app: gettext hands the string
        # back untranslated, so the button reads "Left Click" in every language
        # and nothing else notices.  This is the check that was silent while
        # the code built "Left click" and all ten catalogs carried "Left Click".
        absent = sorted(action_labels() - set(reference))
        if absent:
            problems.append(Problem(REFERENCE, "missing-label",
                                    f"{len(absent)} action label(s) the UI can build "
                                    f"have no msgid: "
                                    + ", ".join(repr(a) for a in absent[:4])))

    if with_msgfmt:
        problems.extend(_msgfmt_problems(locale_dir, langs))
    return problems


def _msgfmt_problems(locale_dir, langs):
    """``msgfmt -c`` over every catalog, when gettext is installed."""
    if shutil.which("msgfmt") is None:
        print("note: msgfmt not found, skipping its syntax check "
              "(install gettext for the full check)", file=sys.stderr)
        return []
    problems = []
    for lang in langs:
        path = catalog_path(locale_dir, lang)
        result = subprocess.run(
            ["msgfmt", "--check-format", "-o", os.devnull, path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            first = (result.stderr.strip().splitlines() or ["failed"])[0]
            problems.append(Problem(lang, "syntax", first))
    return problems


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--locale-dir", default=DEFAULT_LOCALE_DIR,
                        help="catalogs to check (default: the shipped ones)")
    parser.add_argument("--quiet", action="store_true",
                        help="only print problems")
    args = parser.parse_args(argv)

    try:
        langs = languages(args.locale_dir)
        problems = check(args.locale_dir)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    by_lang = {}
    for problem in problems:
        by_lang.setdefault(problem.lang, []).append(problem)

    for lang in [REFERENCE] + [l for l in langs if l != REFERENCE]:
        _, entries = parse_po(catalog_path(args.locale_dir, lang))
        obsolete = sum(1 for e in entries if e.obsolete)
        found = by_lang.get(lang, [])
        fatal = [p for p in found if p.kind in FATAL_KINDS]
        mark = "FAIL" if fatal else ("warn" if found else "ok")
        if not args.quiet or fatal:
            print(f"{lang:6} {mark:4}  {len(entries) - obsolete:3} msgids"
                  + (f", {obsolete} obsolete" if obsolete else ""))
        for problem in found:
            if problem.kind in FATAL_KINDS:
                print(f"         ! {problem.kind}: {problem.detail}")
            elif not args.quiet:
                print(f"         - {problem.kind}: {problem.detail}")

    fatal = [p for p in problems if p.kind in FATAL_KINDS]
    if fatal:
        print(f"\n{len(fatal)} problem(s) to fix in {len({p.lang for p in fatal})} catalog(s)")
        return 1
    warnings = len(problems) - len(fatal)
    print(f"\nall {len(langs)} catalogs agree with {REFERENCE}"
          + (f" ({warnings} warning(s))" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
