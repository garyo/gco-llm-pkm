"""Tests for FileEditor's pages/-vs-toplevel fallback resolution."""

import logging

import pytest

from pkm_bridge.file_editor import FileEditor


@pytest.fixture
def editor(tmp_path):
    org = tmp_path / "org"
    logseq = tmp_path / "logseq"
    (org / "pages").mkdir(parents=True)
    (org / "journals").mkdir()
    (logseq / "Personal" / "pages").mkdir(parents=True)

    (org / "toplevel.org").write_text("at toplevel")
    (org / "pages" / "inpages.org").write_text("in pages")
    (org / "journals" / "2026-07-09.org").write_text("journal")
    (logseq / "Personal" / "pages" / "note.md").write_text("logseq page")

    return FileEditor(logging.getLogger("test"), str(org), str(logseq))


def test_exact_path_wins(editor):
    assert editor.read_file("org:toplevel.org")["path"] == "org:toplevel.org"
    assert editor.read_file("org:pages/inpages.org")["path"] == "org:pages/inpages.org"


def test_toplevel_request_falls_back_to_pages(editor):
    result = editor.read_file("org:inpages.org")
    assert result["path"] == "org:pages/inpages.org"
    assert result["content"] == "in pages"


def test_pages_request_falls_back_to_toplevel(editor):
    result = editor.read_file("org:pages/toplevel.org")
    assert result["path"] == "org:toplevel.org"
    assert result["content"] == "at toplevel"


def test_journal_relative_link_finds_page(editor):
    # A link like [[file:inpages.org]] inside a journal resolves to
    # org:journals/inpages.org; the basename fallback should find pages/.
    result = editor.read_file("org:journals/inpages.org")
    assert result["path"] == "org:pages/inpages.org"


def test_logseq_nested_pages_fallback(editor):
    result = editor.read_file("logseq:Personal/note.md")
    assert result["path"] == "logseq:Personal/pages/note.md"


def test_missing_file_still_raises(editor):
    with pytest.raises(ValueError, match="File not found"):
        editor.read_file("org:nope.org")


def test_write_updates_existing_variant_not_duplicate(editor, tmp_path):
    result = editor.write_file("org:inpages.org", "updated")
    assert result["path"] == "org:pages/inpages.org"
    assert (tmp_path / "org" / "pages" / "inpages.org").read_text() == "updated"
    assert not (tmp_path / "org" / "inpages.org").exists()


def test_write_new_file_created_at_requested_path(editor, tmp_path):
    result = editor.write_file("org:pages/brand-new.org", "new content")
    assert result["path"] == "org:pages/brand-new.org"
    assert (tmp_path / "org" / "pages" / "brand-new.org").read_text() == "new content"


def test_create_only_respects_existing_variant(editor):
    result = editor.write_file("org:inpages.org", "template", create_only=True)
    assert result["status"] == "exists"
    assert result["path"] == "org:pages/inpages.org"
