"""Repository pattern for database operations."""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .database import OAuthToken, ConversationSession, UserSettings, ToolExecutionLog, QueryFeedback, LearnedRule, SessionNote, AgentRunLog


class OAuthRepository:
    """Repository for OAuth token operations."""

    @staticmethod
    def save_token(
        db: Session,
        service: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        scope: Optional[str] = None,
        user_id: str = 'default'
    ) -> OAuthToken:
        """Save or update OAuth token for a service."""
        # Check if token exists
        token = db.query(OAuthToken).filter_by(
            user_id=user_id,
            service=service
        ).first()

        if token:
            # Update existing token
            token.access_token = access_token
            if refresh_token:
                token.refresh_token = refresh_token
            token.expires_at = expires_at
            token.scope = scope
            token.updated_at = datetime.utcnow()
        else:
            # Create new token
            token = OAuthToken(
                user_id=user_id,
                service=service,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scope=scope
            )
            db.add(token)

        db.commit()
        db.refresh(token)
        return token

    @staticmethod
    def get_token(db: Session, service: str, user_id: str = 'default') -> Optional[OAuthToken]:
        """Get OAuth token for a service."""
        return db.query(OAuthToken).filter_by(
            user_id=user_id,
            service=service
        ).first()

    @staticmethod
    def delete_token(db: Session, service: str, user_id: str = 'default') -> bool:
        """Delete OAuth token for a service."""
        token = db.query(OAuthToken).filter_by(
            user_id=user_id,
            service=service
        ).first()

        if token:
            db.delete(token)
            db.commit()
            return True
        return False

    @staticmethod
    def is_token_expired(token: OAuthToken) -> bool:
        """Check if token is expired."""
        if not token.expires_at:
            return False
        return datetime.utcnow() >= token.expires_at


class SessionRepository:
    """Repository for conversation session operations."""

    @staticmethod
    def create_session(
        db: Session,
        session_id: str,
        system_prompt: Optional[str] = None,
        user_id: str = 'default'
    ) -> ConversationSession:
        """Create a new conversation session."""
        session = ConversationSession(
            session_id=session_id,
            user_id=user_id,
            history=[],
            system_prompt=system_prompt
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get_session(db: Session, session_id: str) -> Optional[ConversationSession]:
        """Get conversation session by ID."""
        return db.query(ConversationSession).filter_by(
            session_id=session_id
        ).first()

    @staticmethod
    def get_or_create_session(
        db: Session,
        session_id: str,
        system_prompt: Optional[str] = None,
        user_id: str = 'default'
    ) -> ConversationSession:
        """Get existing session or create new one."""
        session = SessionRepository.get_session(db, session_id)
        if not session:
            session = SessionRepository.create_session(
                db, session_id, system_prompt, user_id
            )
        return session

    @staticmethod
    def append_message(
        db: Session,
        session_id: str,
        role: str,
        content: Any
    ) -> ConversationSession:
        """Append a message to session history."""
        session = SessionRepository.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Ensure history is a list
        if not isinstance(session.history, list):
            session.history = []

        # Append message
        session.history.append({
            "role": role,
            "content": content
        })
        session.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def update_history(
        db: Session,
        session_id: str,
        history: List[Dict[str, Any]]
    ) -> ConversationSession:
        """Replace entire session history."""
        session = SessionRepository.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.history = history
        session.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def delete_session(db: Session, session_id: str) -> bool:
        """Delete a conversation session."""
        session = db.query(ConversationSession).filter_by(
            session_id=session_id
        ).first()

        if session:
            db.delete(session)
            db.commit()
            return True
        return False

    @staticmethod
    def update_session_cost(
        db: Session,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float
    ) -> ConversationSession:
        """Update session token and cost totals."""
        session = SessionRepository.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.total_input_tokens += input_tokens
        session.total_output_tokens += output_tokens
        session.total_cost += cost
        session.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get_all_sessions(db: Session, user_id: str = 'default') -> List[ConversationSession]:
        """Get all sessions for a user."""
        return db.query(ConversationSession).filter_by(
            user_id=user_id
        ).order_by(ConversationSession.updated_at.desc()).all()


class UserSettingsRepository:
    """Repository for user settings operations."""

    @staticmethod
    def get_user_context(db: Session, user_id: str = 'default') -> Optional[str]:
        """Get user context for a user.

        Returns:
            User context string or None if not set
        """
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()
        return settings.user_context if settings else None

    @staticmethod
    def save_user_context(db: Session, context: str, user_id: str = 'default') -> UserSettings:
        """Save or update user context.

        Args:
            db: Database session
            context: User context text
            user_id: User identifier

        Returns:
            Updated UserSettings object
        """
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()

        if settings:
            # Update existing settings
            settings.user_context = context
            settings.updated_at = datetime.utcnow()
        else:
            # Create new settings
            settings = UserSettings(
                user_id=user_id,
                user_context=context
            )
            db.add(settings)

        db.commit()
        db.refresh(settings)
        return settings

    @staticmethod
    def get_or_create_settings(db: Session, user_id: str = 'default') -> UserSettings:
        """Get existing settings or create new one with empty context.

        Args:
            db: Database session
            user_id: User identifier

        Returns:
            UserSettings object
        """
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id, user_context='')
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings


