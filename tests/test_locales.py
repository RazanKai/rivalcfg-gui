"""Tests for tools/check_locales.py and the shipped catalogs.

Two jobs here.  The first is the gate: the catalogs as shipped must pass the
checker, which is what catches a `.po` edited without rebuilding its `.mo` or a
string added to `en.po` but not to the other nine.  Neither failure is visible
at runtime -- a missing msgid falls back to English and an old `.mo` still
translates the strings it knew -- so nothing else would notice.

The second is the checker's own logic, exercised on catalogs built here.  That
part matters as much as the first: a check that cannot fail is not a check, so
each kind of problem the checker claims to find is written into a throwaway
tree and looked for.  The synthetic catalogs are hand-built rather than
generated with gettext, so these run anywhere; only the `en`/`de` pairs the
checks compare are needed, never a real translation.
"""

import struct

import pytest

from tools import check_locales as cl


# ---------------------------------------------------------------------------
# Building catalogs
# ---------------------------------------------------------------------------

MO_MAGIC = 0x950412DE


def _escape(text):
    return (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\n", "\\n").replace("\t", "\\t"))


def _po_text(entries, lang, obsolete=()):
    """A catalog with `entries` (msgid -> msgstr) and optional `#~` leftovers.

    The header is written by hand because it is the one entry whose msgid is
    empty: an obsolete entry can have an empty msgid too, and the checker has
    to tell the two apart.
    """
    out = ['msgid ""',
           'msgstr ""',
           '"Content-Type: text/plain; charset=UTF-8\\n"',
           f'"Language: {lang}\\n"',
           ""]
    for msgid, msgstr in entries.items():
        out += [f"msgid \"{_escape(msgid)}\"", f"msgstr \"{_escape(msgstr)}\"", ""]
    # `#~ ` on every line, as gettext writes a retired entry -- the marker is
    # what makes it obsolete, and the checker reads the msgid through it rather
    # than dropping the line.
    for msgid, msgstr in obsolete:
        out += [f"#~ msgid \"{_escape(msgid)}\"",
                f"#~ msgstr \"{_escape(msgstr)}\"", ""]
    return "\n".join(out) + "\n"


def _write_mo(path, mapping):
    """Compile a msgid -> msgstr mapping into a .mo file.

    Hand-rolled so these tests do not need gettext installed: several of them
    turn on what the checker does with a `.mo` that disagrees with its `.po`,
    and that disagreement has to be constructible without msgfmt.  Only the
    first five header words are ever read back (CPython ignores the hash
    table), so the hash fields are left empty.
    """
    items = [("", "Content-Type: text/plain; charset=UTF-8\n")]
    items += sorted(mapping.items())
    count = len(items)
    orig_offset = 28                                # 7 little-endian uint32s
    trans_offset = orig_offset + count * 8
    data_offset = trans_offset + count * 8

    orig_table, trans_table, blob = b"", b"", b""
    for msgid, msgstr in items:
        key, value = msgid.encode("utf-8"), msgstr.encode("utf-8")
        orig_table += struct.pack("<II", len(key), data_offset + len(blob))
        blob += key + b"\0"
        trans_table += struct.pack("<II", len(value), data_offset + len(blob))
        blob += value + b"\0"

    header = struct.pack("<IIIIIII", MO_MAGIC, 0, count,
                         orig_offset, trans_offset, 0, data_offset)
    path.write_bytes(header + orig_table + trans_table + blob)


def _tree(tmp_path, catalogs):
    """Write a locale tree; return its path.

    `catalogs` maps a language to ``(po_entries, mo_entries)``, or to a
    3-tuple with a list of obsolete `(msgid, msgstr)` pairs appended.  When
    `mo_entries` is None the `.mo` is built from the `.po`'s translated subset,
    which is what a correct build produces; passing it is how a test makes the
    two disagree.
    """
    root = tmp_path / "locales"
    for lang, spec in catalogs.items():
        entries, mo = spec[0], spec[1]
        obsolete = spec[2] if len(spec) > 2 else ()
        directory = root / lang / "LC_MESSAGES"
        directory.mkdir(parents=True)
        (directory / f"{cl.DOMAIN}.po").write_text(
            _po_text(entries, lang, obsolete), encoding="utf-8")
        _write_mo(directory / f"{cl.DOMAIN}.mo",
                  mo if mo is not None else {k: v for k, v in entries.items() if v})
    return str(root)


def _problems(root):
    """Every problem the checker finds, without msgfmt's own parse.

    ``with_labels=False``: the catalogs built here are strings invented for a
    test, so the labels the app builds are all "missing" from them by
    construction.  The shipped tree's own tests call ``check`` directly and do
    keep that check on.
    """
    return cl.check(root, with_msgfmt=False, with_labels=False)


def _found(root):
    return sorted((p.lang, p.kind) for p in _problems(root))


# ---------------------------------------------------------------------------
# The shipped catalogs
# ---------------------------------------------------------------------------

def test_shipped_catalogs_are_in_order():
    """The gate: what ships must pass.  A failure here is the whole point."""
    problems = _problems(cl.DEFAULT_LOCALE_DIR)
    fatal = [p for p in problems if p.kind in cl.FATAL_KINDS]
    assert not fatal, "\n".join(f"{p.lang}: {p.kind}: {p.detail}" for p in fatal)


def test_shipped_catalogs_have_no_obsolete_entries():
    # A warning, so the gate above stays quiet about it -- but 13 dead
    # macro/evdev strings per catalog is exactly the drift this phase removed,
    # and they must not come back unnoticed.
    assert [p for p in _problems(cl.DEFAULT_LOCALE_DIR)
            if p.kind == "obsolete"] == []


def test_the_shipped_tree_has_a_reference_catalog():
    assert cl.REFERENCE in cl.languages(cl.DEFAULT_LOCALE_DIR)


def test_the_shipped_catalogs_carry_every_action_label():
    """The half of the contract a test of the app could not see.

    The labels are read out of ``device_core``, so renaming one in the code and
    not in ``en.po`` fails here rather than shipping a button that is English
    in every language.  This is the drift the 2026-10 fixing pass closed.
    """
    _, entries = cl.parse_po(cl.catalog_path(cl.DEFAULT_LOCALE_DIR, cl.REFERENCE))
    present = {entry.msgid for entry in entries if not entry.obsolete}
    assert sorted(cl.action_labels() - present) == []


# ---------------------------------------------------------------------------
# What the checker reports
# ---------------------------------------------------------------------------

def test_agreeing_catalogs_report_nothing(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello"}, None),
        "de": ({"Hello": "Hallo"}, None),
    })
    assert _problems(root) == []


