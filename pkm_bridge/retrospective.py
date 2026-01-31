"""Daily retrospective analysis pipeline.

Gathers unprocessed QueryFeedback records, reviews recent session conversations,
and uses Opus to identify patterns and generate LearnedRule records.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db, ConversationSession, LearnedRule
from .db_repository import QueryFeedbackRepository, LearnedRuleRepository

RETROSPECTIVE_MODEL = "claude-opus-4-5-20251101"

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

## Rules for generating rules

- Only generate rules you have evidence for from the data above.
- If an existing rule is still valid, include it with the EXACT same rule_text to reinforce it.
- If an existing rule is contradicted by new evidence, generate a replacement with new text.
- Keep rule_text concise (1-2 sentences).
- For vocabulary rules, include structured data in rule_data with "user_term" and "note_terms" keys.
- Confidence should reflect how strong the evidence is (0.3-0.7 for new rules).

Respond with ONLY a JSON object (no markdown fencing):
{{
  "rules": [
    {{
      "rule_type": "retrieval|vocabulary|preference|embedding_gap|general",
      "rule_text": "human-readable description of the rule",
      "rule_data": {{}},
      "confidence": 0.5
    }}
  ],
  "satisfaction_notes": "Brief assessment of overall user satisfaction",
  "summary": "Brief summary of what was analyzed and key findings"
}}
"""


def _strip_conversation_blocks(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Strip tool_use, tool_result, and thinking blocks from conversation history.

    Keeps only user messages and assistant final text responses to reduce token count.
    """
    stripped = []
    for msg in history:
        role = msg.get('role', '')
        content = msg.get('content', '')

        if role == 'user':
            if isinstance(content, str):
                stripped.append({"role": "user", "text": content})
            elif isinstance(content, list):
                # User messages with tool_result blocks - skip these
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'tool_result':
                        continue
                    if isinstance(item, str):
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
            "error": None,
        }

        db = get_db()
        try:
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

            # 5. Build and send the prompt to Opus
            prompt = RETROSPECTIVE_PROMPT.format(
                existing_rules=_format_existing_rules(existing_rules),
                feedback_summary=_format_feedback_summary(feedbacks),
                conversations=conversations_text,
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

            # 6. Parse JSON response
            parsed = self._parse_response(response_text)
            if not parsed:
                result["error"] = "Failed to parse Opus response"
                self.last_run_result = result
                return result

            # 7. Merge rules
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

            # 8. Mark feedback as processed
            feedback_ids = [fb.id for fb in feedbacks]
            QueryFeedbackRepository.mark_processed(db, feedback_ids)

            # 9. Apply confidence decay
            result["rules_decayed"] = LearnedRuleRepository.decay_confidence(db)

            # 10. Enforce max active rules
            result["rules_deactivated"] = LearnedRuleRepository.enforce_max_active(db)

            result["satisfaction_notes"] = parsed.get("satisfaction_notes", "")
            result["summary"] = parsed.get("summary", "")
            result["completed_at"] = datetime.utcnow().isoformat()

            self.logger.info(
                f"Retrospective complete: {result['feedback_processed']} feedback, "
                f"{result['rules_created']} new rules, {result['rules_reinforced']} reinforced, "
                f"{result['rules_decayed']} decayed"
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