class ToolExecutionLogRepository:
    """Repository for tool execution log operations."""

    @staticmethod
    def create_log(
        db: Session,
        session_id: str,
        query_id: str,
        user_message: str,
        tool_name: str,
        tool_params: dict,
        result_summary: str,
        exit_code: Optional[int],
        execution_time_ms: int
    ) -> ToolExecutionLog:
        """Create a new tool execution log entry.

        Args:
            db: Database session
            session_id: Session ID
            query_id: Unique ID for this query (groups all logs from same request)
            user_message: The user's prompt that triggered the tool
            tool_name: Name of the tool executed
            tool_params: Parameters passed to the tool
            result_summary: Truncated result of tool execution
            exit_code: Exit code for shell commands (None for other tools)
            execution_time_ms: Execution time in milliseconds

        Returns:
            Created ToolExecutionLog object
        """
        log = ToolExecutionLog(
            session_id=session_id,
            query_id=query_id,
            user_message=user_message,
            tool_name=tool_name,
            tool_params=tool_params,
            result_summary=result_summary,
            exit_code=exit_code,
            execution_time_ms=execution_time_ms
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def get_logs_for_session(db: Session, session_id: str, limit: int = 100) -> List[ToolExecutionLog]:
        """Get tool execution logs for a session, ordered by creation time.

        Args:
            db: Database session
            session_id: Session ID
            limit: Maximum number of logs to return

        Returns:
            List of ToolExecutionLog objects
        """
        return db.query(ToolExecutionLog).filter_by(
            session_id=session_id
        ).order_by(ToolExecutionLog.created_at.desc()).limit(limit).all()

    @staticmethod
    def delete_old_logs(db: Session, days: int = 30) -> int:
        """Delete logs older than specified days (for maintenance).

        Args:
            db: Database session
            days: Delete logs older than this many days

        Returns:
            Number of logs deleted
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        count = db.query(ToolExecutionLog).filter(
            ToolExecutionLog.created_at < cutoff_date
        ).delete()
        db.commit()
        return count


class QueryFeedbackRepository:
    """Repository for query feedback operations."""

    @staticmethod
    def create(
        db: Session,
        session_id: str,
        query_id: str,
        user_message: str,
        had_rag_context: bool = False,
        rag_context_chars: int = 0,
        search_tools_used: Optional[List[str]] = None,
        tool_error_count: int = 0,
        total_tool_calls: int = 0,
        api_call_count: int = 1,
        retrieval_miss: bool = False,
        user_followup_correction: bool = False,
        explicit_feedback: Optional[str] = None,
        feedback_note: Optional[str] = None,
    ) -> QueryFeedback:
        """Create a new query feedback record."""
        feedback = QueryFeedback(
            session_id=session_id,
            query_id=query_id,
            user_message=user_message,
            had_rag_context=had_rag_context,
            rag_context_chars=rag_context_chars,
            search_tools_used=search_tools_used or [],
            tool_error_count=tool_error_count,
            total_tool_calls=total_tool_calls,
            api_call_count=api_call_count,
            retrieval_miss=retrieval_miss,
            user_followup_correction=user_followup_correction,
            explicit_feedback=explicit_feedback,
            feedback_note=feedback_note,
        )
        db.add(feedback)
        db.commit()
        db.refresh(feedback)
        return feedback

    @staticmethod
    def get_unprocessed(db: Session, limit: int = 200) -> List[QueryFeedback]:
        """Get unprocessed feedback records for retrospective analysis."""
        return db.query(QueryFeedback).filter(
            QueryFeedback.processed == False
        ).order_by(QueryFeedback.created_at.asc()).limit(limit).all()

    @staticmethod
    def mark_processed(db: Session, feedback_ids: List[int]) -> None:
        """Mark feedback records as processed."""
        if not feedback_ids:
            return
        db.query(QueryFeedback).filter(
            QueryFeedback.id.in_(feedback_ids)
        ).update({
            QueryFeedback.processed: True,
            QueryFeedback.processed_at: datetime.utcnow()
        }, synchronize_session='fetch')
        db.commit()

    @staticmethod
    def get_recent_for_session(db: Session, session_id: str, limit: int = 1) -> List[QueryFeedback]:
        """Get most recent feedback records for a session."""
        return db.query(QueryFeedback).filter(
            QueryFeedback.session_id == session_id
        ).order_by(QueryFeedback.created_at.desc()).limit(limit).all()

    @staticmethod
    def mark_correction(db: Session, query_id: str) -> None:
        """Mark a query feedback record as having a user follow-up correction."""
        feedback = db.query(QueryFeedback).filter(
            QueryFeedback.query_id == query_id
        ).first()
        if feedback:
            feedback.user_followup_correction = True
            db.commit()

    @staticmethod
    def get_stats(db: Session, days: int = 7) -> Dict[str, Any]:
        """Get feedback statistics for the last N days."""
        from sqlalchemy import func
        cutoff = datetime.utcnow() - timedelta(days=days)
        total = db.query(func.count(QueryFeedback.id)).filter(
            QueryFeedback.created_at >= cutoff
        ).scalar() or 0
        misses = db.query(func.count(QueryFeedback.id)).filter(
            QueryFeedback.created_at >= cutoff,
            QueryFeedback.retrieval_miss == True
        ).scalar() or 0
        corrections = db.query(func.count(QueryFeedback.id)).filter(
            QueryFeedback.created_at >= cutoff,
            QueryFeedback.user_followup_correction == True
        ).scalar() or 0
        return {
            "total_queries": total,
            "retrieval_misses": misses,
            "user_corrections": corrections,
            "miss_rate": misses / total if total > 0 else 0,
            "correction_rate": corrections / total if total > 0 else 0,
        }


class LearnedRuleRepository:
    """Repository for learned rule operations."""

    @staticmethod
    def create(
        db: Session,
        rule_type: str,
        rule_text: str,
        rule_data: Optional[Dict[str, Any]] = None,
        confidence: float = 0.5,
        source_query_ids: Optional[List[str]] = None,
    ) -> LearnedRule:
        """Create a new learned rule."""
        rule = LearnedRule(
            rule_type=rule_type,
            rule_text=rule_text,
            rule_data=rule_data,
            confidence=confidence,
            source_query_ids=source_query_ids or [],
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def get_active(db: Session) -> List[LearnedRule]:
        """Get all active learned rules, ordered by confidence descending."""
        return db.query(LearnedRule).filter(
            LearnedRule.is_active == True
        ).order_by(LearnedRule.confidence.desc()).all()

    @staticmethod
    def get_all(db: Session) -> List[LearnedRule]:
        """Get all learned rules (active and inactive)."""
        return db.query(LearnedRule).order_by(
            LearnedRule.is_active.desc(),
            LearnedRule.confidence.desc()
        ).all()

    @staticmethod
    def get_by_id(db: Session, rule_id: int) -> Optional[LearnedRule]:
        """Get a rule by ID."""
        return db.query(LearnedRule).filter(LearnedRule.id == rule_id).first()

    @staticmethod
    def update(
        db: Session,
        rule_id: int,
        **kwargs
    ) -> Optional[LearnedRule]:
        """Update a learned rule's fields."""
        rule = db.query(LearnedRule).filter(LearnedRule.id == rule_id).first()
        if not rule:
            return None
        for key, value in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        rule.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def delete(db: Session, rule_id: int) -> bool:
        """Delete a learned rule."""
        rule = db.query(LearnedRule).filter(LearnedRule.id == rule_id).first()
        if rule:
            db.delete(rule)
            db.commit()
            return True
        return False

    @staticmethod
    def merge_or_create(
        db: Session,
        rule_type: str,
        rule_text: str,
        rule_data: Optional[Dict[str, Any]] = None,
        confidence: float = 0.5,
        source_query_ids: Optional[List[str]] = None,
    ) -> LearnedRule:
        """Find a similar existing rule and reinforce it, or create a new one.

        Matching is by rule_type and exact rule_text. In practice the retrospective
        module should try to reuse existing rule text for reinforcement.
        """
        existing = db.query(LearnedRule).filter(
            LearnedRule.rule_type == rule_type,
            LearnedRule.rule_text == rule_text,
        ).first()

        if existing:
            existing.hit_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.1)
            existing.last_reinforced_at = datetime.utcnow()
            existing.is_active = True  # reactivate if it was decayed
            if source_query_ids:
                existing_ids = existing.source_query_ids or []
                existing.source_query_ids = existing_ids + source_query_ids
            if rule_data:
                existing.rule_data = rule_data
            db.commit()
            db.refresh(existing)
            return existing

        return LearnedRuleRepository.create(
            db, rule_type, rule_text, rule_data, confidence, source_query_ids
        )

    @staticmethod
    def decay_confidence(db: Session, days_threshold: int = 30, decay_amount: float = 0.1) -> int:
        """Decay confidence of rules not reinforced within the threshold.

        Returns the number of rules decayed.
        """
        cutoff = datetime.utcnow() - timedelta(days=days_threshold)
        stale_rules = db.query(LearnedRule).filter(
            LearnedRule.is_active == True,
            LearnedRule.last_reinforced_at < cutoff,
        ).all()

        count = 0
        for rule in stale_rules:
            rule.confidence = max(0.0, rule.confidence - decay_amount)
            if rule.confidence < 0.3:
                rule.is_active = False
            count += 1

        if count > 0:
            db.commit()
        return count

    @staticmethod
    def enforce_max_active(db: Session, max_active: int = 30) -> int:
        """Deactivate lowest-confidence rules if over the max active limit.

        Returns the number of rules deactivated.
        """
        active_rules = db.query(LearnedRule).filter(
            LearnedRule.is_active == True
        ).order_by(LearnedRule.confidence.desc()).all()

        if len(active_rules) <= max_active:
            return 0

        deactivated = 0
        for rule in active_rules[max_active:]:
            rule.is_active = False
            deactivated += 1

        if deactivated > 0:
            db.commit()
        return deactivated

    @staticmethod
    def get_vocabulary_rules(db: Session) -> List[LearnedRule]:
        """Get active vocabulary-type rules for query expansion."""
        return db.query(LearnedRule).filter(
            LearnedRule.is_active == True,
            LearnedRule.rule_type == 'vocabulary',
        ).all()