def test_msgid_only_the_reference_has_is_missing(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello", "Advanced": "Advanced"}, None),
        "de": ({"Hello": "Hallo"}, None),
    })
    assert _found(root) == [("de", "missing-msgid")]
    assert "'Advanced'" in _problems(root)[0].detail


def test_msgid_a_catalog_invented_is_extra(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello"}, None),
        "de": ({"Hello": "Hallo", "Gone": "Weg"}, None),
    })
    assert _found(root) == [("de", "extra-msgid")]


def test_mo_built_from_an_older_po_is_stale(tmp_path):
    """The failure the shipped en.mo was in: 151 translations of 181 strings."""
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello", "Advanced": "Advanced"}, None),
        "de": ({"Hello": "Hallo", "Advanced": "Erweitert"},
               {"Hello": "Hallo"}),
    })
    assert _found(root) == [("de", "stale-mo")]
    assert "'Advanced'" in _problems(root)[0].detail


def test_mo_carrying_a_msgid_the_po_dropped_is_an_orphan(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello"}, None),
        "de": ({"Hello": "Hallo"},
               {"Hello": "Hallo", "Gone": "Weg"}),
    })
    assert _found(root) == [("de", "orphan-mo")]


def test_untranslated_entry_is_not_a_problem(tmp_path):
    # Falling back to English is the designed behaviour for a string nobody has
    # translated yet, so the check must gate on presence, not completeness.
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello", "Advanced": "Advanced"}, None),
        "de": ({"Hello": "Hallo", "Advanced": ""}, None),
    })
    assert _problems(root) == []


def test_dropped_placeholders_are_reported(tmp_path):
    """A translation that loses a placeholder fails later, at the call site."""
    root = _tree(tmp_path, {
        "en": ({"{n} Hz": "{n} Hz", "Default (%s)": "Default (%s)"}, None),
        "de": ({"{n} Hz": "Hz", "Default (%s)": "Standard"}, None),
    })
    details = {p.detail for p in _problems(root)}
    assert _found(root) == [("de", "format"), ("de", "format")]
    assert any("braces ['n'] -> []" in d for d in details)
    assert any("conversions ['%s'] -> []" in d for d in details)


