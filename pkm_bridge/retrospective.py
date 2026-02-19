"""Daily retrospective analysis pipeline.

Gathers unprocessed QueryFeedback records, reviews recent session conversations,
and uses Opus to identify patterns and generate LearnedRule records.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db, ConversationSession, LearnedRule, QueryFeedback
from .db_repository import (
    QueryFeedbackRepository, QueryFeedbackExplicitRepository,
    LearnedRuleRepository, ToolExecutionLogExtendedRepository,
)

RETROSPECTIVE_MODEL = "claude-opus-4-6"

RETROSPECTIVE_PROMPT = """\
You are a system analyst for a Personal Knowledge Management (PKM) assistant.
Your job is to review recent query feedback signals and actual conversation transcripts
to identify patterns that can improve future retrieval and response quality.

## Current Learned Rules
{existing_rules}

## Recent Query Feedback Signals
{feedback_summary}

## Recent Session Conversations (last 24h)
{conversations}

## Tool Execution Summaries (last 24h)
{tool_summaries}

## Existing Skills Catalog
{skills_catalog}

## Recent Journal Topics (last 7 days of notes)
{journal_context}

## Your Task

Analyze the feedback signals AND the actual conversations above. Look for:

1. **Retrieval rules**: When the user asks about topic X, what additional context should be retrieved?
   - Look for cases where RAG missed relevant context and Claude had to search manually.
   - Look for topic associations the system should know about.

2. **Vocabulary mappings**: What terms does the user use that map to different terms in their notes?
   - The user might say "PKM" but notes use "org-mode" or "logseq".
   - Informal terms vs formal terms in notes.

3. **Preference rules**: What patterns show user preferences?
   - Ordering preferences (newest first, alphabetical, etc.)
   - Format preferences (brief vs detailed, bullet points vs prose)
   - Topic preferences for certain types of queries

4. **Embedding gaps**: Are there topics with poor retrieval coverage?
   - Topics that consistently require manual search after RAG.
   - Areas where the embedding model might not capture semantic similarity well.

5. **General insights**: Any other observations about how the system could work better.
   - Approach suggestions, capability observations, prompt improvements.
   - Patterns in how the user interacts with the system.

6. **Satisfaction assessment**: Based on the actual conversations, gauge overall user satisfaction.
   - Note specific interactions that went poorly or particularly well.

7. **Tool strategy rules**: Which tools/sequences work well for which query types?
   - Note patterns like "find_context works better than semantic_search for date-range queries."
   - Identify tool sequences that are consistently effective or ineffective.

8. **Skill candidates**: Look for recurring multi-tool patterns across sessions.
   If Claude used the same tool sequence in 3+ queries, propose it as a saveable skill.

10. **Skill audit**: Review the Existing Skills Catalog above for redundancy or consolidation
    opportunities. If two or more skills do essentially the same thing, recommend which to
    keep and which to remove. Include these in the "skill_consolidations" field of your response.

9. **Prompt amendments**: If you identify a change that should be made to the base system instructions
   (not just a rule), propose it with rule_type "prompt_amendment" and rule_data containing
   {{"action": "add|modify|remove", "section": "...", "proposed_text": "..."}}.
   These require human approval before taking effect.

## Rules for generating rules

- Only generate rules you have evidence for from the data above.
- If an existing rule is still valid, include it with the EXACT same rule_text to reinforce it.
- If an existing rule is contradicted by new evidence, generate a replacement with new text.
- Keep rule_text concise (1-2 sentences).
- For vocabulary rules, include structured data in rule_data with "user_term" and "note_terms" keys.
- For tool_strategy rules, include rule_data with "query_pattern" and "recommended_tools" keys.
- Confidence should reflect how strong the evidence is (0.3-0.7 for new rules).

