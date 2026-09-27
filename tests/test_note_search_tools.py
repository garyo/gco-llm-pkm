"""Tests for the note search/listing tools, path helpers and tool registry."""

import logging
import os
from pathlib import Path

import pytest

from pkm_bridge.note_paths import display_path, recency, resolve_note_path
from pkm_bridge.tools.search_notes import SearchNotesTool

logger = logging.getLogger("test")


def _write(path: Path, text: str, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def notes(tmp_path: Path) -> tuple[Path, Path]:
    """An org dir and a Logseq dir holding 'haircut' in several places."""
    org = tmp_path / "org"
    logseq = tmp_path / "logseq"
    _write(org / "journals" / "2026-01-05.md", "- haircut with Mario\n")
    _write(org / "journals" / "2026-09-20.md", "- booked a haircut\n")
    _write(org / "journals" / "2025-12-31.md", "- nothing here\n")
    _write(org / "pages" / "haircuts.md", "# Haircuts\nEvery 3 weeks: haircut\n", 1_000)
    _write(org / "sciatica-history.md", "haircut chair hurt my back\n", 2_000)
    _write(org / ".pkm" / "memory.md", "haircut in a hidden dir\n")
    _write(org / "assets" / "photo.png", "haircut\n")
    _write(logseq / "Personal" / "journals" / "2023_05_12.md", "- haircut\n")
    _write(logseq / "Personal" / "journals" / "2024_02_01.md", "- haircut again\n")
    _write(logseq / "DSS" / "journals" / "2023_09_26.md", "- haircut at work?\n")
    _write(logseq / "Personal" / "pages" / "Mario.md", "- barber, haircut\n")
    return org, logseq


# --- note_paths ---------------------------------------------------------------


def test_display_path_prefixes(notes):
    org, logseq = notes
    assert display_path(org / "pages" / "haircuts.md", org, logseq) == "org:pages/haircuts.md"
    assert display_path(logseq / "DSS" / "x.md", org, logseq) == "logseq:DSS/x.md"
    assert display_path("gmail://abc", org, logseq) == "gmail://abc"
    assert display_path("/elsewhere/x.md", org, logseq) == "/elsewhere/x.md"


def test_resolve_note_path_forms(notes):
    org, logseq = notes
    assert resolve_note_path("org:journals", org, logseq) == (org / "journals").resolve()
    assert resolve_note_path("journals", org, logseq) == (org / "journals").resolve()
    # Relative paths that only exist in Logseq resolve there
    assert resolve_note_path("Personal/pages", org, logseq) == (logseq / "Personal/pages").resolve()
    assert resolve_note_path(str(org / "pages"), org, logseq) == (org / "pages").resolve()


@pytest.mark.parametrize("bad", ["/etc", "../outside", "org:../../etc", "logseq:../x"])
def test_resolve_note_path_confines(notes, bad):
    org, logseq = notes
    with pytest.raises(ValueError):
        resolve_note_path(bad, org, logseq)


def test_recency_prefers_filename_date(tmp_path):
    old_name = tmp_path / "2026-09-01.md"
    _write(old_name, "", mtime=0)
    newer_mtime = tmp_path / "page.md"
    _write(newer_mtime, "", mtime=1_000)
    assert recency(old_name) > recency(newer_mtime)


# --- search_notes -------------------------------------------------------------


def _file_headers(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(("org:", "logseq:"))]


def test_search_notes_covers_all_dirs_newest_first(notes):
    org, logseq = notes
    out = SearchNotesTool(logger, org, logseq).execute({"pattern": "haircut", "context": 0})
    assert _file_headers(out) == [
        "org:journals/2026-09-20.md",
        "org:journals/2026-01-05.md",
        "org:sciatica-history.md",  # newer mtime than pages/haircuts.md
        "org:pages/haircuts.md",
        "logseq:Personal/journals/2024_02_01.md",
        "logseq:DSS/journals/2023_09_26.md",
        "logseq:Personal/journals/2023_05_12.md",
        "logseq:Personal/pages/Mario.md",
    ]
    assert str(org) not in out and str(logseq) not in out
    assert "hidden dir" not in out and "photo.png" not in out
    assert "2:Every 3 weeks: haircut" in out


def test_search_notes_files_only_counts(notes):
    org, logseq = notes
    out = SearchNotesTool(logger, org, logseq).execute({"pattern": "haircut", "files_only": True})
    lines = out.splitlines()
    assert lines[0] == "org:journals/2026-09-20.md: 1"
    assert "org:pages/haircuts.md: 2" in lines
    assert len(lines) == 8


def test_search_notes_truncates_at_file_boundary(notes):
    org, logseq = notes
    out = SearchNotesTool(logger, org, logseq).execute({"pattern": "haircut", "limit": 100})
    assert out.startswith("⚠️ Showing ")
    assert "of 8 matching files" in out
    assert "files_only=true" in out
    assert "org:journals/2026-09-20.md" in out
    assert "logseq:" not in out


def test_search_notes_reports_bad_regex_once(notes):
    org, logseq = notes
    out = SearchNotesTool(logger, org, logseq).execute({"pattern": "("})
    assert out.count("rg error") == 1
    assert "regex parse error" in out


def test_search_notes_pattern_starting_with_dash(notes):
    org, logseq = notes
    out = SearchNotesTool(logger, org, logseq).execute({"pattern": "- booked"})
    assert _file_headers(out) == ["org:journals/2026-09-20.md"]
