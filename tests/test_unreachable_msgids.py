"""Tests for tools/find_unreachable_msgids.py.

The script is a diagnostic, not a gate, so these tests do not assert a count.
They assert the two things it exists to get right -- source-wrapped literals
rejoined, and `.po` escapes decoded -- because getting either wrong makes it
*over*-report, which is the failure mode that would let someone "clean up" a
msgid that is in fact live.
"""

import sys
from pathlib import Path

import pytest

from tools import find_unreachable_msgids as fu

REPO = Path(fu.ROOT)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A throwaway package dir + catalog, with the module's paths redirected."""
    package = tmp_path / "rivalcfg_gui"
    (package / "locales" / "en" / "LC_MESSAGES").mkdir(parents=True)
    catalog = package / "locales" / "en" / "LC_MESSAGES" / "rivalcfg_gui.po"
    monkeypatch.setattr(fu, "PACKAGE", str(package))
    monkeypatch.setattr(fu, "CATALOG", str(catalog))
    return package, catalog


def _write(fake, source, msgids):
    package, catalog = fake
    (package / "app.py").write_text(source, encoding="utf-8")
    body = ['msgid ""', 'msgstr "Content-Type: text/plain; charset=UTF-8\\n"', ""]
    body += ['msgid "%s"' % m for m in msgids]
    catalog.write_text("\n\n".join(body), encoding="utf-8")


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------

def test_the_shipped_list_is_absent_from_the_source():
    """Whatever it reports must genuinely not be a literal in the package."""
    source = fu.package_source()
    reported = fu.unreachable()
    assert reported, "the shipped tree is expected to have some (see WORKLOG)"
    assert all(msgid not in source for msgid in reported)


def test_a_live_msgid_is_not_reported():
    # A string the app really passes to _() must never appear in the report.
    assert "Delete profile" in fu.package_source()
    assert not any("Delete profile" in msgid for msgid in fu.unreachable())


# ---------------------------------------------------------------------------
# The two traps
# ---------------------------------------------------------------------------

def test_a_wrapped_literal_is_not_reported(fake):
    """Python splits long literals across lines; that is still one literal."""
    _write(fake, 'x = _("A very long message that the source "\n'
                 '      "wraps across two lines.")\n',
           ["A very long message that the source wraps across two lines."])
    assert fu.unreachable() == []


def test_a_po_escaped_quote_is_not_reported(fake):
    """`.po` spells a quote \\" where the source spells it bare."""
    _write(fake, 'x = _(\'Delete profile "%s"?\')\n',
           ['Delete profile \\"%s\\"?'])
    assert fu.unreachable() == []


def test_a_real_miss_is_reported(fake):
    _write(fake, 'x = _("live string")\n', ["live string", "never spelled here"])
    assert fu.unreachable() == ["never spelled here"]


# ---------------------------------------------------------------------------
# Parsing details
# ---------------------------------------------------------------------------

def test_the_header_and_obsolete_entries_are_skipped(fake):
    package, catalog = fake
    (package / "app.py").write_text('x = _("live")\n', encoding="utf-8")
    catalog.write_text(
        'msgid ""\n'
        'msgstr "Content-Type: text/plain; charset=UTF-8\\n"\n'
        '\n'
        'msgid "live"\n'
        'msgstr "live"\n'
        '\n'
        '#~ msgid "gone"\n'
        '#~ msgstr "gone"\n',
        encoding="utf-8")
    assert fu.unreachable() == []


def test_main_exits_zero_even_with_findings(fake, capsys):
    _write(fake, 'x = _("live")\n', ["live", "dead"])
    assert fu.main() == 0
    out = capsys.readouterr().out
    assert "dead" in out and "always 0" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
