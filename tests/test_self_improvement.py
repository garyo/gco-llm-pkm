"""Tests for the self-improvement capabilities (Phases 1-6).

Tests skills tools, feedback capture, retrospective enhancements,
and frontmatter parsing — all without requiring a database connection.
"""

import logging
import stat
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Phase 1: Skills / Recipes — frontmatter helpers + tools
# ---------------------------------------------------------------------------

from pkm_bridge.tools.skills import (
    SKILL_NAME_RE,
    ListSkillsTool,
    NoteToSelfTool,
    SaveSkillTool,
    UseSkillTool,
    _build_md_frontmatter,
    _build_shell_frontmatter,
    _parse_md_frontmatter,
    _parse_shell_frontmatter,
    _parse_skill_file,
)


@pytest.fixture
def logger():
    return logging.getLogger("test")


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".pkm" / "skills"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def org_dir(tmp_path: Path) -> Path:
    """Return a temp dir that acts as ORG_DIR (skills_dir created lazily)."""
    return tmp_path


# -- Skill name validation ---------------------------------------------------

@pytest.mark.parametrize(
    "name,valid",
    [
        ("weekly-review", True),
        ("search-music-notes", True),
        ("ab", True),
        ("a1", True),
        ("a", False),        # too short (< 2 chars)
        ("A-Bad", False),    # uppercase
        ("-leading", False),
        ("trailing-", False),
        ("has space", False),
        ("x" * 51, False),   # over 50 chars
        ("good-name-123", True),
    ],
)
def test_skill_name_regex(name: str, valid: bool):
    assert bool(SKILL_NAME_RE.match(name)) == valid


# -- Frontmatter round-trip ---------------------------------------------------

class TestShellFrontmatter:
    def test_round_trip(self):
        meta = {"name": "demo", "description": "A demo skill", "tags": ["test"]}
        fm = _build_shell_frontmatter(meta)

        parsed, body = _parse_shell_frontmatter(fm + "\n#!/bin/bash\necho hi\n")
        assert parsed["name"] == "demo"
        assert parsed["description"] == "A demo skill"
        assert parsed["tags"] == ["test"]
        assert "echo hi" in body

    def test_empty_content(self):
        meta, body = _parse_shell_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_no_frontmatter(self):
        meta, body = _parse_shell_frontmatter("#!/bin/bash\necho hi\n")
        assert meta == {}
        assert "echo hi" in body


class TestMdFrontmatter:
    def test_round_trip(self):
        meta = {"name": "recipe", "description": "A recipe", "use_count": 3}
        fm = _build_md_frontmatter(meta)

        parsed, body = _parse_md_frontmatter(fm + "\n\n## Steps\n1. Do stuff\n")
        assert parsed["name"] == "recipe"
        assert parsed["use_count"] == 3
        assert "Do stuff" in body

    def test_empty_content(self):
        meta, body = _parse_md_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_no_frontmatter(self):
        meta, body = _parse_md_frontmatter("# Just a heading\nNo fm here")
        assert meta == {}
        assert "Just a heading" in body


# -- _parse_skill_file --------------------------------------------------------

class TestParseSkillFile:
    def test_shell_file(self, skills_dir: Path):
        fm = _build_shell_frontmatter({"name": "my-shell", "description": "test"})
        p = skills_dir / "my-shell.sh"
        p.write_text(fm + "\n#!/bin/bash\ndate\n")

        parsed = _parse_skill_file(p)
        assert parsed is not None
        assert parsed["name"] == "my-shell"
        assert parsed["_type"] == "shell"
        assert parsed["_file"] == "my-shell.sh"
        assert "date" in parsed["_body"]

    def test_md_file(self, skills_dir: Path):
        fm = _build_md_frontmatter({"name": "my-recipe", "description": "test"})
        p = skills_dir / "my-recipe.md"
        p.write_text(fm + "\n\n## Steps\n1. foo\n")

        parsed = _parse_skill_file(p)
        assert parsed is not None
        assert parsed["_type"] == "recipe"
        assert "foo" in parsed["_body"]

    def test_unsupported_extension(self, tmp_path: Path):
        p = tmp_path / "readme.txt"
        p.write_text("hello")
        assert _parse_skill_file(p) is None

    def test_missing_file(self, tmp_path: Path):
        assert _parse_skill_file(tmp_path / "nope.sh") is None


# -- SaveSkillTool ------------------------------------------------------------