Respond with ONLY a JSON object (no markdown fencing):
{{
  "rules": [
    {{
      "rule_type": "retrieval|vocabulary|preference|embedding_gap|general|tool_strategy|prompt_amendment",
      "rule_text": "human-readable description of the rule",
      "rule_data": {{}},
      "confidence": 0.5
    }}
  ],
  "proposed_skills": [
    {{
      "skill_name": "kebab-case-name",
      "skill_type": "shell|recipe",
      "description": "what this skill does",
      "trigger": "when to use it",
      "content": "script or procedure content"
    }}
  ],
  "skill_consolidations": [
    {{
      "keep": "skill-name-to-keep",
      "remove": ["skill-name-to-remove", "..."],
      "reason": "why these are redundant"
    }}
  ],
  "satisfaction_notes": "Brief assessment of overall user satisfaction",
  "summary": "Brief summary of what was analyzed and key findings"
}}
"""


def _strip_conversation_blocks(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Process conversation history, keeping text and condensed tool summaries.

    Instead of stripping tool blocks entirely, includes condensed summaries
    so the retrospective can see what tools were used and their results.
    """
    stripped = []
    for msg in history:
        role = msg.get('role', '')
        content = msg.get('content', '')

        if role == 'user':
            if isinstance(content, str):
                stripped.append({"role": "user", "text": content})
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get('type') == 'tool_result':
                            # Include condensed tool result
                            result_content = item.get('content', '')
                            if isinstance(result_content, str):
                                result_preview = result_content[:200]
                            else:
                                result_preview = str(result_content)[:200]
                            stripped.append({"role": "system", "text": f"[RESULT: {result_preview}]"})
                    elif isinstance(item, str):
                        stripped.append({"role": "user", "text": item})
        elif role == 'assistant':
            if isinstance(content, str):
                stripped.append({"role": "assistant", "text": content})
            elif isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get('type') == 'text':
                            text_parts.append(item.get('text', ''))
                        elif item.get('type') == 'tool_use':
                            # Include condensed tool call
                            tool_name = item.get('name', '?')
                            tool_input = item.get('input', {})
                            # Summarize params
                            params_summary = str(tool_input)[:150]
                            text_parts.append(f"[TOOL: {tool_name}({params_summary})]")
                    elif isinstance(item, str):
                        text_parts.append(item)
                if text_parts:
                    stripped.append({"role": "assistant", "text": ' '.join(text_parts)})

    return stripped


def _format_feedback_summary(feedbacks) -> str:
    """Format feedback records into a readable summary."""
    if not feedbacks:
        return "No new feedback signals."

    lines = []
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
        if len(fb.search_tools_used) > 1:
            signals.append(f"MULTI_SEARCH({len(fb.search_tools_used)})")

        signal_str = ", ".join(signals) if signals else "OK"
        lines.append(
            f"- Query: \"{fb.user_message[:120]}\" | "
            f"RAG: {'yes' if fb.had_rag_context else 'no'} ({fb.rag_context_chars} chars) | "
            f"Tools: {fb.total_tool_calls} | Signals: {signal_str}"
        )

    return "\n".join(lines)


def _format_existing_rules(rules) -> str:
    """Format existing rules for context in the prompt."""
    if not rules:
        return "No existing rules."

    lines = []
    for rule in rules:
        lines.append(
            f"- [{rule.rule_type}] (conf={rule.confidence:.2f}, hits={rule.hit_count}) {rule.rule_text}"
        )
    return "\n".join(lines)


