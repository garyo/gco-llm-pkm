"""Tests for the curation apply engine: anchor validation, apply, staleness."""

import logging

import pytest

from pkm_bridge.curation.apply import apply_proposal, validate_payload
from pkm_bridge.file_editor import FileEditor

LOGGER = logging.getLogger("test")


@pytest.fixture
def editor(tmp_path):
    org = tmp_path / "org"
    logseq = tmp_path / "logseq"
    (logseq / "pages").mkdir(parents=True)
    (logseq / "journals").mkdir()

    (logseq / "journals" / "2026_07_01.md").write_text(
        "- worked on the dovetail jig today\n- other stuff\n"
    )
    (logseq / "pages" / "Woodworking.md").write_text("- my woodworking notes\n")

    return FileEditor(LOGGER, str(org), str(logseq))


def link_edit(find="worked on the dovetail jig", replace="worked on the [[dovetail jig]]"):
    return {"file": "logseq:journals/2026_07_01.md", "find": find, "replace": replace}


class TestValidatePayload:
    def test_valid_add_links(self, editor):
        assert validate_payload("add_links", {"edits": [link_edit()]}, editor) == []

    def test_unknown_kind(self, editor):
        assert validate_payload("reorganize", {}, editor)

    def test_add_links_requires_edits(self, editor):
        assert validate_payload("add_links", {"edits": []}, editor)

    def test_anchor_missing(self, editor):
        problems = validate_payload("add_links", {"edits": [link_edit(find="not there")]}, editor)
        assert any("not found" in p for p in problems)

    def test_anchor_not_unique(self, editor):
        problems = validate_payload("add_links", {"edits": [link_edit(find="- ")]}, editor)
        assert any("must be unique" in p for p in problems)

    def test_identical_find_replace(self, editor):
        problems = validate_payload(
            "add_links", {"edits": [link_edit(replace="worked on the dovetail jig")]}, editor
        )
        assert any("identical" in p for p in problems)

    def test_missing_file(self, editor):
        edit = {"file": "logseq:journals/nope.md", "find": "x", "replace": "y"}
        assert validate_payload("add_links", {"edits": [edit]}, editor)

    def test_new_page_requires_content(self, editor):
        problems = validate_payload("new_page", {"page": {"file": "logseq:pages/X.md"}}, editor)
        assert any("page.content" in p for p in problems)

    def test_new_page_must_not_exist(self, editor):
        payload = {"page": {"file": "logseq:pages/Woodworking.md", "content": "dup"}}
        problems = validate_payload("new_page", payload, editor)
        assert any("already exists" in p for p in problems)

    def test_path_escape_rejected(self, editor):
        edit = {"file": "logseq:../outside.md", "find": "x", "replace": "y"}
        assert validate_payload("add_links", {"edits": [edit]}, editor)

    def test_insight_valid_without_changes(self, editor):
        assert validate_payload("insight", {"edits": []}, editor) == []

    def test_insight_must_not_carry_edits(self, editor):
        problems = validate_payload("insight", {"edits": [link_edit()]}, editor)
        assert any("no file changes" in p for p in problems)


class TestApplyProposal:
    def test_apply_add_links(self, editor, tmp_path):
        result = apply_proposal("add_links", {"edits": [link_edit()]}, editor, LOGGER)
        assert result["status"] == "applied"
        content = (tmp_path / "logseq" / "journals" / "2026_07_01.md").read_text()
        assert "[[dovetail jig]]" in content
        assert content.count("dovetail") == 1

    def test_apply_new_page_with_backlink(self, editor, tmp_path):
        payload = {
            "page": {
                "file": "logseq:pages/Dovetail Jig.md",
                "content": "- all about the dovetail jig\n",
            },
            "edits": [link_edit()],
        }
        result = apply_proposal("new_page", payload, editor, LOGGER)
        assert result["status"] == "applied"
        assert (tmp_path / "logseq" / "pages" / "Dovetail Jig.md").exists()
        journal = (tmp_path / "logseq" / "journals" / "2026_07_01.md").read_text()
        assert "[[dovetail jig]]" in journal

    def test_stale_anchor_writes_nothing(self, editor, tmp_path):
        journal = tmp_path / "logseq" / "journals" / "2026_07_01.md"
        journal.write_text("- completely rewritten since the proposal\n")
        result = apply_proposal("add_links", {"edits": [link_edit()]}, editor, LOGGER)
        assert result["status"] == "stale"
        assert "rewritten" in journal.read_text()

    def test_reapply_is_stale(self, editor):
        payload = {"edits": [link_edit()]}
        assert apply_proposal("add_links", payload, editor, LOGGER)["status"] == "applied"
        # Anchor was consumed by the first apply — a second apply must refuse.
        assert apply_proposal("add_links", payload, editor, LOGGER)["status"] == "stale"

    def test_insight_applies_writing_nothing(self, editor, tmp_path):
        result = apply_proposal("insight", {"edits": []}, editor, LOGGER)
        assert result["status"] == "applied"
        assert result["written"] == []
        journal = (tmp_path / "logseq" / "journals" / "2026_07_01.md").read_text()
        assert "dovetail jig today" in journal

    def test_multi_edit_applies_all(self, editor):
        payload = {
            "edits": [
                link_edit(),
                {
                    "file": "logseq:pages/Woodworking.md",
                    "find": "my woodworking notes",
                    "replace": "my [[Woodworking]] notes",
                },
            ]
        }
        result = apply_proposal("add_links", payload, editor, LOGGER)
        assert result["status"] == "applied"
        assert len(result["written"]) == 2