class TestSaveSkillTool:
    def test_save_shell_skill(self, logger, org_dir: Path):
        tool = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        result = tool.execute({
            "skill_name": "hello-world",
            "skill_type": "shell",
            "description": "Prints hello",
            "content": "echo hello",
        })
        assert "Created" in result
        fp = org_dir / ".pkm/skills" / "hello-world.sh"
        assert fp.exists()
        content = fp.read_text()
        assert "echo hello" in content
        assert "#!/bin/bash" in content
        assert "set -euo pipefail" in content
        # Should be executable
        assert fp.stat().st_mode & stat.S_IXUSR

    def test_save_recipe_skill(self, logger, org_dir: Path):
        tool = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        result = tool.execute({
            "skill_name": "weekly-review",
            "skill_type": "recipe",
            "description": "Weekly review procedure",
            "content": "## Steps\n1. Check calendar\n2. Review notes",
            "trigger": "user asks for weekly review",
            "tags": ["review", "weekly"],
        })
        assert "Created" in result
        fp = org_dir / ".pkm/skills" / "weekly-review.md"
        assert fp.exists()
        content = fp.read_text()
        assert "Check calendar" in content
        # Verify frontmatter
        meta, _ = _parse_md_frontmatter(content)
        assert meta["trigger"] == "user asks for weekly review"
        assert meta["tags"] == ["review", "weekly"]
        assert meta["use_count"] == 0

    def test_update_existing_skill(self, logger, org_dir: Path):
        tool = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        # Create first
        tool.execute({
            "skill_name": "updatable",
            "skill_type": "recipe",
            "description": "v1",
            "content": "old content",
        })
        fp = org_dir / ".pkm/skills" / "updatable.md"
        meta1, _ = _parse_md_frontmatter(fp.read_text())
        created_time = meta1["created"]

        # Update
        result = tool.execute({
            "skill_name": "updatable",
            "skill_type": "recipe",
            "description": "v2",
            "content": "new content",
        })
        assert "Updated" in result
        content = fp.read_text()
        assert "new content" in content
        meta2, _ = _parse_md_frontmatter(content)
        assert meta2["created"] == created_time  # preserved
        assert meta2["description"] == "v2"

    def test_bad_name_rejected(self, logger, org_dir: Path):
        tool = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        result = tool.execute({
            "skill_name": "BAD NAME!",
            "skill_type": "recipe",
            "description": "bad",
            "content": "nope",
        })
        assert "Error" in result

    def test_dangerous_shell_blocked(self, logger, org_dir: Path):
        tool = SaveSkillTool(logger, org_dir, dangerous_patterns=[r"rm\s+-rf"])
        result = tool.execute({
            "skill_name": "danger-zone",
            "skill_type": "shell",
            "description": "dangerous",
            "content": "rm -rf /",
        })
        assert "Error" in result
        assert "blocked" in result.lower() or "safety" in result.lower()


# -- ListSkillsTool -----------------------------------------------------------

class TestListSkillsTool:
    def _create_skill(self, org_dir: Path, name: str, stype: str = "recipe",
                      tags: list | None = None, desc: str = "test"):
        """Helper: write a skill file directly."""
        skills_dir = org_dir / ".pkm" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": desc, "tags": tags or [],
                "use_count": 0, "last_used": "never"}
        if stype == "shell":
            fm = _build_shell_frontmatter(meta)
            (skills_dir / f"{name}.sh").write_text(fm + "\n#!/bin/bash\ntrue\n")
        else:
            fm = _build_md_frontmatter(meta)
            (skills_dir / f"{name}.md").write_text(fm + "\n\ncontent\n")

    def test_empty(self, logger, org_dir: Path):
        tool = ListSkillsTool(logger, org_dir)
        result = tool.execute({})
        assert "No skills found" in result

    def test_lists_all(self, logger, org_dir: Path):
        self._create_skill(org_dir, "skill-a", desc="Alpha skill")
        self._create_skill(org_dir, "skill-b", stype="shell", desc="Beta skill")
        tool = ListSkillsTool(logger, org_dir)
        result = tool.execute({})
        assert "2 skill(s)" in result
        assert "skill-a" in result
        assert "skill-b" in result

    def test_filter_by_tag(self, logger, org_dir: Path):
        self._create_skill(org_dir, "tagged", tags=["music"])
        self._create_skill(org_dir, "untagged")
        tool = ListSkillsTool(logger, org_dir)
        result = tool.execute({"tag": "music"})
        assert "tagged" in result
        assert "untagged" not in result

    def test_filter_by_search(self, logger, org_dir: Path):
        self._create_skill(org_dir, "find-notes", desc="Search through notes")
        self._create_skill(org_dir, "do-other", desc="Something else")
        tool = ListSkillsTool(logger, org_dir)
        result = tool.execute({"search": "notes"})
        assert "find-notes" in result
        assert "do-other" not in result


# -- UseSkillTool -------------------------------------------------------------

