"""Database models and connection management."""

import os
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, JSON, Text, Index, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from sqlalchemy.pool import QueuePool
from pgvector.sqlalchemy import Vector

Base = declarative_base()


class OAuthToken(Base):
    """Store OAuth tokens for external services."""
    __tablename__ = 'oauth_tokens'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, default='default')
    service = Column(String(50), nullable=False)  # e.g., 'ticktick'
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String(50), default='Bearer')
    expires_at = Column(DateTime, nullable=True)
    scope = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<OAuthToken(service='{self.service}', user_id='{self.user_id}', expires_at='{self.expires_at}')>"


class ConversationSession(Base):
    """Store conversation history for chat sessions."""
    __tablename__ = 'conversation_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(String(255), nullable=False, default='default')
    history = Column(JSON, nullable=False, default=list)  # List of message dicts
    system_prompt = Column(Text, nullable=True)  # Optional custom system prompt
    total_input_tokens = Column(Integer, nullable=False, default=0)  # Cumulative input tokens
    total_output_tokens = Column(Integer, nullable=False, default=0)  # Cumulative output tokens
    total_cost = Column(Float, nullable=False, default=0.0)  # Cumulative cost in USD
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ConversationSession(session_id='{self.session_id}', messages={len(self.history)})>"


class UserSettings(Base):
    """Store user settings and context."""
    __tablename__ = 'user_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), unique=True, nullable=False, index=True, default='default')
    user_context = Column(Text, nullable=True)  # Personal context for system prompt
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<UserSettings(user_id='{self.user_id}', context_length={len(self.user_context or '')})"


class ToolExecutionLog(Base):
    """Store tool execution logs for debugging and activity tracking."""
    __tablename__ = 'tool_execution_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    query_id = Column(String(100), nullable=False, index=True)  # Unique ID for grouping logs from same query
    user_message = Column(Text, nullable=False)  # The user's prompt
    tool_name = Column(String(100), nullable=False)
    tool_params = Column(JSON, nullable=False)
    result_summary = Column(Text, nullable=True)  # Truncated result
    exit_code = Column(Integer, nullable=True)  # For shell commands
    execution_time_ms = Column(Integer, nullable=False)  # Duration in milliseconds
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<ToolExecutionLog(tool='{self.tool_name}', session='{self.session_id}', time={self.execution_time_ms}ms)>"


# Database connection management
_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """Get database URL from environment with automatic password encoding.

    Supports placeholders in DATABASE_URL:
    - {DB_USER} or {USER} - replaced with DB_USER env var
    - {DB_PASSWORD} or {PASSWORD} - replaced with URL-encoded DB_PASSWORD env var

    Example:
        DATABASE_URL=postgresql://{DB_USER}:{DB_PASSWORD}@localhost:5432/pkm_db
        DB_USER=pkm
        DB_PASSWORD=my@pass#word

    The password will be automatically URL-encoded to handle special characters.
    """
    database_url = os.getenv('DATABASE_URL', 'postgresql://pkm:pkm@localhost:5432/pkm_db')

    # Check if URL contains placeholders
    if '{' in database_url:
        # Get credentials from environment
        db_user = os.getenv('DB_USER', 'pkm')
        db_password = os.getenv('DB_PASSWORD', '')

        # URL-encode the password to handle special characters
        encoded_password = quote_plus(db_password) if db_password else ''

        # Substitute placeholders
        database_url = database_url.replace('{DB_USER}', db_user)
        database_url = database_url.replace('{USER}', db_user)
        database_url = database_url.replace('{DB_PASSWORD}', encoded_password)
        database_url = database_url.replace('{PASSWORD}', encoded_password)

    return database_url


def init_db() -> None:
    """Initialize database connection and create tables."""
    global _engine, _SessionLocal

    database_url = get_database_url()

    # Debug: Log the database URL (mask password for security)
    import re
    masked_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', database_url)
    print(f"[DEBUG] Connecting to database: {masked_url}", flush=True)

    _engine = create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before using
        echo=False  # Set to True for SQL debugging
    )

    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # Create all tables
    Base.metadata.create_all(bind=_engine)


def get_db() -> Session:
    """Get a database session. Use with context manager."""
    if _SessionLocal is None:
        init_db()

    db = _SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def close_db() -> None:
    """Close database connection."""
    global _engine
    if _engine:
        _engine.dispose()
        _engine = None


class Document(Base):
    """Track which files have been embedded for RAG."""
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String(1024), unique=True, nullable=False, index=True)
    file_type = Column(String(10), nullable=False)  # 'org' or 'md'
    file_hash = Column(String(64), nullable=False)  # SHA256 for change detection
    date_extracted = Column(String(20), nullable=True)  # YYYY-MM-DD
    total_chunks = Column(Integer, nullable=False, default=0)
    last_embedded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Document(file_path='{self.file_path}', chunks={self.total_chunks})>"


class DocumentChunk(Base):
    """Store note chunks with vector embeddings for semantic search."""
    __tablename__ = 'document_chunks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)  # Position within document
    chunk_type = Column(String(20), nullable=False)  # 'heading', 'content', 'bullet'
    heading_path = Column(Text, nullable=True)  # "* Top\n** Second\n*** Current"
    content = Column(Text, nullable=False)  # Actual text content
    start_line = Column(Integer, nullable=True)  # Line number in original file
    token_count = Column(Integer, nullable=False)  # Approximate tokens

    # pgvector embedding (voyage-3 uses 1024 dimensions)
    embedding = Column(Vector(1024), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return f"<DocumentChunk(document_id={self.document_id}, chunk={self.chunk_index}, tokens={self.token_count})>"

    # Vector similarity search index
    __table_args__ = (
        Index('idx_embedding_cosine', embedding, postgresql_using='ivfflat',
              postgresql_with={'lists': 100}, postgresql_ops={'embedding': 'vector_cosine_ops'}),
    )
