"""Tests for journal_append / edit_note and the journal section helpers."""

import logging
from typing import Callable

import pytest

from pkm_bridge.file_editor import FileEditor
from pkm_bridge.journal import append_to_section, journal_rel_path, journal_template
from pkm_bridge.tools.note_writing import EditNoteTool, JournalAppendTool

LOGGER = logging.getLogger("test")

JOURNAL = """---
title: "2026-09-27"
id: ABC
date: 2026-09-27
---
# Today
- [ ] pack

# Music
- rehearsed

## Heartbeat (08:00)
- gig at noon
"""


class RacingEditor(FileEditor):
    """Changes the file on disk just before the next write, like a Syncthing sync."""

    def __init__(self, *args, changes: list[Callable[[str], str]]):
        super().__init__(*args)
        self.changes = changes

    def write_file(self, filepath, content, **kwargs):
        if kwargs.get("base_hash") and self.changes:
            full = self._resolve_prefixed_path(filepath)
            full.write_text(self.changes.pop(0)(full.read_text()))
        return super().write_file(filepath, content, **kwargs)


@pytest.fixture
def org(tmp_path):
    org = tmp_path / "org"
    (org / "journals").mkdir(parents=True)
    (org / "pages").mkdir()
    return org


def journal_tool(org, changes=None) -> JournalAppendTool:
    tool = JournalAppendTool(LOGGER, org)
    if changes:
        tool.editor = RacingEditor(LOGGER, str(org), "", changes=changes)
    return tool


class TestJournalHelpers:
    def test_rel_path_requires_iso_date(self):
        assert journal_rel_path("2026-09-27") == "journals/2026-09-27.md"
        for bad in ("20260927", "2026-9-27", "2026-02-30", "../x"):
            with pytest.raises(ValueError):
                journal_rel_path(bad)

    def test_template_matches_editor(self):
        # Same text as journalTemplate in shared/editor/journal.ts
        assert journal_template("2026-09-21", "ABC-123") == (
            '---\ntitle: "2026-09-21"\nid: ABC-123\ndate: 2026-09-21\n---\n\n'
        )

    def test_append_at_end(self):
        assert append_to_section(JOURNAL, "- late note").endswith("- gig at noon\n- late note\n")

    def test_append_under_heading_goes_before_the_next_heading(self):
        out = append_to_section(JOURNAL, "- unpack", "Today")
        assert "- [ ] pack\n- unpack\n\n# Music" in out
        # Ahead of the subheading, so it doesn't read as part of Heartbeat.
        out = append_to_section(JOURNAL, "- practiced scales", "music")
        assert "- rehearsed\n- practiced scales\n\n## Heartbeat" in out

    def test_level_pins_the_match(self):
        text = "# Notes\n- a\n\n## Notes\n- b\n"
        assert append_to_section(text, "- c", "## Notes") == "# Notes\n- a\n\n## Notes\n- b\n- c\n"

    def test_missing_heading_is_created_at_end(self):
        out = append_to_section(JOURNAL, "- new flights", "## Heartbeat (14:00)")
        assert out == JOURNAL + "\n## Heartbeat (14:00)\n- new flights\n"
        out = append_to_section(journal_template("2026-09-27", "X"), "- idea", "Misc")
        assert out.endswith("---\n\n# Misc\n- idea\n")

    def test_ignores_frontmatter_fences_and_tags(self):
        text = "---\n# not a heading\n---\n```\n# Code\n```\n# Code #dev ^anchor\n- x\n"
        out = append_to_section(text, "- y", "code")
        assert out == text + "- y\n"

    def test_existing_text_is_untouched(self):
        out = append_to_section(JOURNAL, "- scales", "Music")
        assert out.replace("- scales\n", "", 1) == JOURNAL

    def test_crlf_preserved(self):
        assert append_to_section("# A\r\n- x\r\n", "- y", "A") == "# A\r\n- x\r\n- y\r\n"