class TestUseSkillTool:
    def test_load_and_bump(self, logger, org_dir: Path):
        # Create a skill first
        save = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        save.execute({
            "skill_name": "loadable",
            "skill_type": "recipe",
            "description": "Loadable skill",
            "content": "Step 1: do the thing",
        })

        use = UseSkillTool(logger, org_dir)
        result = use.execute({"skill_name": "loadable"})
        assert "loadable" in result
        assert "Step 1" in result

        # use_count should now be 1
        fp = org_dir / ".pkm/skills" / "loadable.md"
        meta, _ = _parse_md_frontmatter(fp.read_text())
        assert meta["use_count"] == 1

        # Use again
        use.execute({"skill_name": "loadable"})
        meta2, _ = _parse_md_frontmatter(fp.read_text())
        assert meta2["use_count"] == 2

    def test_not_found(self, logger, org_dir: Path):
        use = UseSkillTool(logger, org_dir)
        result = use.execute({"skill_name": "nonexistent"})
        assert "not found" in result.lower()

    def test_load_shell_skill(self, logger, org_dir: Path):
        save = SaveSkillTool(logger, org_dir, dangerous_patterns=[])
        save.execute({
            "skill_name": "my-script",
            "skill_type": "shell",
            "description": "A shell skill",
            "content": "echo 'running'",
        })
        use = UseSkillTool(logger, org_dir)
        result = use.execute({"skill_name": "my-script"})
        assert "shell" in result.lower()
        assert "echo" in result


# ---------------------------------------------------------------------------
# Phase 3: Satisfaction detection
# ---------------------------------------------------------------------------

from pkm_bridge.feedback_capture import detect_satisfaction


class TestSatisfactionDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("thanks, that's exactly what I needed", True),
            ("perfect, thank you!", True),
            ("great, that works", True),
            ("thanks!", True),  # short affirmation
            ("What is the weather?", False),
            ("That's not right at all", False),
            ("Can you try again?", False),
            ("", False),
        ],
    )
    def test_detect_satisfaction(self, text: str, expected: bool):
        assert detect_satisfaction(text) == expected

    def test_short_affirmation_patterns(self):
        for msg in ["thanks", "thanks!", "Thank you", "got it", "perfect", "great"]:
            assert detect_satisfaction(msg), f"Expected satisfaction for: {msg!r}"

    def test_longer_negative_not_satisfied(self):
        # "thanks" embedded but negative overall context shouldn't trigger short affirmation
        assert not detect_satisfaction("No thanks, I don't want that")


# ---------------------------------------------------------------------------
# Phase 4: Retrospective — tool summary stripping
# ---------------------------------------------------------------------------

from pkm_bridge.retrospective import (
    SessionRetrospective,
    _strip_conversation_blocks,
)