class QueryFeedbackExplicitRepository:
    """Extended operations for explicit feedback (Phase 2)."""

    @staticmethod
    def update_explicit_feedback(
        db: Session,
        query_id: str,
        feedback: str,
        note: Optional[str] = None,
    ) -> bool:
        """Update explicit feedback on a query.

        Args:
            db: Database session
            query_id: The query ID to update
            feedback: 'positive' or 'negative'
            note: Optional user note explaining the feedback

        Returns:
            True if feedback was recorded, False if query_id not found
        """
        record = db.query(QueryFeedback).filter(
            QueryFeedback.query_id == query_id
        ).first()

        if not record:
            return False

        record.explicit_feedback = feedback
        if note:
            record.feedback_note = note
        db.commit()
        return True

    @staticmethod
    def mark_satisfaction(
        db: Session,
        query_id: str,
        feedback_type: str = 'positive_implicit',
    ) -> bool:
        """Mark a query as having implicit positive satisfaction.

        Args:
            db: Database session
            query_id: The query ID to mark
            feedback_type: Type of implicit feedback (e.g., 'positive_implicit', 'abandoned')

        Returns:
            True if marked, False if not found or already has explicit feedback
        """
        record = db.query(QueryFeedback).filter(
            QueryFeedback.query_id == query_id
        ).first()

        if not record:
            return False

        # Don't overwrite explicit feedback
        if record.explicit_feedback and record.explicit_feedback in ('positive', 'negative'):
            return False

        record.explicit_feedback = feedback_type
        db.commit()
        return True


