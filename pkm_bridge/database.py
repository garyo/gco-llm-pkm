"""Database models and connection management."""

import os
import threading
import time
from datetime import datetime
from urllib.parse import quote_plus

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool

Base = declarative_base()


class OAuthToken(Base):
    """Store OAuth tokens for external services."""

    __tablename__ = "oauth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, default="default")
    service = Column(String(50), nullable=False)  # e.g., 'ticktick'
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String(50), default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    scope = Column(String(255), nullable=True)
    # Set when the provider rejected a refresh (e.g. revoked grant); cleared by a new token.
    refresh_error = Column(Text, nullable=True)
    refresh_failed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<OAuthToken(service='{self.service}', user_id='{self.user_id}', "
            f"expires_at='{self.expires_at}')>"
        )


class ConversationSession(Base):
    """Store conversation history for chat sessions."""

    __tablename__ = "conversation_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(String(255), nullable=False, default="default")
    history = Column(JSON, nullable=False, default=list)  # List of message dicts
    system_prompt = Column(Text, nullable=True)  # Optional custom system prompt
    total_input_tokens = Column(Integer, nullable=False, default=0)  # Cumulative input tokens
    total_output_tokens = Column(Integer, nullable=False, default=0)  # Cumulative output tokens
    total_cache_write_tokens = Column(
        Integer, nullable=False, default=0, server_default="0"
    )  # Cumulative cache write tokens
    total_cache_read_tokens = Column(
        Integer, nullable=False, default=0, server_default="0"
    )  # Cumulative cache read tokens
    total_cost = Column(Float, nullable=False, default=0.0)  # Cumulative cost in USD
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<ConversationSession(session_id='{self.session_id}', messages={len(self.history)})>"
        )


class UserSettings(Base):
    """Store user settings and context."""

    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), unique=True, nullable=False, index=True, default="default")
    user_context = Column(Text, nullable=True)  # Personal context for system prompt
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<UserSettings(user_id='{self.user_id}', "
            f"context_length={len(self.user_context or '')})"
        )


class ToolExecutionLog(Base):
    """Store tool execution logs for debugging and activity tracking."""

    __tablename__ = "tool_execution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    query_id = Column(
        String(100), nullable=False, index=True
    )  # Unique ID for grouping logs from same query
    user_message = Column(Text, nullable=False)  # The user's prompt
    tool_name = Column(String(100), nullable=False)
    tool_params = Column(JSON, nullable=False)
    result_summary = Column(Text, nullable=True)  # Truncated result
    exit_code = Column(Integer, nullable=True)  # For shell commands
    execution_time_ms = Column(Integer, nullable=False)  # Duration in milliseconds
    was_helpful = Column(Boolean, nullable=True)  # Derived from satisfaction/correction signals
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<ToolExecutionLog(tool='{self.tool_name}', session='{self.session_id}', "
            f"time={self.execution_time_ms}ms)>"
        )


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
    database_url = os.getenv("DATABASE_URL", "postgresql://pkm:pkm@localhost:5432/pkm_db")

    # Check if URL contains placeholders
    if "{" in database_url:
        # Get credentials from environment
        db_user = os.getenv("DB_USER", "pkm")
        db_password = os.getenv("DB_PASSWORD", "")

        # URL-encode the password to handle special characters
        encoded_password = quote_plus(db_password) if db_password else ""

        # Substitute placeholders
        database_url = database_url.replace("{DB_USER}", db_user)
        database_url = database_url.replace("{USER}", db_user)
        database_url = database_url.replace("{DB_PASSWORD}", encoded_password)
        database_url = database_url.replace("{PASSWORD}", encoded_password)

    return database_url


# HNSW replaced the original IVFFlat index (lists=100, built on an empty
# table so its centroids were meaningless, queried with probes=1), which
# found only 0.40 of the true top-30 neighbours in production.
VECTOR_INDEX = "idx_embedding_hnsw"
_OLD_VECTOR_INDEX = "idx_embedding_cosine"
_VECTOR_INDEX_LOCK = 0x504B4D01  # pg advisory lock key: one process builds
_FIND_CHUNK_INDEXES = text("SELECT indexname FROM pg_indexes WHERE tablename = 'document_chunks'")


def _start_vector_index_upgrade(engine) -> None:
    """Upgrade the vector index in a background thread, if it needs it.

    Building HNSW over ~25k chunks takes about a minute, longer than the
    container health check allows for startup; searches use the old index
    until the new one commits.
    """
    try:
        with engine.connect() as conn:
            existing = set(conn.execute(_FIND_CHUNK_INDEXES).scalars())
    except Exception as e:
        print(f"[DB] WARNING: could not check vector index: {e}", flush=True)
        return
    if VECTOR_INDEX in existing and _OLD_VECTOR_INDEX not in existing:
        return
    threading.Thread(
        target=_upgrade_vector_index, args=(engine,), name="vector-index-upgrade", daemon=True
    ).start()


