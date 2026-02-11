"""Meta-tools for the self-improvement agent.

These tools let the agent inspect and modify the PKM system's own artifacts:
skills, rules, feedback, conversations, tool logs, system prompt, and agent memory.

All tools subclass BaseTool and follow the same pattern as the main PKM tools.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import yaml

from ..tools.base import BaseTool
from ..tools.skills import (
    SKILL_NAME_RE,
    _build_md_frontmatter,
    _build_shell_frontmatter,
    _parse_skill_file,
)
from .filesystem import (
    MEMORY_CATEGORIES,
    get_skills_dir,
    read_memory_file,
    write_memory_file,
)

# ---------------------------------------------------------------------------
# Inspection tools (read-only)
# ---------------------------------------------------------------------------


class InspectSkillsTool(BaseTool):
    """List all skills with metadata, or read a specific skill's full content."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir

    @property
    def name(self) -> str:
        return "inspect_skills"

    @property
    def description(self) -> str:
        return (
            "List all saved skills with full metadata, descriptions, content previews, "
            "and usage stats. Optionally pass a skill_name to read its complete content."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Optional: specific skill name to read in full.",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        skills_dir = get_skills_dir(self.org_dir)
        skill_name = params.get("skill_name")

        if skill_name:
            # Read a specific skill
            for ext in (".sh", ".md"):
                filepath = skills_dir / f"{skill_name}{ext}"
                if filepath.exists():
                    parsed = _parse_skill_file(filepath)
                    if parsed:
                        return json.dumps(parsed, indent=2, default=str)
            return f"Skill '{skill_name}' not found."

        # List all skills
        skills = []
        for filepath in sorted(skills_dir.iterdir()):
            if filepath.suffix not in (".sh", ".md"):
                continue
            parsed = _parse_skill_file(filepath)
            if parsed:
                # Truncate body for listing
                parsed["_body"] = parsed.get("_body", "")[:200]
                skills.append(parsed)

        if not skills:
            return "No skills found."

        return json.dumps(skills, indent=2, default=str)


class InspectRulesTool(BaseTool):
    """List all active learned rules from the database."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "inspect_rules"

    @property
    def description(self) -> str:
        return (
            "List all active learned rules with type, text, confidence, hit count, "
            "age, and last reinforcement date. Optionally filter by rule_type."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rule_type": {
                    "type": "string",
                    "description": (
                        "Optional filter: retrieval, vocabulary, preference, "
                        "embedding_gap, general, tool_strategy, "
                        "prompt_amendment, approved_amendment."
                    ),
                },
                "include_inactive": {
                    "type": "boolean",
                    "description": "Include inactive/deactivated rules. Default false.",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import get_db
        from ..db_repository import LearnedRuleRepository

        db = get_db()
        try:
            include_inactive = params.get("include_inactive", False)
            if include_inactive:
                rules = LearnedRuleRepository.get_all(db)
            else:
                rules = LearnedRuleRepository.get_active(db)

            rule_type_filter = params.get("rule_type")
            if rule_type_filter:
                rules = [r for r in rules if r.rule_type == rule_type_filter]

            if not rules:
                return "No rules found."

            result = []
            now = datetime.utcnow()
            for r in rules:
                age_days = (now - r.created_at).days if r.created_at else 0
                if r.last_reinforced_at:
                    since_reinforced = (now - r.last_reinforced_at).days
                else:
                    since_reinforced = age_days
                result.append({
                    "id": r.id,
                    "type": r.rule_type,
                    "text": r.rule_text,
                    "data": r.rule_data,
                    "confidence": round(r.confidence, 2),
                    "hit_count": r.hit_count,
                    "is_active": r.is_active,
                    "age_days": age_days,
                    "days_since_reinforced": since_reinforced,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "last_reinforced_at": (
                        r.last_reinforced_at.isoformat()
                        if r.last_reinforced_at else None
                    ),
                })
            return json.dumps(result, indent=2)
        finally:
            db.close()


class InspectFeedbackTool(BaseTool):
    """Read recent QueryFeedback records."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "inspect_feedback"

    @property
    def description(self) -> str:
        return (
            "Read recent QueryFeedback records (last N days). Shows user message, "
            "signals (retrieval miss, correction, satisfaction, tool errors), and "
            "explicit thumbs up/down."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back. Default 7.",
                },
                "unprocessed_only": {
                    "type": "boolean",
                    "description": "Only show unprocessed feedback. Default false.",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import QueryFeedback, get_db

        days = params.get("days", 7)
        unprocessed_only = params.get("unprocessed_only", False)

        db = get_db()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = db.query(QueryFeedback).filter(QueryFeedback.created_at >= cutoff)
            if unprocessed_only:
                query = query.filter(QueryFeedback.processed == False)  # noqa: E712
            feedbacks = query.order_by(QueryFeedback.created_at.desc()).limit(100).all()

            if not feedbacks:
                return f"No feedback in the last {days} days."

            result = []
            for fb in feedbacks:
                signals = []
                if fb.retrieval_miss:
                    signals.append("RAG_MISS")
                if fb.user_followup_correction:
                    signals.append("USER_CORRECTION")
                if fb.tool_error_count > 0:
                    signals.append(f"ERRORS({fb.tool_error_count})")
                if fb.api_call_count > 3:
                    signals.append(f"LONG_CHAIN({fb.api_call_count})")

                result.append({
                    "query_id": fb.query_id,
                    "user_message": fb.user_message[:200],
                    "signals": signals,
                    "had_rag": fb.had_rag_context,
                    "total_tool_calls": fb.total_tool_calls,
                    "explicit_feedback": fb.explicit_feedback,
                    "feedback_note": fb.feedback_note,
                    "processed": fb.processed,
                    "created_at": fb.created_at.isoformat() if fb.created_at else None,
                })
            return json.dumps(result, indent=2)
        finally:
            db.close()


class InspectConversationsTool(BaseTool):
    """Read recent conversation summaries."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "inspect_conversations"

    @property
    def description(self) -> str:
        return (
            "Read recent conversation summaries (condensed). Shows what users asked, "
            "what tools were used, and whether interactions went well."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back. Default 24.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of sessions. Default 10.",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import ConversationSession, get_db
        from ..retrospective import _strip_conversation_blocks

        hours = params.get("hours", 24)
        limit = params.get("limit", 10)

        db = get_db()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            sessions = (
                db.query(ConversationSession)
                .filter(ConversationSession.updated_at >= cutoff)
                .order_by(ConversationSession.updated_at.desc())
                .limit(limit)
                .all()
            )

            if not sessions:
                return f"No conversations in the last {hours} hours."

            parts = []
            for session in sessions:
                if not session.history:
                    continue
                stripped = _strip_conversation_blocks(session.history)
                if not stripped:
                    continue

                ts = session.updated_at.strftime('%Y-%m-%d %H:%M')
                session_text = (
                    f"\n### Session {session.session_id[:8]}..."
                    f" ({ts})\n"
                )
                for msg in stripped[:20]:  # Limit messages per session
                    session_text += f"**{msg['role'].upper()}**: {msg['text'][:300]}\n"
                parts.append(session_text)

            return "\n".join(parts) if parts else "No conversations with content."
        finally:
            db.close()


class InspectToolLogsTool(BaseTool):
    """Read tool execution logs with helpfulness flags."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "inspect_tool_logs"

    @property
    def description(self) -> str:
        return (
            "Read tool execution logs with helpfulness flags. Shows which tool "
            "chains worked for which query types."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back. Default 24.",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import get_db
        from ..db_repository import ToolExecutionLogExtendedRepository

        hours = params.get("hours", 24)
        db = get_db()
        try:
            logs = ToolExecutionLogExtendedRepository.get_recent_summaries(db, hours=hours)
            if not logs:
                return f"No tool executions in the last {hours} hours."

            # Group by query_id
            grouped: Dict[str, list] = {}
            for log in logs:
                if log.query_id not in grouped:
                    grouped[log.query_id] = []
                grouped[log.query_id].append(log)

            lines = []
            for _, query_logs in list(grouped.items())[:50]:
                user_msg = query_logs[0].user_message[:100] if query_logs else "?"
                tool_chain = " -> ".join(
                    f"{log.tool_name}("
                    f"{'ok' if log.exit_code in (None, 0) else f'err:{log.exit_code}'})"
                    for log in query_logs
                )
                helpful = ""
                for log in query_logs:
                    if log.was_helpful is True:
                        helpful = " [HELPFUL]"
                        break
                    elif log.was_helpful is False:
                        helpful = " [UNHELPFUL]"
                        break

                lines.append(f'- "{user_msg}" => {tool_chain}{helpful}')

            return "\n".join(lines)
        finally:
            db.close()


class InspectSystemPromptTool(BaseTool):
    """Read the current system prompt."""

    def __init__(self, logger, system_prompt_path: str | Path):
        super().__init__(logger)
        self.system_prompt_path = Path(system_prompt_path)

    @property
    def name(self) -> str:
        return "inspect_system_prompt"

    @property
    def description(self) -> str:
        return (
            "Read the current system prompt template, "
            "including any injected rules and amendments."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        try:
            return self.system_prompt_path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            return f"Error reading system prompt: {e}"


class ReadMemoryTool(BaseTool):
    """Read the agent's own persistent memory files."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "Read all or a specific category from .pkm/memory/. "
            f"Categories: {', '.join(MEMORY_CATEGORIES)}. "
            "Returns the markdown file contents — your notes from previous runs."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Specific category to read. One of: "
                        f"{', '.join(MEMORY_CATEGORIES)}. "
                        "If omitted, reads all."
                    ),
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        category = params.get("category")
        if category:
            if category not in MEMORY_CATEGORIES:
                return f"Invalid category '{category}'. Valid: {', '.join(MEMORY_CATEGORIES)}"
            content = read_memory_file(category, self.org_dir)
            return content if content else f"No {category} memory file yet."

        # Read all
        parts = []
        for cat in MEMORY_CATEGORIES:
            content = read_memory_file(cat, self.org_dir)
            if content:
                parts.append(f"## {cat}\n\n{content}")
        return "\n\n---\n\n".join(parts) if parts else "No memory files yet."


# ---------------------------------------------------------------------------
# Action tools (write)
# ---------------------------------------------------------------------------


class WriteSkillTool(BaseTool):
    """Create or update a skill file in .pkm/skills/."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir
        self._run_log: list[str] = []

    @property
    def name(self) -> str:
        return "write_skill"

    @property
    def description(self) -> str:
        return (
            "Create or update a skill file in .pkm/skills/. Provide name, type, "
            "description, trigger, tags, and content."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "Kebab-case name (e.g., 'weekly-review'). "
                        "[a-z0-9-], 2-50 chars."
                    ),
                },
                "skill_type": {
                    "type": "string",
                    "enum": ["shell", "recipe"],
                    "description": "Type: 'shell' for .sh, 'recipe' for .md.",
                },
                "description": {"type": "string", "description": "What the skill does."},
                "content": {"type": "string", "description": "Skill content."},
                "trigger": {"type": "string", "description": "When to use this skill."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization.",
                },
            },
            "required": ["skill_name", "skill_type", "description", "content"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        import stat

        skill_name = params["skill_name"]
        skill_type = params["skill_type"]
        description = params["description"]
        content = params["content"]
        trigger = params.get("trigger", "")
        tags = params.get("tags", [])

        if not SKILL_NAME_RE.match(skill_name):
            return "Error: skill_name must be 2-50 chars, kebab-case [a-z0-9-]."

        skills_dir = get_skills_dir(self.org_dir)
        ext = ".sh" if skill_type == "shell" else ".md"
        filepath = skills_dir / f"{skill_name}{ext}"

        # Check existing for preserving metadata
        existing_metadata: dict = {}
        if filepath.exists():
            parsed = _parse_skill_file(filepath)
            if parsed:
                existing_metadata = parsed

        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        metadata = {
            "name": skill_name,
            "description": description,
            "trigger": trigger,
            "tags": tags,
            "created": existing_metadata.get("created", now),
            "last_used": existing_metadata.get("last_used", now),
            "use_count": existing_metadata.get("use_count", 0),
        }

        if skill_type == "shell":
            fm = _build_shell_frontmatter(metadata)
            file_content = fm + "\n#!/bin/bash\nset -euo pipefail\n\n" + content.strip() + "\n"
        else:
            fm = _build_md_frontmatter(metadata)
            file_content = fm + "\n\n" + content.strip() + "\n"

        filepath.write_text(file_content, encoding="utf-8")

        if skill_type == "shell":
            filepath.chmod(filepath.stat().st_mode | stat.S_IRUSR | stat.S_IXUSR)

        action = "Updated" if existing_metadata else "Created"
        self._run_log.append(f"{action} skill '{skill_name}' ({skill_type})")
        self.logger.info(f"SI Agent: {action.lower()} skill {skill_name}")
        return f"{action} skill '{skill_name}' ({skill_type})"


class DeleteSkillTool(BaseTool):
    """Remove a skill file by name."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir
        self._run_log: list[str] = []

    @property
    def name(self) -> str:
        return "delete_skill"

    @property
    def description(self) -> str:
        return "Remove a skill file by name. You must provide reasoning."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill to remove."},
                "reason": {"type": "string", "description": "Why this skill should be removed."},
            },
            "required": ["skill_name", "reason"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        skill_name = params["skill_name"]
        reason = params["reason"]
        skills_dir = get_skills_dir(self.org_dir)

        deleted = False
        for ext in (".sh", ".md"):
            filepath = skills_dir / f"{skill_name}{ext}"
            if filepath.exists():
                filepath.unlink()
                deleted = True
                break

        if not deleted:
            return f"Skill '{skill_name}' not found."

        msg = f"Deleted skill '{skill_name}': {reason}"
        self._run_log.append(msg)
        self.logger.info(f"SI Agent: {msg}")
        return msg


class ManageRulesTool(BaseTool):
    """Create, reinforce, deactivate, or delete learned rules."""

    def __init__(self, logger):
        super().__init__(logger)
        self._run_log: list[str] = []

    @property
    def name(self) -> str:
        return "manage_rules"

    @property
    def description(self) -> str:
        return (
            "Create, reinforce, deactivate, or delete learned rules in the database. "
            "Actions: 'create', 'reinforce', 'deactivate', 'delete'. "
            "Provide reasoning for every action."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "reinforce", "deactivate", "delete"],
                    "description": "What to do with the rule.",
                },
                "rule_id": {
                    "type": "integer",
                    "description": "Required for reinforce/deactivate/delete.",
                },
                "rule_type": {
                    "type": "string",
                    "description": (
                        "For create: retrieval, vocabulary, preference, "
                        "embedding_gap, general, tool_strategy."
                    ),
                },
                "rule_text": {
                    "type": "string",
                    "description": "For create: human-readable rule text.",
                },
                "rule_data": {
                    "type": "object",
                    "description": "For create: structured data (e.g., vocabulary mappings).",
                },
                "confidence": {
                    "type": "number",
                    "description": "For create: initial confidence (0.3-0.7 typical).",
                },
                "reason": {"type": "string", "description": "Why this action is being taken."},
            },
            "required": ["action", "reason"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import get_db
        from ..db_repository import LearnedRuleRepository

        action = params["action"]
        reason = params["reason"]
        db = get_db()
        try:
            if action == "create":
                rule_type = params.get("rule_type", "general")
                rule_text = params.get("rule_text", "")
                if not rule_text:
                    return "Error: rule_text is required for create."
                rule = LearnedRuleRepository.merge_or_create(
                    db,
                    rule_type=rule_type,
                    rule_text=rule_text,
                    rule_data=params.get("rule_data"),
                    confidence=params.get("confidence", 0.5),
                )
                msg = f"Created/reinforced rule #{rule.id} [{rule_type}]: {rule_text[:80]}"

            elif action == "reinforce":
                rule_id = params.get("rule_id")
                if not rule_id:
                    return "Error: rule_id required for reinforce."
                rule = LearnedRuleRepository.get_by_id(db, rule_id)
                if not rule:
                    return f"Rule #{rule_id} not found."
                rule.hit_count += 1
                rule.confidence = min(1.0, rule.confidence + 0.1)
                rule.last_reinforced_at = datetime.utcnow()
                rule.is_active = True
                db.commit()
                msg = f"Reinforced rule #{rule_id} (confidence now {rule.confidence:.2f})"

            elif action == "deactivate":
                rule_id = params.get("rule_id")
                if not rule_id:
                    return "Error: rule_id required for deactivate."
                rule = LearnedRuleRepository.update(db, rule_id, is_active=False)
                if not rule:
                    return f"Rule #{rule_id} not found."
                msg = f"Deactivated rule #{rule_id}"

            elif action == "delete":
                rule_id = params.get("rule_id")
                if not rule_id:
                    return "Error: rule_id required for delete."
                if not LearnedRuleRepository.delete(db, rule_id):
                    return f"Rule #{rule_id} not found."
                msg = f"Deleted rule #{rule_id}"

            else:
                return f"Unknown action: {action}"

            full_msg = f"{msg} — Reason: {reason}"
            self._run_log.append(full_msg)
            self.logger.info(f"SI Agent: {full_msg}")
            return msg
        finally:
            db.close()


class ProposeAmendmentTool(BaseTool):
    """Propose a system prompt change (requires human approval)."""

    def __init__(self, logger):
        super().__init__(logger)
        self._run_log: list[str] = []

    @property
    def name(self) -> str:
        return "propose_amendment"

    @property
    def description(self) -> str:
        return (
            "Propose a change to the base system prompt. Creates a prompt_amendment rule "
            "that requires human approval before taking effect. Use sparingly and only "
            "when you have strong evidence a prompt change would help."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "modify", "remove"],
                    "description": "Type of prompt change.",
                },
                "section": {
                    "type": "string",
                    "description": "Which section of the prompt to modify.",
                },
                "proposed_text": {"type": "string", "description": "The proposed text or change."},
                "reason": {"type": "string", "description": "Evidence-based justification."},
            },
            "required": ["action", "proposed_text", "reason"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import get_db
        from ..db_repository import LearnedRuleRepository

        db = get_db()
        try:
            rule = LearnedRuleRepository.create(
                db,
                rule_type="prompt_amendment",
                rule_text=params["proposed_text"],
                rule_data={
                    "action": params.get("action", "add"),
                    "section": params.get("section", ""),
                    "proposed_text": params["proposed_text"],
                    "reason": params["reason"],
                },
                confidence=0.5,
            )
            msg = (
                f"Proposed prompt amendment #{rule.id}: "
                f"{params['proposed_text'][:80]}... "
                "(requires human approval)"
            )
            self._run_log.append(msg)
            self.logger.info(f"SI Agent: {msg}")
            return msg
        finally:
            db.close()


class WriteMemoryTool(BaseTool):
    """Write to the agent's persistent memory files."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir
        self._run_log: list[str] = []

    @property
    def name(self) -> str:
        return "write_memory"

    @property
    def description(self) -> str:
        return (
            "Append to or replace a section in a .pkm/memory/ file. "
            f"Categories: {', '.join(MEMORY_CATEGORIES)}. "
            "These persist across runs and form your long-term memory."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(MEMORY_CATEGORIES),
                    "description": "Which memory file to write to.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write. Use markdown with dated sections.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["append", "replace"],
                    "description": "Whether to append to or replace the file. Default: append.",
                },
            },
            "required": ["category", "content"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        category = params["category"]
        content = params["content"]
        mode = params.get("mode", "append")

        write_memory_file(
            category, content, self.org_dir, append=(mode == "append")
        )

        msg = f"{'Appended to' if mode == 'append' else 'Replaced'} memory/{category}.md"
        self._run_log.append(msg)
        self.logger.info(f"SI Agent: {msg}")
        return msg


class WriteRulesSnapshotTool(BaseTool):
    """Write a human-readable YAML snapshot of all active rules."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir

    @property
    def name(self) -> str:
        return "write_rules_snapshot"

    @property
    def description(self) -> str:
        return (
            "Write .pkm/rules-snapshot.yaml with all active rules formatted for human reading. "
            "Call this at the end of each run."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..database import get_db
        from ..db_repository import LearnedRuleRepository
        from .filesystem import get_pkm_dir

        db = get_db()
        try:
            rules = LearnedRuleRepository.get_active(db)
            snapshot: list[dict] = []
            for r in rules:
                snapshot.append({
                    "id": r.id,
                    "type": r.rule_type,
                    "text": r.rule_text,
                    "confidence": round(r.confidence, 2),
                    "hits": r.hit_count,
                    "last_reinforced": (
                        r.last_reinforced_at.isoformat()
                        if r.last_reinforced_at else None
                    ),
                })

            pkm_dir = get_pkm_dir(self.org_dir)
            filepath = pkm_dir / "rules-snapshot.yaml"
            yaml_content = yaml.dump(
                {"generated_at": datetime.utcnow().isoformat(), "rules": snapshot},
                default_flow_style=False,
                sort_keys=False,
            )
            filepath.write_text(yaml_content, encoding="utf-8")
            return f"Wrote rules-snapshot.yaml with {len(snapshot)} rules."
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Tool registration helper
# ---------------------------------------------------------------------------


def create_inspection_tools(logger, org_dir: Path, system_prompt_path: Path) -> list[BaseTool]:
    """Create all inspection (read-only) meta-tools."""
    return [
        InspectSkillsTool(logger, org_dir),
        InspectRulesTool(logger),
        InspectFeedbackTool(logger),
        InspectConversationsTool(logger),
        InspectToolLogsTool(logger),
        InspectSystemPromptTool(logger, system_prompt_path),
        ReadMemoryTool(logger, org_dir),
    ]


def create_action_tools(logger, org_dir: Path) -> list[BaseTool]:
    """Create all action (write) meta-tools."""
    return [
        WriteSkillTool(logger, org_dir),
        DeleteSkillTool(logger, org_dir),
        ManageRulesTool(logger),
        ProposeAmendmentTool(logger),
        WriteMemoryTool(logger, org_dir),
        WriteRulesSnapshotTool(logger, org_dir),
    ]
