"""Repository pattern for database operations."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .database import OAuthToken, ConversationSession, UserSettings


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
