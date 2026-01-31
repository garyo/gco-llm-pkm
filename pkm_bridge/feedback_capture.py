"""Implicit signal detection and QueryFeedback creation.

Captures per-query signals at the end of every query (no API cost).
Detects retrieval misses, tool errors, multi-search patterns,
and user follow-up corrections.
"""

import re
from typing import List, Optional

from .database import get_db
from .db_repository import QueryFeedbackRepository

# Search tools that indicate Claude is looking for information beyond RAG
SEARCH_TOOL_NAMES = {'search_notes', 'find_context', 'semantic_search'}

# Patterns indicating user dissatisfaction in follow-up messages
CORRECTION_PATTERNS = [
    r'\bno\b[,.]?\s*(?:that|those|this)',
    r'\bwrong\b',
    r'\bnot what i (?:meant|asked|wanted)\b',
    r'\bactually i was asking about\b',
    r"\bcouldn'?t find\b",
    r"\bdidn'?t find\b",
    r"\bthat'?s not (?:it|right|correct)\b",
    r'\btry again\b',
    r'\bi (?:meant|mean)\b',
    r'\bnot (?:quite|exactly|really)\b',
]
CORRECTION_RE = re.compile('|'.join(CORRECTION_PATTERNS), re.IGNORECASE)

# Short negation responses (entire message is just a negation)
SHORT_NEGATION_RE = re.compile(
    r'^\s*(?:no\.?|nope\.?|nah\.?|wrong\.?|incorrect\.?)\s*$',
    re.IGNORECASE,
)


def detect_correction(user_message: str) -> bool:
    """Check if a user message indicates dissatisfaction with the previous response."""
    if SHORT_NEGATION_RE.match(user_message):
        return True
    return bool(CORRECTION_RE.search(user_message))


def capture_feedback(
    session_id: str,
    query_id: str,
    user_message: str,
    had_rag_context: bool,
    rag_context_chars: int,
    tool_names_used: List[str],
    tool_error_count: int,
    total_tool_calls: int,
    api_call_count: int,
    logger,
) -> None:
    """Build and store a QueryFeedback record from signals available after a query.

    This is called at the end of the tool loop, before returning the response.
    """
    search_tools_used = [t for t in tool_names_used if t in SEARCH_TOOL_NAMES]
    retrieval_miss = had_rag_context and len(search_tools_used) > 0

    try:
        db = get_db()
        try:
            QueryFeedbackRepository.create(
                db=db,
                session_id=session_id,
                query_id=query_id,
                user_message=user_message,
                had_rag_context=had_rag_context,
                rag_context_chars=rag_context_chars,
                search_tools_used=search_tools_used,
                tool_error_count=tool_error_count,
                total_tool_calls=total_tool_calls,
                api_call_count=api_call_count,
                retrieval_miss=retrieval_miss,
            )
            if retrieval_miss:
                logger.info(f"Feedback: retrieval miss detected (RAG present but {len(search_tools_used)} search tools used)")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to capture query feedback: {e}")


def check_previous_correction(
    session_id: str,
    user_message: str,
    logger,
) -> None:
    """Check if the current user message is a correction of the previous query.

    If so, retroactively mark the previous query's feedback record.
    """
    if not detect_correction(user_message):
        return

    try:
        db = get_db()
        try:
            recent = QueryFeedbackRepository.get_recent_for_session(db, session_id, limit=1)
            if recent:
                prev = recent[0]
                if not prev.user_followup_correction:
                    QueryFeedbackRepository.mark_correction(db, prev.query_id)
                    logger.info(f"Feedback: marked previous query {prev.query_id} as corrected")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to check previous correction: {e}")