class ToolExecutionLogExtendedRepository:
    """Extended operations for tool execution logs (Phase 4)."""

    @staticmethod
    def mark_helpful(db: Session, query_id: str) -> int:
        """Mark all tool executions for a query as helpful.

        Returns number of records updated.
        """
        count = db.query(ToolExecutionLog).filter(
            ToolExecutionLog.query_id == query_id,
            ToolExecutionLog.tool_name != '__query_summary__',
        ).update({ToolExecutionLog.was_helpful: True}, synchronize_session='fetch')
        db.commit()
        return count

    @staticmethod
    def mark_unhelpful(db: Session, query_id: str) -> int:
        """Mark all tool executions for a query as unhelpful.

        Returns number of records updated.
        """
        count = db.query(ToolExecutionLog).filter(
            ToolExecutionLog.query_id == query_id,
            ToolExecutionLog.tool_name != '__query_summary__',
        ).update({ToolExecutionLog.was_helpful: False}, synchronize_session='fetch')
        db.commit()
        return count

    @staticmethod
    def get_recent_summaries(db: Session, hours: int = 24, limit: int = 500) -> List[ToolExecutionLog]:
        """Get recent tool execution logs for retrospective analysis."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return db.query(ToolExecutionLog).filter(
            ToolExecutionLog.created_at >= cutoff,
            ToolExecutionLog.tool_name != '__query_summary__',
        ).order_by(ToolExecutionLog.created_at.asc()).limit(limit).all()


class SessionNoteRepository:
    """Repository for per-session working memory notes."""

    @staticmethod
    def create(
        db: Session,
        session_id: str,
        note: str,
        category: str = 'other',
    ) -> SessionNote:
        """Create a new session note."""
        session_note = SessionNote(
            session_id=session_id,
            note=note,
            category=category,
        )
        db.add(session_note)
        db.commit()
        db.refresh(session_note)
        return session_note

    @staticmethod
    def get_for_session(db: Session, session_id: str) -> List[SessionNote]:
        """Get all notes for a session, ordered by creation time."""
        return db.query(SessionNote).filter(
            SessionNote.session_id == session_id,
        ).order_by(SessionNote.created_at.asc()).all()


class AgentRunLogRepository:
    """Repository for self-improvement agent run logs."""

    @staticmethod
    def create(
        db: Session,
        started_at: datetime,
        completed_at: Optional[datetime] = None,
        trigger: str = "scheduled",
        turns_used: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        actions_summary: Optional[List[Dict[str, Any]]] = None,
        summary: Optional[str] = None,
        error: Optional[str] = None,
        run_file: Optional[str] = None,
    ) -> AgentRunLog:
        """Create a new agent run log entry."""
        log = AgentRunLog(
            started_at=started_at,
            completed_at=completed_at,
            trigger=trigger,
            turns_used=turns_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actions_summary=actions_summary,
            summary=summary,
            error=error,
            run_file=run_file,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def get_recent(db: Session, limit: int = 10) -> List[AgentRunLog]:
        """Get recent agent run logs, newest first."""
        return db.query(AgentRunLog).order_by(
            AgentRunLog.started_at.desc()
        ).limit(limit).all()

    @staticmethod
    def get_latest(db: Session) -> Optional[AgentRunLog]:
        """Get the most recent agent run log."""
        return db.query(AgentRunLog).order_by(
            AgentRunLog.started_at.desc()
        ).first()