def _upgrade_vector_index(engine) -> None:
    """Build the HNSW embedding index and drop the old IVFFlat one, once.

    Runs in one transaction, so a failure or crash leaves the old index in
    place and the next startup retries. The build takes a SHARE lock:
    searches keep working (on the old index), embedding writes wait for it.
    Failures are logged, never raised.
    """
    try:
        with engine.begin() as conn:
            got_lock = conn.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _VECTOR_INDEX_LOCK}
            ).scalar()
            if not got_lock:
                print("[DB] Another process is upgrading the vector index; skipping", flush=True)
                return
            existing = set(conn.execute(_FIND_CHUNK_INDEXES).scalars())
            if VECTOR_INDEX not in existing:
                print(f"[DB] Building HNSW index {VECTOR_INDEX}...", flush=True)
                start = time.monotonic()
                # The default 64MB can't hold the graph, making the build far slower
                conn.execute(text("SET LOCAL maintenance_work_mem = '512MB'"))
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {VECTOR_INDEX} ON document_chunks "
                        "USING hnsw (embedding vector_cosine_ops)"
                    )
                )
                print(f"[DB] Built {VECTOR_INDEX} in {time.monotonic() - start:.1f}s", flush=True)
            if _OLD_VECTOR_INDEX in existing:
                conn.execute(text(f"DROP INDEX IF EXISTS {_OLD_VECTOR_INDEX}"))
                print(f"[DB] Dropped old IVFFlat index {_OLD_VECTOR_INDEX}", flush=True)
    except Exception as e:
        print(f"[DB] WARNING: vector index upgrade failed, keeping old index: {e}", flush=True)


def _upgrade_schema(engine) -> None:
    """Add missing columns to existing tables (lightweight migration)."""
    insp = inspect(engine)

    # ConversationSession: add cache token columns if missing
    if "conversation_sessions" in insp.get_table_names():
        columns = {c["name"] for c in insp.get_columns("conversation_sessions")}
        with engine.begin() as conn:
            if "total_cache_write_tokens" not in columns:
                conn.execute(
                    text(
                        "ALTER TABLE conversation_sessions "
                        "ADD COLUMN total_cache_write_tokens INTEGER NOT NULL DEFAULT 0"
                    )
                )
                print(
                    "[DB] Added 'total_cache_write_tokens' column to conversation_sessions",
                    flush=True,
                )
            if "total_cache_read_tokens" not in columns:
                conn.execute(
                    text(
                        "ALTER TABLE conversation_sessions "
                        "ADD COLUMN total_cache_read_tokens INTEGER NOT NULL DEFAULT 0"
                    )
                )
                print(
                    "[DB] Added 'total_cache_read_tokens' column to conversation_sessions",
                    flush=True,
                )

    # DocumentChunk: expression GIN index backing hybrid keyword search
    # (context_retriever). Queries must use the identical
    # to_tsvector('english', content) expression to hit this index.
    if "document_chunks" in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_content_fts "
                    "ON document_chunks USING gin (to_tsvector('english', content))"
                )
            )
        _start_vector_index_upgrade(engine)

    # ScheduledTask: add per-task model override if missing
    if "scheduled_tasks" in insp.get_table_names():
        columns = {c["name"] for c in insp.get_columns("scheduled_tasks")}
        if "model" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN model VARCHAR(100)"))
                print("[DB] Added 'model' column to scheduled_tasks", flush=True)

    # ToolExecutionLog: add was_helpful column if missing
    if "tool_execution_logs" in insp.get_table_names():
        columns = {c["name"] for c in insp.get_columns("tool_execution_logs")}
        if "was_helpful" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE tool_execution_logs ADD COLUMN was_helpful BOOLEAN"))
                print("[DB] Added 'was_helpful' column to tool_execution_logs", flush=True)

    # Background-job usage: cache tokens and dollar cost next to the token counts
    cost_columns = {
        "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cost_usd": "DOUBLE PRECISION NOT NULL DEFAULT 0",
    }
    for table in ("scheduled_task_runs", "daily_token_usage", "agent_run_log"):
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        missing = {name: ddl for name, ddl in cost_columns.items() if name not in existing}
        if not missing:
            continue
        with engine.begin() as conn:
            for name, ddl in missing.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                print(f"[DB] Added '{name}' column to {table}", flush=True)

    # OAuthToken: record rejected refreshes
    if "oauth_tokens" in insp.get_table_names():
        columns = {c["name"] for c in insp.get_columns("oauth_tokens")}
        with engine.begin() as conn:
            if "refresh_error" not in columns:
                conn.execute(text("ALTER TABLE oauth_tokens ADD COLUMN refresh_error TEXT"))
                print("[DB] Added 'refresh_error' column to oauth_tokens", flush=True)
            if "refresh_failed_at" not in columns:
                conn.execute(
                    text("ALTER TABLE oauth_tokens ADD COLUMN refresh_failed_at TIMESTAMP")
                )
                print("[DB] Added 'refresh_failed_at' column to oauth_tokens", flush=True)