def test_reordered_placeholders_are_reported(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"%s of %d": "%s of %d"}, None),
        "de": ({"%s of %d": "%d von %s"}, None),
    })
    assert _found(root) == [("de", "format")]


def test_a_lone_percent_in_a_translation_is_left_alone(tmp_path):
    # No conversion in the source means the string is never formatted, so a
    # stray `%` is a translator's own text and not a lost placeholder.
    root = _tree(tmp_path, {
        "en": ({"Charging": "Charging"}, None),
        "de": ({"Charging": "Laden (100 %voll)"}, None),
    })
    assert _problems(root) == []


def test_a_repeated_msgid_is_a_syntax_error(tmp_path):
    root = tmp_path / "locales"
    directory = root / "en" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    (directory / f"{cl.DOMAIN}.po").write_text(
        _po_text({"Hello": "Hello"}, "en")
        + _po_text({"Hello": "Hello again"}, "en").split("\n\n", 1)[1],
        encoding="utf-8")
    _write_mo(directory / f"{cl.DOMAIN}.mo", {"Hello": "Hello"})
    assert _found(str(root)) == [("en", "syntax")]


def test_obsolete_entries_warn_without_failing(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello"}, None, [("Removed feature", "Removed feature")]),
        "de": ({"Hello": "Hallo"}, None),
    })
    problems = _problems(root)
    assert [(p.lang, p.kind) for p in problems] == [("en", "obsolete")]
    assert problems[0].kind not in cl.FATAL_KINDS


def test_an_action_label_the_catalogs_lack_is_reported(tmp_path):
    """Prove the kind fires: a check that cannot fail is not a check.

    A catalog carrying every label is in order; the same catalog minus one is
    not, and the label that was dropped is the one named.
    """
    labels = sorted(cl.action_labels())
    assert labels, "device_core offers no labels -- the check would be vacuous"

    def catalog(names):
        return {name: name for name in names}

    root = _tree(tmp_path / "complete", {
        "en": (catalog(labels), None),
        "de": (catalog(labels), None),
    })
    assert cl.check(root, with_msgfmt=False, with_labels=True) == []

    root = _tree(tmp_path / "minus-one", {
        "en": (catalog(labels[:-1]), None),
        "de": (catalog(labels[:-1]), None),
    })
    problems = cl.check(root, with_msgfmt=False, with_labels=True)
    assert [(p.lang, p.kind) for p in problems] == [("en", "missing-label")]
    assert problems[0].kind in cl.FATAL_KINDS
    assert labels[-1] in problems[0].detail


def test_the_label_check_is_off_for_a_throwaway_tree(tmp_path):
    # Otherwise every test above would report the app's labels as missing from
    # catalogs that were never meant to carry them.
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello"}, None),
        "de": ({"Hello": "Hallo"}, None),
    })
    assert _problems(root) == []


def test_a_tree_without_the_reference_catalog_is_an_error(tmp_path):
    root = _tree(tmp_path, {"de": ({"Hello": "Hallo"}, None)})
    with pytest.raises(cl.CatalogError):
        _problems(root)


def test_a_missing_locale_directory_is_an_error(tmp_path):
    with pytest.raises(cl.CatalogError):
        _problems(str(tmp_path / "nowhere"))


# ---------------------------------------------------------------------------
# Exit status, since CI reads it and not the report
# ---------------------------------------------------------------------------

def test_main_exits_zero_on_the_shipped_catalogs(capsys):
    assert cl.main(["--quiet"]) == 0
    assert "agree" in capsys.readouterr().out


def test_main_exits_nonzero_on_a_broken_catalog(tmp_path):
    root = _tree(tmp_path, {
        "en": ({"Hello": "Hello", "Advanced": "Advanced"}, None),
        "de": ({"Hello": "Hallo"}, None),
    })
    assert cl.main(["--quiet", "--locale-dir", root]) == 1


def test_main_reports_an_unreadable_tree_as_a_usage_error(tmp_path):
    # 2, not 1: "the check could not run" must not look like "the catalogs are
    # fine" to a `set -e` shell, nor like a real finding to a reviewer.
    assert cl.main(["--locale-dir", str(tmp_path / "nowhere")]) == 2