class TestJournalAppend:
    def test_creates_journal(self, org):
        out = journal_tool(org).execute({"date": "2026-09-27", "text": "- idea", "heading": "Misc"})
        assert "created the journal" in out and "org:journals/2026-09-27.md" in out
        content = (org / "journals" / "2026-09-27.md").read_text()
        assert content.startswith('---\ntitle: "2026-09-27"\nid: ')
        assert content.endswith("---\n\n# Misc\n- idea\n")

    def test_appends_under_existing_and_new_heading(self, org):
        (org / "journals" / "2026-09-27.md").write_text(JOURNAL)
        tool = journal_tool(org)
        tool.execute({"date": "2026-09-27", "text": "- unpack", "heading": "Today"})
        out = tool.execute({"date": "2026-09-27", "text": "- rain", "heading": "Weather"})
        assert "created the journal" not in out
        content = (org / "journals" / "2026-09-27.md").read_text()
        assert "- [ ] pack\n- unpack\n\n# Music" in content
        assert content.endswith("- gig at noon\n\n# Weather\n- rain\n")

    def test_concurrent_edit_is_merged(self, org):
        (org / "journals" / "2026-09-27.md").write_text(JOURNAL)
        tool = journal_tool(org, changes=[lambda t: t.replace("- [ ] pack", "- [x] pack")])
        tool.execute({"date": "2026-09-27", "text": "- late", "heading": "Music"})
        content = (org / "journals" / "2026-09-27.md").read_text()
        assert "- [x] pack" in content and "- rehearsed\n- late\n" in content

    def test_overlapping_concurrent_append_is_redone(self, org):
        (org / "journals" / "2026-09-27.md").write_text(JOURNAL)
        tool = journal_tool(org, changes=[lambda t: t + "- synced in\n"])
        tool.execute({"date": "2026-09-27", "text": "- mine"})
        content = (org / "journals" / "2026-09-27.md").read_text()
        assert content.endswith("- gig at noon\n- synced in\n- mine\n")

    def test_bad_date(self, org):
        assert "YYYY-MM-DD" in journal_tool(org).execute({"date": "today", "text": "- x"})


class TestEditNote:
    @pytest.fixture
    def page(self, org):
        path = org / "pages" / "travel.md"
        path.write_text("# Travel\n- Paris in May\n- Rome\n- Rome again\n")
        return path

    def test_edits_applied(self, org, page):
        out = EditNoteTool(LOGGER, org).execute(
            {
                "path": "pages/travel.md",
                "edits": [
                    {"find": "Paris in May", "replace": "Paris in June"},
                    {"find": "- Rome again\n", "replace": ""},
                ],
            }
        )
        assert out.startswith("✅ Edited org:pages/travel.md (2 edit(s))")
        assert page.read_text() == "# Travel\n- Paris in June\n- Rome\n"

    def test_anchor_not_found_writes_nothing(self, org, page):
        before = page.read_text()
        out = EditNoteTool(LOGGER, org).execute(
            {
                "path": "org:pages/travel.md",
                "edits": [
                    {"find": "Paris in May", "replace": "Paris in June"},
                    {"find": "Berlin", "replace": "Bonn"},
                ],
            }
        )
        assert "edit 2: anchor text not found" in out
        assert page.read_text() == before

    def test_anchor_ambiguous(self, org, page):
        out = EditNoteTool(LOGGER, org).execute(
            {"path": "org:pages/travel.md", "edits": [{"find": "- Rome", "replace": "- Milan"}]}
        )
        assert "occurs 2 times" in out

    def test_concurrent_edit_is_merged(self, org, page):
        tool = EditNoteTool(LOGGER, org)
        tool.editor = RacingEditor(
            LOGGER, str(org), "", changes=[lambda t: t.replace("# Travel", "# Trips")]
        )
        out = tool.execute(
            {"path": "org:pages/travel.md", "edits": [{"find": "- Rome\n", "replace": "- Oslo\n"}]}
        )
        assert "merged" in out
        assert page.read_text() == "# Trips\n- Paris in May\n- Oslo\n- Rome again\n"

    def test_create(self, org):
        out = EditNoteTool(LOGGER, org).execute(
            {"path": "org:pages/boats.md", "content": "# Boats\n"}
        )
        assert out.startswith("✅ Created org:pages/boats.md")
        assert (org / "pages" / "boats.md").read_text() == "# Boats\n"

    def test_create_only_refuses_existing(self, org, page):
        out = EditNoteTool(LOGGER, org).execute(
            {"path": "org:pages/travel.md", "content": "overwrite!"}
        )
        assert "already exists" in out
        assert "Paris" in page.read_text()

    def test_create_requires_markdown(self, org):
        out = EditNoteTool(LOGGER, org).execute({"path": "org:pages/x.org", "content": "* x"})
        assert "must end in .md" in out

    def test_needs_exactly_one_of_edits_or_content(self, org, page):
        tool = EditNoteTool(LOGGER, org)
        assert "either edits" in tool.execute({"path": "org:pages/travel.md"})
        both = {"path": "org:pages/travel.md", "content": "x", "edits": [{"find": "a"}]}
        assert "either edits" in tool.execute(both)

    def test_missing_file(self, org):
        out = EditNoteTool(LOGGER, org).execute(
            {"path": "org:pages/nope.md", "edits": [{"find": "a", "replace": "b"}]}
        )
        assert "not found" in out

    def test_path_escape_refused(self, org):
        out = EditNoteTool(LOGGER, org).execute({"path": "org:../evil.md", "content": "x"})
        assert out.startswith("❌")
        assert not (org.parent / "evil.md").exists()
