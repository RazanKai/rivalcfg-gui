"""Tests for tools/check_versions.py.

The gate itself is two assertions on the real tree, plus the parsing in a
throwaway copy.  The parsing half matters most: each version is pulled out by a
regex anchored on its own file's syntax, and a regex that quietly stops matching
-- because a file was reformatted, or a field renamed -- would leave CI green
while checking nothing.  So every source is read from a copy with a known value
written into it, and the disagreement path is exercised rather than assumed.
"""

import shutil
import sys
from pathlib import Path

import pytest

from tools import check_versions as cv

REPO = Path(cv.ROOT)


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------

def test_the_shipped_versions_agree():
    found = cv.collect()
    assert len(found) == len(cv.SOURCES)
    reference = found[cv.REFERENCE]
    disagreements = {label: value for label, value in found.items()
                     if value != reference and label != cv.REFERENCE}
    assert not disagreements, disagreements


def test_main_exits_zero_on_the_shipped_tree(capsys):
    assert cv.main([]) == 0
    assert "agree" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The parsing, on a copy
# ---------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A copy of the four files, with cv.ROOT pointed at it."""
    root = tmp_path / "repo"
    for _label, relative, _pattern in cv.SOURCES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    monkeypatch.setattr(cv, "ROOT", str(root))
    return root


def _set(root, relative, old, new):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{relative} no longer contains {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_every_source_is_read(tree):
    found = cv.collect()
    assert set(found) == {label for label, _r, _p in cv.SOURCES}
    assert all(value for value in found.values())


def test_a_bumped_init_is_the_reference(tree):
    _set(tree, "rivalcfg_gui/__init__.py", "__version__ = \"1.6.0\"",
         "__version__ = \"9.9.9\"")
    assert cv.collect()[cv.REFERENCE] == "9.9.9"


def test_each_other_file_is_compared_against_it(tree):
    # A disagreement in any one of the three, not just the first found.
    for relative, old, new in (
        ("dist/aur/PKGBUILD", "pkgver=1.6.0", "pkgver=1.5.0"),
        ("dist/aur/.SRCINFO", "pkgver = 1.6.0", "pkgver = 1.5.0"),
        ("dist/debian/changelog", "rivalcfg-gui (1.6.0-1)",
         "rivalcfg-gui (1.5.0-1)"),
    ):
        _set(tree, relative, old, new)
        assert cv.main([]) == 1, relative
        _set(tree, relative, new, old)


def test_a_debian_revision_is_not_part_of_the_version(tree):
    # `1.6.0-2` is the same upstream version as `1.6.0`; bumping the packaging
    # revision must not read as a version bump.
    _set(tree, "dist/debian/changelog", "(1.6.0-1)", "(1.6.0-2)")
    assert cv.collect()["dist/debian/changelog"] == "1.6.0"
    assert cv.main([]) == 0


def test_a_reformatted_file_is_a_parse_error_not_agreement(tree):
    """The failure mode this whole script could otherwise hide behind.

    If a field is renamed or reindented so the regex stops matching, the check
    must say so -- not skip the file and report that the rest agree.
    """
    _set(tree, "dist/aur/PKGBUILD", "pkgver=1.6.0", "pkgver = 1.6.0")
    with pytest.raises(cv.CheckError, match="no version found"):
        cv.collect()


def test_an_unreadable_file_is_a_usage_error(tree, capsys):
    (tree / "dist/aur/.SRCINFO").unlink()
    assert cv.main([]) == 2
    assert "error" in capsys.readouterr().err


def test_the_srcinfo_hint_only_appears_for_srcinfo(tree, capsys):
    _set(tree, "dist/aur/PKGBUILD", "pkgver=1.6.0", "pkgver=1.5.0")
    assert cv.main([]) == 1
    assert "--printsrcinfo" not in capsys.readouterr().err

    _set(tree, "dist/aur/PKGBUILD", "pkgver=1.5.0", "pkgver=1.6.0")
    _set(tree, "dist/aur/.SRCINFO", "pkgver = 1.6.0", "pkgver = 1.5.0")
    assert cv.main([]) == 1
    assert "--printsrcinfo" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