def init_db() -> None:
    """Initialize database connection and create tables."""
    global _engine, _SessionLocal

    database_url = get_database_url()

    # Debug: Log the database URL (mask password for security)
    import re

    masked_url = re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", database_url)
    print(f"[DEBUG] Connecting to database: {masked_url}", flush=True)

    _engine = create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before using
        echo=False,  # Set to True for SQL debugging
    )

    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # Create all tables (new tables auto-created; existing tables need ALTER for new columns)
    Base.metadata.create_all(bind=_engine)

    # Add any missing columns to existing tables
    _upgrade_schema(_engine)


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


class FileVersion(Base):
    """Recently seen note contents by hash: the merge bases for concurrent saves."""

    __tablename__ = "file_versions"

    hash = Column(String(64), primary_key=True)
    path = Column(String(1024), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class Document(Base):
    """Track which files have been embedded for RAG."""

    __tablename__ = "documents"

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

    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index = Column(Integer, nullable=False)  # Position within document
    chunk_type = Column(String(20), nullable=False)  # 'heading', 'content', 'bullet'
    heading_path = Column(Text, nullable=True)  # "* Top\n** Second\n*** Current"
    content = Column(Text, nullable=False)  # Actual text content
    start_line = Column(Integer, nullable=True)  # Line number in original file
    token_count = Column(Integer, nullable=False)  # Approximate tokens

    # pgvector embedding (voyage-3.5 uses 1024 dimensions)
    embedding = Column(Vector(1024), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return (
            f"<DocumentChunk(document_id={self.document_id}, "
            f"chunk={self.chunk_index}, tokens={self.token_count})>"
        )

    # Vector similarity search index (see _upgrade_vector_index)
    __table_args__ = (
        Index(
            VECTOR_INDEX,
            embedding,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class QueryFeedback(Base):
    """Capture per-query signals for the self-improvement retrospective."""

    __tablename__ = "query_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    query_id = Column(String(100), nullable=False, unique=True, index=True)
    user_message = Column(Text, nullable=False)

    # RAG signals
    had_rag_context = Column(Boolean, nullable=False, default=False)
    rag_context_chars = Column(Integer, nullable=False, default=0)

    # Tool usage signals
    search_tools_used = Column(JSON, nullable=False, default=list)  # list of tool names
    tool_error_count = Column(Integer, nullable=False, default=0)
    total_tool_calls = Column(Integer, nullable=False, default=0)
    api_call_count = Column(Integer, nullable=False, default=1)

    # Derived signals
    retrieval_miss = Column(
        Boolean, nullable=False, default=False
    )  # RAG present but Claude still searched
    user_followup_correction = Column(
        Boolean, nullable=False, default=False
    )  # detected dissatisfaction

    # Explicit feedback (for future use)
    explicit_feedback = Column(String(20), nullable=True)  # 'positive', 'negative', etc.
    feedback_note = Column(Text, nullable=True)

    # Processing state
    processed = Column(Boolean, nullable=False, default=False)
    processed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<QueryFeedback(query_id='{self.query_id}', miss={self.retrieval_miss}, "
            f"correction={self.user_followup_correction})>"
        )


class SessionNote(Base):
    """Per-session working memory notes (note_to_self tool)."""

    __tablename__ = "session_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    note = Column(Text, nullable=False)
    category = Column(
        String(30), nullable=False, default="other"
    )  # user_preference, discovery, strategy, correction, other
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<SessionNote(session='{self.session_id}', category='{self.category}')>"


class LearnedRule(Base):
    """Store learned patterns from retrospective analysis."""

    __tablename__ = "learned_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(
        String(30), nullable=False, index=True
    )  # 'retrieval', 'vocabulary', 'preference', 'embedding_gap', 'general'
    rule_text = Column(Text, nullable=False)  # human-readable rule
    rule_data = Column(JSON, nullable=True)  # structured data (e.g., term mappings)
    confidence = Column(Float, nullable=False, default=0.5)
    hit_count = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    source_query_ids = Column(JSON, nullable=True)  # list of query_ids that generated this
    expires_at = Column(DateTime, nullable=True)
    last_reinforced_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<LearnedRule(type='{self.rule_type}', "
            f"confidence={self.confidence:.2f}, active={self.is_active})>"
        )


class ScheduledTask(Base):
    """A task that runs on a cron or interval schedule via Claude."""

    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    prompt = Column(Text, nullable=False)
    schedule_type = Column(String(10), nullable=False)  # 'cron' or 'interval'
    schedule_expr = Column(String(100), nullable=False)  # "0 9 * * 1-5" or "4h"
    model = Column(String(100), nullable=True)  # null = scheduler role default
    tools_allowed = Column(JSON, nullable=True)  # null = all tools
    is_heartbeat = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    max_turns = Column(Integer, default=10)
    max_input_tokens = Column(Integer, default=200_000)
    max_output_tokens = Column(Integer, default=10_000)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(String(20), default="user")  # 'user', 'chat', or 'system'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<ScheduledTask(name='{self.name}', "
            f"schedule='{self.schedule_type}:{self.schedule_expr}', enabled={self.enabled})>"
        )


class ScheduledTaskRun(Base):
    """Log of a single execution of a scheduled task."""

    __tablename__ = "scheduled_task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), index=True)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20))  # 'running', 'completed', 'failed', 'budget_exceeded'
    turns_used = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)  # uncached input
    output_tokens = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<ScheduledTaskRun(task_id={self.task_id}, "
            f"status='{self.status}', turns={self.turns_used})>"
        )


class DailyTokenUsage(Base):
    """Aggregate daily token usage for scheduled task budget enforcement."""

    __tablename__ = "daily_token_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), unique=True, index=True)  # 'YYYY-MM-DD'
    input_tokens = Column(Integer, default=0)  # uncached input
    output_tokens = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    task_runs = Column(Integer, default=0)

    def __repr__(self):
        return f"<DailyTokenUsage(date='{self.date}', runs={self.task_runs})>"


class McpClientRegistration(Base):
    """Persist MCP OAuth client registrations across container restarts."""

    __tablename__ = "mcp_client_registrations"

    client_id = Column(String(255), primary_key=True)
    client_info_json = Column(Text, nullable=False)  # Serialized OAuthClientInformationFull
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<McpClientRegistration(client_id='{self.client_id}')>"


class NoteProposal(Base):
    """A proposed note-organization change awaiting user review (curation agent)."""

    __tablename__ = "note_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(20), nullable=False, index=True)  # 'add_links', 'new_page'
    title = Column(String(200), nullable=False)  # short human-readable label
    rationale = Column(Text, nullable=False)  # why the curator suggests this
    payload = Column(JSON, nullable=False)  # edits/page content — see curation/apply.py
    confidence = Column(Float, nullable=False, default=0.5)
    status = Column(String(20), nullable=False, default="pending", index=True)
    # 'pending', 'applied', 'rejected', 'stale'
    resolution_note = Column(Text, nullable=True)  # reject reason / modification note
    source = Column(String(20), nullable=False, default="curator")  # 'curator' or 'chat'
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<NoteProposal(id={self.id}, kind='{self.kind}', status='{self.status}')>"


class AgentRunLog(Base):
    """Lightweight log of self-improvement agent runs (for admin API)."""

    __tablename__ = "agent_run_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    trigger = Column(String(20), nullable=False)  # 'scheduled', 'manual'
    turns_used = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=False, default=0)  # uncached input
    output_tokens = Column(Integer, nullable=False, default=0)
    cache_write_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    actions_summary = Column(JSON, nullable=True)  # [{description}]
    summary = Column(Text, nullable=True)  # agent's own summary
    error = Column(Text, nullable=True)
    run_file = Column(String(100), nullable=True)  # path to .pkm/runs/YYYY-MM-DD-HHMM.md

    def __repr__(self):
        return f"<AgentRunLog(id={self.id}, trigger='{self.trigger}', turns={self.turns_used})>"


class OpsState(Base):
    """Small timestamped records for ops bookkeeping.

    Keys are namespaced: "alert:<condition>" holds when the watchdog last
    pushed that alert; "job:<name>" holds when a background job last finished.
    """

    __tablename__ = "ops_state"

    key = Column(String(200), primary_key=True)
    at = Column(DateTime, nullable=False)
    detail = Column(Text, nullable=True)

    def __repr__(self):
        return f"<OpsState(key='{self.key}', at='{self.at}')>"