class SessionRetrospective:
    """Runs daily retrospective analysis on query feedback and sessions."""

    def __init__(self, anthropic_client, logger):
        self.client = anthropic_client
        self.logger = logger
        self.last_run_result: Optional[Dict[str, Any]] = None

    def run(self) -> Dict[str, Any]:
        """Execute the retrospective pipeline.

        Returns a summary dict with stats about what was processed.
        """
        self.logger.info("Starting retrospective analysis...")
        result = {
            "started_at": datetime.utcnow().isoformat(),
            "feedback_processed": 0,
            "rules_created": 0,
            "rules_reinforced": 0,
            "rules_decayed": 0,
            "rules_deactivated": 0,
            "skills_proposed": 0,
            "skills_consolidated": 0,
            "abandoned_marked": 0,
            "error": None,
        }

        db = get_db()
        try:
            # 0. Session abandonment detection (Phase 3)
            # Mark sessions where last message is from user and stale (>30 min)
            result["abandoned_marked"] = self._detect_abandoned_sessions(db)

            # 1. Gather unprocessed feedback
            feedbacks = QueryFeedbackRepository.get_unprocessed(db)
            if not feedbacks:
                self.logger.info("Retrospective: no unprocessed feedback, skipping.")
                result["summary"] = "No unprocessed feedback."
                self.last_run_result = result
                return result

            result["feedback_processed"] = len(feedbacks)
            self.logger.info(f"Retrospective: processing {len(feedbacks)} feedback records")

            # 2. Load recent conversations (last 24h)
            conversations_text = self._load_recent_conversations(db)

            # 3. Load recent journal context
            journal_context = self._load_journal_context()

            # 4. Load existing active rules
            existing_rules = LearnedRuleRepository.get_active(db)

            # 5. Load tool execution summaries (Phase 4)
            tool_summaries = self._load_tool_execution_summaries(db)

            # 5b. Load existing skills catalog for audit
            skills_catalog = self._load_skills_catalog()

            # 6. Build and send the prompt to Opus
            prompt = RETROSPECTIVE_PROMPT.format(
                existing_rules=_format_existing_rules(existing_rules),
                feedback_summary=_format_feedback_summary(feedbacks),
                conversations=conversations_text,
                tool_summaries=tool_summaries,
                skills_catalog=skills_catalog,
                journal_context=journal_context,
            )

            response = self.client.messages.create(
                model=RETROSPECTIVE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = ""
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    response_text += block.text

            # 7. Parse JSON response
            parsed = self._parse_response(response_text)
            if not parsed:
                result["error"] = "Failed to parse Opus response"
                self.last_run_result = result
                return result

            # 8. Merge rules
            source_query_ids = [fb.query_id for fb in feedbacks]
            rules_data = parsed.get("rules", [])
            for rule_data in rules_data:
                rule_type = rule_data.get("rule_type", "general")
                rule_text = rule_data.get("rule_text", "")
                if not rule_text:
                    continue

                existing = db.query(LearnedRule).filter_by(
                    rule_type=rule_type, rule_text=rule_text
                ).first()

                if existing:
                    result["rules_reinforced"] += 1
                else:
                    result["rules_created"] += 1

                LearnedRuleRepository.merge_or_create(
                    db=db,
                    rule_type=rule_type,
                    rule_text=rule_text,
                    rule_data=rule_data.get("rule_data"),
                    confidence=rule_data.get("confidence", 0.5),
                    source_query_ids=source_query_ids,
                )

            # 9. Process proposed skills (Phase 6)
            proposed_skills = parsed.get("proposed_skills", [])
            result["skills_proposed"] = self._save_proposed_skills(proposed_skills)

            # 9b. Process skill consolidation recommendations
            consolidations = parsed.get("skill_consolidations", [])
            result["skills_consolidated"] = self._process_skill_consolidations(consolidations)

            # 10. Mark feedback as processed
            feedback_ids = [fb.id for fb in feedbacks]
            QueryFeedbackRepository.mark_processed(db, feedback_ids)

            # Note: confidence decay and max-active enforcement are now handled
            # by the self-improvement agent's manage_rules tool, not hardcoded here.

            result["satisfaction_notes"] = parsed.get("satisfaction_notes", "")
            result["summary"] = parsed.get("summary", "")
            result["completed_at"] = datetime.utcnow().isoformat()

            self.logger.info(
                f"Retrospective complete: {result['feedback_processed']} feedback, "
                f"{result['rules_created']} new rules, {result['rules_reinforced']} reinforced, "
                f"{result['rules_decayed']} decayed, {result['skills_proposed']} skills proposed, "
                f"{result.get('skills_consolidated', 0)} skills consolidated"
            )

        except Exception as e:
            result["error"] = str(e)
            self.logger.error(f"Retrospective failed: {e}", exc_info=True)
        finally:
            db.close()

        self.last_run_result = result
        return result

    def _load_recent_conversations(self, db) -> str:
        """Load and format recent session conversations for analysis."""
        cutoff = datetime.utcnow() - timedelta(hours=24)
        sessions = db.query(ConversationSession).filter(
            ConversationSession.updated_at >= cutoff
        ).order_by(ConversationSession.updated_at.desc()).limit(10).all()

        if not sessions:
            return "No recent conversations."

        parts = []
        total_chars = 0
        max_chars = 30000  # Keep total under ~8K tokens

        for session in sessions:
            if not session.history:
                continue
            stripped = _strip_conversation_blocks(session.history)
            if not stripped:
                continue

            session_text = f"\n### Session {session.session_id[:8]}... ({session.updated_at.strftime('%Y-%m-%d %H:%M')})\n"
            for msg in stripped:
                line = f"**{msg['role'].upper()}**: {msg['text'][:500]}\n"
                session_text += line

            if total_chars + len(session_text) > max_chars:
                break
            parts.append(session_text)
            total_chars += len(session_text)

        return "\n".join(parts) if parts else "No recent conversations with content."

    def _load_journal_context(self) -> str:
        """Load recent journal file names/topics for vocabulary analysis."""
        # Try to read recent journal file names from the org directory
        org_dir = os.getenv("ORG_DIR", "")
        if not org_dir:
            return "No journal context available."

        from pathlib import Path
        journal_dir = Path(org_dir).expanduser()

        # Look for recent journal files
        cutoff = datetime.utcnow() - timedelta(days=7)
        recent_files = []

        for pattern in ["*.org", "journals/*.md", "journals/*.org"]:
            for f in journal_dir.glob(pattern):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime >= cutoff:
                        # Read first few lines for topic hints
                        with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                            preview = fh.read(500)
                        recent_files.append(f"- {f.name}: {preview[:200]}...")
                except (OSError, IOError):
                    continue

        if not recent_files:
            return "No recent journal entries found."

        return "\n".join(recent_files[:20])

    def _detect_abandoned_sessions(self, db) -> int:
        """Mark sessions where the last message is from the user and stale (>30 min).

        Returns the number of sessions marked as abandoned.
        """
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        # Get sessions updated in the last 24h but idle for 30+ min
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        sessions = db.query(ConversationSession).filter(
            ConversationSession.updated_at >= recent_cutoff,
            ConversationSession.updated_at < cutoff,
        ).all()

        count = 0
        for session in sessions:
            if not session.history:
                continue

            # Check if the last message is from the user
            last_msg = session.history[-1]
            if last_msg.get('role') != 'user':
                continue

            # Find the last query feedback for this session
            recent = QueryFeedbackRepository.get_recent_for_session(
                db, session.session_id, limit=1
            )
            if recent:
                prev = recent[0]
                if not prev.explicit_feedback:
                    QueryFeedbackExplicitRepository.mark_satisfaction(
                        db, prev.query_id, 'abandoned'
                    )
                    count += 1

        if count:
            self.logger.info(f"Retrospective: marked {count} sessions as abandoned")
        return count

    def _load_tool_execution_summaries(self, db) -> str:
        """Load and format recent tool execution logs for analysis."""
        logs = ToolExecutionLogExtendedRepository.get_recent_summaries(db, hours=24)

        if not logs:
            return "No tool executions in the last 24 hours."

        # Group by query_id
        grouped: Dict[str, list] = {}
        for log in logs:
            if log.query_id not in grouped:
                grouped[log.query_id] = []
            grouped[log.query_id].append(log)

        lines = []
        for _, query_logs in list(grouped.items())[:50]:  # Limit to 50 queries
            user_msg = query_logs[0].user_message[:100] if query_logs else '?'
            tool_chain = ' -> '.join(
                f"{log.tool_name}({'ok' if log.exit_code in (None, 0) else f'err:{log.exit_code}'})"
                for log in query_logs
            )
            helpful_status = ''
            for log in query_logs:
                if log.was_helpful is True:
                    helpful_status = ' [HELPFUL]'
                    break
                elif log.was_helpful is False:
                    helpful_status = ' [UNHELPFUL]'
                    break

            lines.append(f"- \"{user_msg}\" => {tool_chain}{helpful_status}")

        return "\n".join(lines)

    def _load_skills_catalog(self) -> str:
        """Load the existing skills catalog for retrospective analysis."""
        org_dir = os.getenv("ORG_DIR", "")
        if not org_dir:
            return "No skills directory available."

        from pathlib import Path
        from .tools.skills import _parse_skill_file

        skills_dir = Path(org_dir).expanduser() / '.pkm' / 'skills'
        if not skills_dir.exists():
            return "No skills saved yet."

        skills = []
        for filepath in sorted(skills_dir.iterdir()):
            if filepath.suffix not in ('.sh', '.md'):
                continue
            parsed = _parse_skill_file(filepath)
            if not parsed:
                continue
            skills.append(parsed)

        if not skills:
            return "No skills saved yet."

        lines = [f"Total: {len(skills)} skills\n"]
        for s in skills:
            name = s.get('name', s.get('_file', '?'))
            stype = s.get('_type', '?')
            desc = s.get('description', '')
            tags = ', '.join(s.get('tags', []))
            use_count = s.get('use_count', 0)
            body_preview = s.get('_body', '')[:150].replace('\n', ' ')
            lines.append(
                f"- **{name}** ({stype}): {desc}"
                + (f" [tags: {tags}]" if tags else "")
                + f" (used {use_count}x)"
                + f"\n  Content preview: {body_preview}..."
            )

        return "\n".join(lines)

    def _process_skill_consolidations(self, consolidations: list) -> int:
        """Process skill consolidation recommendations from retrospective.

        Removes redundant skill files that Opus identified as duplicates.
        Returns number of skills removed.
        """
        if not consolidations:
            return 0

        org_dir = os.getenv("ORG_DIR", "")
        if not org_dir:
            return 0

        from pathlib import Path
        skills_dir = Path(org_dir).expanduser() / '.pkm' / 'skills'
        if not skills_dir.exists():
            return 0

        count = 0
        for consolidation in consolidations:
            keep = consolidation.get("keep", "")
            remove_list = consolidation.get("remove", [])
            reason = consolidation.get("reason", "")

            # Only remove if the "keep" skill actually exists
            keep_exists = any(
                (skills_dir / f'{keep}{ext}').exists() for ext in ('.sh', '.md')
            )
            if not keep_exists:
                self.logger.warning(
                    f"Skill consolidation: '{keep}' (to keep) not found, skipping"
                )
                continue

            for skill_name in remove_list:
                for ext in ('.sh', '.md'):
                    filepath = skills_dir / f'{skill_name}{ext}'
                    if filepath.exists():
                        filepath.unlink()
                        self.logger.info(
                            f"Skill consolidated: removed '{skill_name}' "
                            f"(keeping '{keep}'): {reason}"
                        )
                        count += 1

        return count

    def _save_proposed_skills(self, proposed_skills: list) -> int:
        """Save proposed skills from retrospective to .pkm-skills/ if they don't exist.

        Returns number of skills created.
        """
        if not proposed_skills:
            return 0

        org_dir = os.getenv("ORG_DIR", "")
        if not org_dir:
            return 0

        from pathlib import Path
        from .tools.skills import SaveSkillTool

        save_tool = SaveSkillTool(
            logger=self.logger,
            org_dir=Path(org_dir).expanduser(),
            dangerous_patterns=[],  # Skip validation for retro-proposed skills
        )

        count = 0
        for skill in proposed_skills:
            skill_name = skill.get('skill_name', '')
            if not skill_name:
                continue

            # Check if skill already exists
            skills_dir = Path(org_dir).expanduser() / '.pkm' / 'skills'
            if (skills_dir / f'{skill_name}.sh').exists() or \
               (skills_dir / f'{skill_name}.md').exists():
                self.logger.debug(f"Skill '{skill_name}' already exists, skipping")
                continue

            try:
                result = save_tool.execute({
                    'skill_name': skill_name,
                    'skill_type': skill.get('skill_type', 'recipe'),
                    'description': skill.get('description', ''),
                    'content': skill.get('content', ''),
                    'trigger': skill.get('trigger', ''),
                    'tags': ['auto-proposed'],
                })
                self.logger.info(f"Retrospective proposed skill: {result}")
                count += 1
            except Exception as e:
                self.logger.warning(f"Failed to save proposed skill '{skill_name}': {e}")

        return count

    def _parse_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON response from Opus, handling potential markdown fencing."""
        text = response_text.strip()

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line (```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse retrospective response: {e}")
            self.logger.debug(f"Response text: {text[:500]}")
            return None