class TestRetrospectiveToolStripping:
    """Test that _strip_conversation_blocks now includes condensed tool info."""

    def test_tool_use_preserved_as_summary(self):
        messages = [
            {"role": "user", "content": "Find my notes about sailing"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me search for that."},
                    {
                        "type": "tool_use",
                        "name": "search_notes",
                        "input": {"pattern": "sailing", "context": 2},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": "Found 3 matches in notes/sailing.org ...",
                    }
                ],
            },
        ]

        stripped = _strip_conversation_blocks(messages)
        texts = [m["text"] for m in stripped]
        joined = " ".join(texts)

        assert "TOOL: search_notes" in joined
        assert "RESULT:" in joined
        assert "sailing" in joined

    def test_plain_text_messages_preserved(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        stripped = _strip_conversation_blocks(messages)
        assert len(stripped) == 2
        assert stripped[0]["text"] == "Hello"
        assert stripped[1]["text"] == "Hi there!"


# ---------------------------------------------------------------------------
# Phase 5: NoteToSelfTool (mocked DB)
# ---------------------------------------------------------------------------

class TestNoteToSelfTool:
    def test_saves_note_success(self, logger):
        tool = NoteToSelfTool(logger)
        mock_db = MagicMock()
        mock_repo = MagicMock()

        with patch.dict("sys.modules", {}), \
             patch("pkm_bridge.database.get_db", return_value=mock_db), \
             patch("pkm_bridge.db_repository.SessionNoteRepository", mock_repo):
            # The tool does deferred imports inside execute(), so we need to
            # patch at the module level that the relative import resolves to.
            result = tool.execute(
                {"note": "User prefers bullet points", "category": "user_preference"},
                context={"session_id": "sess-123"},
            )
            assert "Noted" in result
            assert "user_preference" in result
            mock_db.close.assert_called_once()

    def test_handles_db_failure_gracefully(self, logger):
        tool = NoteToSelfTool(logger)

        with patch("pkm_bridge.database.get_db", side_effect=Exception("connection refused")):
            result = tool.execute(
                {"note": "test"},
                context={"session_id": "s1"},
            )
            # Should not raise, returns acknowledgment
            assert "Note acknowledged" in result or "noted" in result.lower()


# ---------------------------------------------------------------------------
# Phase 3: Abandonment detection
# ---------------------------------------------------------------------------

class TestAbandonmentDetection:
    def test_detects_abandoned_session(self):
        """Sessions with last user message >30 min old should be marked abandoned."""
        retro = SessionRetrospective.__new__(SessionRetrospective)
        retro.logger = logging.getLogger("test-retro")

        mock_db = MagicMock()

        # Create mock session with stale user message as last
        old_time = datetime.utcnow() - timedelta(hours=2)
        mock_session = MagicMock()
        mock_session.session_id = "abandoned-sess"
        mock_session.updated_at = old_time
        mock_session.conversation_history = [
            {"role": "user", "content": "Hello?"},
            {"role": "assistant", "content": "Hi there."},
            {"role": "user", "content": "Can you help with X?"},  # last msg is user
        ]

        with patch(
            "pkm_bridge.retrospective.QueryFeedbackExplicitRepository"
        ):
            mock_db.query.return_value.filter.return_value.all.return_value = [mock_session]
            retro._detect_abandoned_sessions(mock_db)
            # Verify no exception raised — detailed assertions need real DB

    def test_no_crash_on_empty(self):
        retro = SessionRetrospective.__new__(SessionRetrospective)
        retro.logger = logging.getLogger("test-retro")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        retro._detect_abandoned_sessions(mock_db)  # should not raise


# ---------------------------------------------------------------------------
# Config / settings.py — rule type formatting
# ---------------------------------------------------------------------------

from config.settings import Config


class TestSettingsRuleFormatting:
    def test_tool_strategy_rules_formatted(self):
        """tool_strategy rules should appear in formatted output."""
        mock_rule = MagicMock()
        mock_rule.rule_type = "tool_strategy"
        mock_rule.rule_text = "find_context better than search_notes for date queries"
        mock_rule.confidence = 0.8
        mock_rule.hit_count = 5

        config = Config.__new__(Config)
        result = config._format_learned_rules([mock_rule])
        assert "Tool Strategy" in result
        assert "find_context" in result

    def test_prompt_amendment_excluded(self):
        """prompt_amendment rules should NOT be auto-injected."""
        mock_rule = MagicMock()
        mock_rule.rule_type = "prompt_amendment"
        mock_rule.rule_text = "Add a section about embeddings"
        mock_rule.confidence = 0.9
        mock_rule.hit_count = 1

        config = Config.__new__(Config)
        result = config._format_learned_rules([mock_rule])
        assert "Add a section about embeddings" not in result

    def test_approved_amendment_included(self):
        """approved_amendment rules SHOULD be injected."""
        mock_rule = MagicMock()
        mock_rule.rule_type = "approved_amendment"
        mock_rule.rule_text = "Always check embeddings first"
        mock_rule.confidence = 0.9
        mock_rule.hit_count = 2

        config = Config.__new__(Config)
        result = config._format_learned_rules([mock_rule])
        assert "Always check embeddings first" in result


# ---------------------------------------------------------------------------
# Database schema upgrade
# ---------------------------------------------------------------------------

class TestSchemaUpgrade:
    def test_upgrade_adds_was_helpful_column(self):
        """_upgrade_schema should add was_helpful if missing."""
        from pkm_bridge.database import _upgrade_schema

        mock_engine = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = ["tool_execution_logs"]
        mock_inspector.get_columns.return_value = [
            {"name": "id"}, {"name": "tool_name"}, {"name": "exit_code"}
        ]  # no was_helpful

        mock_conn = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        with patch("pkm_bridge.database.inspect", return_value=mock_inspector):
            _upgrade_schema(mock_engine)

        # Should have executed ALTER TABLE
        mock_conn.execute.assert_called_once()
        sql_arg = str(mock_conn.execute.call_args[0][0])
        assert "ALTER TABLE" in sql_arg
        assert "was_helpful" in sql_arg

    def test_upgrade_skips_if_column_exists(self):
        """_upgrade_schema should be a no-op if was_helpful already present."""
        from pkm_bridge.database import _upgrade_schema

        mock_engine = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = ["tool_execution_logs"]
        mock_inspector.get_columns.return_value = [
            {"name": "id"}, {"name": "tool_name"}, {"name": "was_helpful"}
        ]

        with patch("pkm_bridge.database.inspect", return_value=mock_inspector):
            _upgrade_schema(mock_engine)

        # Should NOT have called engine.begin (no ALTER needed)
        mock_engine.begin.assert_not_called()
