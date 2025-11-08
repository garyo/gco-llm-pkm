#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "anthropic>=0.39.0",
#   "flask>=3.0.0",
#   "python-dotenv>=1.0.0",
#   "pyyaml>=6.0.2",
#   "flask-hot-reload>=0.3.0",
#   "pyjwt>=2.8.0",
#   "bcrypt>=4.1.0",
#   "flask-limiter>=3.5.0",
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
#   "alembic>=1.13.0",
#   "requests>=2.31.0",
#   "watchdog>=3.0.0",
# ]
# ///
"""
gco-pkm-llm Bridge Server

A modular server providing Claude API access to Personal Knowledge Management files.
"""

import time
from contextlib import contextmanager
from typing import Dict, Any

from anthropic import Anthropic
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
from pathlib import Path

# Optional: flask_hot_reload (dev only)
try:
    from flask_hot_reload import HotReload
    HOT_RELOAD_AVAILABLE = True
except ImportError:
    HOT_RELOAD_AVAILABLE = False

# Import configuration and logging
from config.settings import Config
from pkm_bridge.logging_config import setup_logging

# Import auth
from pkm_bridge.auth import AuthManager

# Import database
from pkm_bridge.database import init_db, get_db
from pkm_bridge.db_repository import SessionRepository

# Import tool components
from pkm_bridge.tools.registry import ToolRegistry
from pkm_bridge.tools.shell import ExecuteShellTool
from pkm_bridge.tools.files import ListFilesTool
from pkm_bridge.tools.search_notes import SearchNotesTool
from pkm_bridge.tools.ticktick import TickTickTool
from pkm_bridge.tools.open_file import OpenFileTool
from pkm_bridge.tools.find_context import FindContextTool

# Import database components
from pkm_bridge.database import init_db, get_db
from pkm_bridge.db_repository import OAuthRepository, UserSettingsRepository

# Import TickTick components
from pkm_bridge.ticktick_oauth import TickTickOAuth
from pkm_bridge.ticktick_client import TickTickClient

# Import SSE event manager
from pkm_bridge.events import event_manager

# -------------------------
# Setup & Configuration
# -------------------------

# Load configuration
config = Config()

# Setup logging
logger = setup_logging(config.log_level)

# Initialize Anthropic client
client = Anthropic(api_key=config.anthropic_api_key)

# Flask app
app = Flask(__name__)

# Enable browser hot-reload in debug mode (if available)
if config.debug and HOT_RELOAD_AVAILABLE:
    HotReload(app, includes=['templates', 'static'])
    logger.info("Hot reload enabled")

# Initialize database
try:
    init_db()
    logger.info("Database initialized")
except Exception as e:
    logger.warning(f"Database initialization failed (will retry on use): {e}")

# Initialize TickTick OAuth handler (optional - only if configured)
ticktick_oauth = None
try:
    ticktick_oauth = TickTickOAuth()
    logger.info("TickTick OAuth handler initialized")
except ValueError as e:
    logger.info(f"TickTick not configured: {e}")

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per hour"],  # Global default
    storage_uri="memory://",
)

# Initialize auth manager if enabled
auth_manager = None
if config.auth_enabled:
    auth_manager = AuthManager(
        secret_key=config.jwt_secret,
        password_hash=config.password_hash,
        token_expiry_hours=config.token_expiry_hours,
        logger=logger
    )
    logger.info("Authentication enabled with rate limiting")

# -------------------------
# Initialize Tools
# -------------------------

# Create tool registry and register all tools
tool_registry = ToolRegistry()

# Register tools
execute_shell_tool = ExecuteShellTool(
    logger, config.allowed_commands, config.org_dir, config.logseq_dir
)
tool_registry.register(execute_shell_tool)
tool_registry.register(ListFilesTool(logger, config.org_dir, config.logseq_dir))
tool_registry.register(SearchNotesTool(logger, config.org_dir, config.logseq_dir))
tool_registry.register(FindContextTool(logger, config.org_dir, config.logseq_dir))
tool_registry.register(OpenFileTool(logger, config.org_dir, config.logseq_dir))

# Register TickTick tool if configured
if ticktick_oauth:
    tool_registry.register(TickTickTool(logger, ticktick_oauth))
    logger.info("TickTick tool registered")

logger.info(f"Registered {len(tool_registry)} tools: {', '.join(tool_registry.list_tools())}")

# Initialize file editor
from pkm_bridge.file_editor import FileEditor
file_editor = FileEditor(logger, config.org_dir, config.logseq_dir)

# Initialize history manager for conversation truncation
from pkm_bridge.history_manager import HistoryManager
history_manager = HistoryManager(
    max_tokens=100000,  # ~$0.10 per request with Haiku
    keep_recent_turns=10  # Always keep last 10 conversation turns
)

# -------------------------
# Utilities
# -------------------------

@contextmanager
def timer(label: str):
    """Context manager for timing operations."""
    start = time.time()
    try:
        yield
    finally:
        logger.debug(f"{label}: {time.time() - start:.3f}s")


def serialize_message_content(content):
    """Convert Anthropic message content to JSON-serializable format."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        serialized = []
        for item in content:
            if hasattr(item, 'model_dump'):
                # Anthropic SDK objects have model_dump()
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(item)
            else:
                # Fallback: convert to dict manually
                serialized.append({"type": getattr(item, "type", "unknown"), "data": str(item)})
        return serialized
    else:
        return str(content)


# -------------------------
# Web Endpoints
# -------------------------

@app.route('/')
def index():
    """Serve the main web interface."""
    return render_template('index.html')


@app.route('/settings')
def settings():
    """Serve the settings page."""
    return render_template('settings.html')


@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files from templates directory (Astro build output).

    This catches all paths not matched by other routes and serves them
    from the templates/ directory. HTML files are excluded to ensure
    they go through render_template() for proper handling.
    """
    # Don't serve .html files this way - they should use render_template()
    if filename.endswith('.html'):
        return "Not found", 404

    templates_dir = Path(app.template_folder)
    file_path = templates_dir / filename

    # Security: ensure the file is actually within templates directory
    try:
        file_path.resolve().relative_to(templates_dir.resolve())
    except ValueError:
        return "Not found", 404

    if not file_path.exists() or not file_path.is_file():
        return "Not found", 404

    return send_from_directory(templates_dir, filename)


@app.route('/login', methods=['POST'])
@limiter.limit("5 per minute")  # Strict rate limit on login attempts
def login():
    """Authenticate user and return JWT token.

    Request body:
        {"password": "user-password"}

    Response:
        {"token": "jwt-token-string", "expires_in": hours}
        or {"error": "message"} with 401 status
    """
    client_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')

    if not config.auth_enabled:
        logger.warning(f"Login attempted from {client_ip} but auth is disabled")
        return jsonify({"error": "Authentication is disabled"}), 400

    data = request.json
    password = data.get('password', '')

    if not password:
        logger.warning(f"Login attempt from {client_ip} with empty password")
        return jsonify({"error": "Password is required"}), 400

    logger.info(f"Login attempt from {client_ip} (User-Agent: {user_agent[:50]}...)")

    if auth_manager.verify_password(password):
        token = auth_manager.generate_token()
        logger.info(f"✅ Successful login from {client_ip}")
        return jsonify({
            "token": token,
            "expires_in": config.token_expiry_hours
        })
    else:
        logger.warning(f"❌ Failed login attempt from {client_ip} - invalid password")
        return jsonify({"error": "Invalid password"}), 401


@app.route('/verify-token', methods=['POST'])
@limiter.limit("30 per minute")  # Allow reasonable token verification rate
def verify_token():
    """Verify if a token is still valid.

    Request body:
        {"token": "jwt-token-string"}

    Response:
        {"valid": true} or {"valid": false}
    """
    if not config.auth_enabled:
        return jsonify({"valid": True})  # No auth = always valid

    data = request.json
    token = data.get('token', '')

    if not token:
        logger.debug(f"Token verification from {request.remote_addr}: no token provided")
        return jsonify({"valid": False})

    payload = auth_manager.verify_token(token)
    is_valid = payload is not None

    if is_valid:
        logger.debug(f"Token verification from {request.remote_addr}: valid")
    else:
        logger.info(f"Token verification from {request.remote_addr}: invalid/expired")

    return jsonify({"valid": is_valid})


@app.route('/query', methods=['POST'])
@limiter.limit("60 per minute")  # Reasonable limit for queries
def query():
    """Main query endpoint with tool-use loop."""
    # Check auth if enabled
    if config.auth_enabled:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logger.warning(f"Unauthorized query attempt from {request.remote_addr}: missing auth header")
            return jsonify({"error": "Missing or invalid authorization header"}), 401
        token = auth_header[7:]
        if not auth_manager.verify_token(token):
            logger.warning(f"Unauthorized query attempt from {request.remote_addr}: invalid token")
            return jsonify({"error": "Invalid or expired token"}), 401

    request_start = time.time()

    data = request.json
    session_id = data.get('session_id', 'default')
    user_message = data['message']
    model = data.get('model', config.model)
    thinking = data.get('thinking')
    user_timezone = data.get('timezone')  # Optional timezone from client

    # Get or create session from database
    db = get_db()
    try:
        # Load user context from database for system prompt
        user_context = UserSettingsRepository.get_user_context(db, user_id='default')

        # Get system prompt blocks for optimal caching
        # Block 1: Static instructions (cached)
        # Block 2: User context (cached - separate so edits don't invalidate base)
        # Block 3: Date (NOT cached - appended dynamically, changes daily)
        system_prompt_blocks = config.get_system_prompt_blocks(
            user_context=user_context,
            user_timezone=user_timezone
        )

        # Debug: log system block structure
        if logger.level <= 10:  # DEBUG level
            for i, block in enumerate(system_prompt_blocks, 1):
                cached = "✓ CACHED" if "cache_control" in block else "✗ not cached"
                logger.debug(f"  System block {i}: {len(block['text'])} chars, {cached}")

        # Also get flat version for session storage
        system_prompt_flat = config.get_system_prompt(
            user_context=user_context,
            user_timezone=user_timezone
        )

        db_session = SessionRepository.get_or_create_session(
            db, session_id, system_prompt=system_prompt_flat
        )
        history = db_session.history if db_session.history else []
    finally:
        db.close()

    # Append user message
    history.append({
        "role": "user",
        "content": user_message
    })

    # Truncate history to stay within budget before sending to API
    # Log history stats before truncation
    stats_before = history_manager.get_history_stats(history)
    if stats_before['total_tokens'] > 50000:  # Only log if potentially concerning
        logger.info(f"History before truncation: {stats_before['budget_usage']}")

    history = history_manager.truncate_history(history)

    # Log if we truncated
    stats_after = history_manager.get_history_stats(history)
    if stats_after['total_tokens'] < stats_before['total_tokens']:
        saved = stats_before['total_tokens'] - stats_after['total_tokens']
        logger.info(f"✂️  Truncated history: saved {saved} tokens ({stats_after['budget_usage']})")

    logger.info(f"User: {user_message[:100]}{'...' if len(user_message) > 100 else ''}")

    try:
        # Build beta headers - include prompt caching for cost optimization
        beta_features = ["prompt-caching-2024-07-31"]
        if thinking:
            beta_features.append("interleaved-thinking-2025-05-14")

        # Get tools with caching enabled
        tools = tool_registry.get_anthropic_tools()

        # Mark the last tool for caching (tools are relatively static)
        if tools:
            tools[-1]["cache_control"] = {"type": "ephemeral"}

        # Build API call parameters
        # System prompt is structured as blocks for optimal caching:
        # - Block 1: Static instructions (cached)
        # - Block 2: User context (cached)
        # - Block 3: Today's date (NOT cached - changes daily)
        # Tools are also cached as they rarely change
        api_params = {
            "model": model,
            "max_tokens": 8192,
            "system": system_prompt_blocks,
            "messages": history,
            "tools": tools,
            "extra_headers": {
                "anthropic-beta": ",".join(beta_features)
            }
        }

        # Add thinking parameter if enabled
        if thinking:
            api_params["thinking"] = thinking

        # Initial call
        with timer(f"Claude API call (initial, {model})"):
            response = client.messages.create(**api_params)

        api_call_count = 1
        tool_call_count = 0

        # Tool loop
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    tool_call_count += 1
                    logger.info(f"Tool call: {block.name} with params: {block.input}")
                    with timer(f"Tool execution: {block.name}"):
                        # Pass session_id in context for tools that need it
                        context = {"session_id": session_id}
                        result = tool_registry.execute_tool(block.name, block.input, context=context)
                    # Log if tool result contains an error
                    if result.startswith("❌"):
                        logger.error(f"Tool {block.name} returned error: {result[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            history.append({"role": "assistant", "content": serialize_message_content(response.content)})
            history.append({"role": "user", "content": tool_results})

            # Update API params with new history
            api_params["messages"] = history

            api_call_count += 1
            with timer(f"Claude API call #{api_call_count} ({model})"):
                response = client.messages.create(**api_params)

        # Final text
        assistant_text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                assistant_text += block.text

        history.append({"role": "assistant", "content": serialize_message_content(response.content)})

        # Save updated history to database
        db = get_db()
        try:
            SessionRepository.update_history(db, session_id, history)
        finally:
            db.close()

        total_elapsed = time.time() - request_start
        logger.info(f"Assistant: {assistant_text[:400]}{'...' if len(assistant_text) > 200 else ''}")

        # Log cache performance if available
        usage = response.usage
        if hasattr(usage, 'cache_creation_input_tokens') or hasattr(usage, 'cache_read_input_tokens'):
            cache_write = getattr(usage, 'cache_creation_input_tokens', 0)
            cache_read = getattr(usage, 'cache_read_input_tokens', 0)
            input_tokens = getattr(usage, 'input_tokens', 0)

            # Calculate breakdown
            history_turns = len([m for m in history if m.get('role') in ['user', 'assistant']])
            system_blocks = len(system_prompt_blocks)

            # Estimate cost (Haiku-4.5 rates: $0.80/M input, $0.08/M cached, $4/M output)
            output_tokens = getattr(usage, 'output_tokens', 0)
            cost_input = (input_tokens * 0.80) / 1_000_000
            cost_cache_write = (cache_write * 1.00) / 1_000_000  # Cache writes cost more
            cost_cache_read = (cache_read * 0.08) / 1_000_000
            cost_output = (output_tokens * 4.00) / 1_000_000
            total_cost = cost_input + cost_cache_write + cost_cache_read + cost_output

            logger.info(f"Token usage: {input_tokens} input, {cache_write} cache write, {cache_read} cache read, {output_tokens} output")
            logger.info(f"  Breakdown: {history_turns} conversation turns, {system_blocks} system blocks, {len(tools)} tools")
            logger.info(f"  Estimated cost: ${total_cost:.4f} (${cost_input:.4f} input + ${cost_cache_read:.4f} cached + ${cost_output:.4f} output)")

            # Warn if conversation is getting large
            uncached_input = input_tokens - cache_read
            if uncached_input > 20000:
                logger.warning(f"⚠️  Large uncached input ({uncached_input} tokens) - likely long conversation history")

        logger.info(f"Request completed in {total_elapsed:.3f}s ({api_call_count} API calls, {tool_call_count} tool calls)")

        return jsonify({"response": assistant_text, "session_id": session_id})

    except Exception as e:
        logger.error(f"Query error: {str(e)}", exc_info=True)
        return jsonify({"response": f"❌ Error: {str(e)}", "session_id": session_id}), 500


@app.route('/sessions/<session_id>/history', methods=['GET'])
@limiter.limit("30 per minute")
def get_history(session_id):
    """Return a simplified text-only history for debugging UI."""
    # Check auth if enabled
    if config.auth_enabled:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logger.warning(f"Unauthorized history access from {request.remote_addr}")
            return jsonify({"error": "Missing or invalid authorization header"}), 401
        token = auth_header[7:]
        if not auth_manager.verify_token(token):
            logger.warning(f"Unauthorized history access from {request.remote_addr}: invalid token")
            return jsonify({"error": "Invalid or expired token"}), 401

    # Get session from database
    db = get_db()
    try:
        db_session = SessionRepository.get_session(db, session_id)
        if not db_session:
            return jsonify([])

        history = []
        for msg in db_session.history:
            if msg['role'] in ['user', 'assistant']:
                if isinstance(msg['content'], str):
                    history.append({"role": msg['role'], "text": msg['content']})
                elif isinstance(msg['content'], list):
                    text = ""
                    for item in msg['content']:
                        if hasattr(item, 'text'):
                            text += item.text
                        elif isinstance(item, dict) and 'text' in item:
                            text += item['text']
                    if text:
                        history.append({"role": msg['role'], "text": text})

        return jsonify(history)
    finally:
        db.close()


@app.route('/sessions', methods=['GET'])
@limiter.limit("30 per minute")
def list_sessions():
    """List all conversation sessions for the user."""
    # Check auth if enabled
    if config.auth_enabled:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logger.warning(f"Unauthorized sessions list from {request.remote_addr}")
            return jsonify({"error": "Missing or invalid authorization header"}), 401
        token = auth_header[7:]
        if not auth_manager.verify_token(token):
            logger.warning(f"Unauthorized sessions list from {request.remote_addr}: invalid token")
            return jsonify({"error": "Invalid or expired token"}), 401

    # Get all sessions from database
    db = get_db()
    try:
        db_sessions = SessionRepository.get_all_sessions(db, user_id='default')

        sessions_list = []
        for session in db_sessions:
            # Get first user message as preview
            preview = ""
            for msg in session.history:
                if msg.get('role') == 'user':
                    content = msg.get('content', '')
                    if isinstance(content, str):
                        preview = content[:100]
                    break

            sessions_list.append({
                "session_id": session.session_id,
                "created_at": session.created_at.isoformat() + 'Z',  # Mark as UTC
                "updated_at": session.updated_at.isoformat() + 'Z',  # Mark as UTC
                "message_count": len(session.history),
                "preview": preview
            })

        return jsonify(sessions_list)
    finally:
        db.close()


@app.route('/sessions/<session_id>', methods=['DELETE'])
@limiter.limit("10 per minute")
def clear_session(session_id):
    """Clear a conversation session."""
    # Check auth if enabled
    if config.auth_enabled:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logger.warning(f"Unauthorized session delete from {request.remote_addr}")
            return jsonify({"error": "Missing or invalid authorization header"}), 401
        token = auth_header[7:]
        if not auth_manager.verify_token(token):
            logger.warning(f"Unauthorized session delete from {request.remote_addr}: invalid token")
            return jsonify({"error": "Invalid or expired token"}), 401

    # Delete session from database
    db = get_db()
    try:
        deleted = SessionRepository.delete_session(db, session_id)
        if deleted:
            logger.info(f"Cleared session: {session_id}")
        return jsonify({"status": "ok"})
    finally:
        db.close()


@app.route('/assets/<path:filepath>', methods=['GET'])
@limiter.limit("100 per minute")
def serve_asset(filepath):
    """Serve image and asset files from ORG_DIR or LOGSEQ_DIR.

    This endpoint allows the frontend to display images referenced in org-mode and Logseq files.
    Searches for assets in the following locations (in order):
    1. ORG_DIR/{filepath}
    2. ORG_DIR/assets/{filepath}
    3. LOGSEQ_DIR/{filepath}
    4. LOGSEQ_DIR/Personal/assets/{filepath}
    5. LOGSEQ_DIR/DSS/assets/{filepath}

    Includes path traversal protection to prevent accessing files outside allowed directories.

    Args:
        filepath: Relative path to the asset file

    Returns:
        The requested file or 404 if not found/invalid
    """
    # Check auth if enabled (accept token from header or query parameter)
    if config.auth_enabled:
        token = None

        # Try Authorization header first
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

        # Fall back to query parameter (for browser <img> tags)
        if not token:
            token = request.args.get('token', '')

        if not token:
            logger.warning(f"Unauthorized asset access from {request.remote_addr}: no token provided")
            return jsonify({"error": "Missing authorization token"}), 401

        if not auth_manager.verify_token(token):
            logger.warning(f"Unauthorized asset access from {request.remote_addr}: invalid token")
            return jsonify({"error": "Invalid or expired token"}), 401

    # Security: Validate and resolve the path to prevent directory traversal
    try:
        # Try multiple locations for the asset
        search_paths = [
            config.org_dir / filepath,
            config.org_dir / "assets" / filepath,  # Org-mode assets subdirectory
        ]

        # Add Logseq paths if configured
        if config.logseq_dir:
            search_paths.extend([
                config.logseq_dir / filepath,
                config.logseq_dir / "Personal" / "assets" / filepath,
                config.logseq_dir / "DSS" / "assets" / filepath,
            ])

        found_path = None
        allowed_roots = [config.org_dir.resolve()]
        if config.logseq_dir:
            allowed_roots.append(config.logseq_dir.resolve())

        for candidate_path in search_paths:
            try:
                resolved_path = candidate_path.resolve()

                # Check if path is within allowed directories
                is_allowed = any(
                    str(resolved_path).startswith(str(root))
                    for root in allowed_roots
                )

                if not is_allowed:
                    continue

                # Check if file exists
                if resolved_path.is_file():
                    found_path = resolved_path
                    break

            except (ValueError, OSError):
                # Skip invalid paths
                continue

        if not found_path:
            logger.debug(f"Asset not found: {filepath}")
            return jsonify({"error": "File not found"}), 404

        # Final security check: ensure resolved path is within allowed directories
        is_safe = any(
            str(found_path).startswith(str(root))
            for root in allowed_roots
        )

        if not is_safe:
            logger.warning(f"Path traversal attempt from {request.remote_addr}: {filepath}")
            return jsonify({"error": "Invalid file path"}), 403

        # Get the directory and filename
        directory = found_path.parent
        filename = found_path.name

        logger.debug(f"Serving asset: {filepath} from {found_path}")
        return send_from_directory(directory, filename)

    except Exception as e:
        logger.error(f"Error serving asset {filepath}: {str(e)}")
        return jsonify({"error": "Error serving file"}), 500


# -------------------------
# User Settings Endpoints
# -------------------------

@app.route('/api/user-context', methods=['GET'])
@auth_manager.require_auth
@limiter.limit("30 per minute")
def get_user_context():
    """Get user context for the current user.

    Returns:
        JSON with user_context string
    """
    try:
        db = get_db()
        try:
            context = UserSettingsRepository.get_user_context(db, user_id='default')

            # If no context in DB, try to load from file as fallback
            if context is None:
                user_context_file = Path(__file__).parent / "config" / "user_context.txt"
                if user_context_file.exists():
                    context = user_context_file.read_text(encoding="utf-8")
                    logger.info("Loaded user context from file (migration needed)")
                else:
                    context = ""

            return jsonify({"user_context": context})
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error getting user context: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/user-context', methods=['PUT'])
@auth_manager.require_auth
@limiter.limit("10 per minute")
def update_user_context():
    """Update user context for the current user.

    Request body:
        {"user_context": "context text here"}

    Returns:
        JSON with status and updated_at timestamp
    """
    try:
        data = request.json
        if data is None or 'user_context' not in data:
            return jsonify({"error": "Missing 'user_context' in request body"}), 400

        context = data['user_context']

        # Basic validation
        if not isinstance(context, str):
            return jsonify({"error": "'user_context' must be a string"}), 400

        db = get_db()
        try:
            settings = UserSettingsRepository.save_user_context(db, context, user_id='default')
            logger.info(f"User context updated ({len(context)} chars)")

            return jsonify({
                "status": "success",
                "user_context": settings.user_context,
                "updated_at": settings.updated_at.isoformat() + 'Z'
            })
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error updating user context: {str(e)}")
        return jsonify({"error": str(e)}), 500


# -------------------------
# File Editor Endpoints
# -------------------------

@app.route('/api/files', methods=['GET'])
@auth_manager.require_auth
@limiter.limit("60 per minute")
def list_files():
    """List all editable files (.org and .md) in PKM directories.

    Returns:
        JSON list of files with path, name, dir, modified timestamp
    """
    try:
        files = file_editor.list_files()
        return jsonify(files)
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/file/<path:filepath>', methods=['GET'])
@auth_manager.require_auth
@limiter.limit("60 per minute")
def get_file(filepath):
    """Read file content.

    Args:
        filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"

    Returns:
        JSON with content, path, modified timestamp
    """
    try:
        file_data = file_editor.read_file(filepath)
        return jsonify(file_data)
    except ValueError as e:
        logger.warning(f"Invalid file path requested: {filepath} - {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/file/<path:filepath>', methods=['PUT'])
@auth_manager.require_auth
@limiter.limit("60 per minute")
def save_file(filepath):
    """Save file content.

    Args:
        filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"

    Request body:
        {"content": "file content here"}

    Returns:
        JSON with status, path, modified timestamp
    """
    try:
        data = request.json
        if not data or 'content' not in data:
            return jsonify({"error": "Missing 'content' in request body"}), 400

        result = file_editor.write_file(filepath, data['content'])
        return jsonify(result)
    except ValueError as e:
        logger.warning(f"Invalid file path for save: {filepath} - {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving file {filepath}: {str(e)}")
        return jsonify({"error": str(e)}), 500


# -------------------------
# TickTick OAuth Routes
# -------------------------

@app.route('/auth/ticktick/authorize', methods=['GET'])
def ticktick_authorize():
    """Initiate TickTick OAuth flow.

    This endpoint doesn't require authentication since it's part of the initial
    setup flow. The actual authorization happens on TickTick's servers.
    Redirects user to TickTick's authorization page.
    """
    if not ticktick_oauth:
        return jsonify({"error": "TickTick not configured"}), 503

    try:
        auth_data = ticktick_oauth.get_authorization_url()
        # Redirect user to TickTick authorization page
        return redirect(auth_data['url'])
    except Exception as e:
        logger.error(f"Error initiating TickTick OAuth: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/auth/ticktick/callback', methods=['GET'])
def ticktick_callback():
    """Handle TickTick OAuth callback."""
    if not ticktick_oauth:
        return jsonify({"error": "TickTick not configured"}), 503

    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error:
        logger.error(f"TickTick OAuth error: {error}")
        return jsonify({"error": error}), 400

    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    try:
        # Exchange code for tokens
        token_data = ticktick_oauth.exchange_code(code)

        # Store tokens in database
        db = get_db()
        OAuthRepository.save_token(
            db=db,
            service='ticktick',
            access_token=token_data['access_token'],
            refresh_token=token_data.get('refresh_token'),
            expires_at=token_data['expires_at'],
            scope=token_data.get('scope')
        )
        db.close()

        logger.info("TickTick OAuth completed successfully")
        return """
        <html>
            <head>
                <title>TickTick Connected</title>
                <meta http-equiv="refresh" content="3;url=/" />
            </head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1 style="color: #4CAF50;">✓ TickTick Connected Successfully!</h1>
                <p>Claude can now access your TickTick tasks.</p>
                <p>Redirecting to home page... <a href="/">Click here</a> if not redirected.</p>
            </body>
        </html>
        """

    except Exception as e:
        logger.error(f"Error completing TickTick OAuth: {e}")
        return f"""
        <html>
            <head><title>TickTick Connection Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1 style="color: #f44336;">✗ Connection Failed</h1>
                <p>Error: {str(e)}</p>
                <p><a href="/auth/ticktick/authorize">Try again</a></p>
            </body>
        </html>
        """, 500


@app.route('/auth/ticktick/status', methods=['GET'])
def ticktick_status():
    """Check TickTick connection status."""
    if auth_manager:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not auth_manager.verify_token(token):
            return jsonify({"error": "Invalid token"}), 401

    try:
        db = get_db()
        token = OAuthRepository.get_token(db, 'ticktick')
        db.close()

        if token:
            is_expired = OAuthRepository.is_token_expired(token)
            return jsonify({
                "connected": True,
                "expired": is_expired,
                "expires_at": token.expires_at.isoformat() if token.expires_at else None
            })
        else:
            return jsonify({"connected": False})

    except Exception as e:
        logger.error(f"Error checking TickTick status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/auth/ticktick/disconnect', methods=['POST'])
def ticktick_disconnect():
    """Disconnect TickTick."""
    if auth_manager:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not auth_manager.verify_token(token):
            return jsonify({"error": "Invalid token"}), 401

    try:
        db = get_db()
        deleted = OAuthRepository.delete_token(db, 'ticktick')
        db.close()

        if deleted:
            logger.info("TickTick disconnected")
            return jsonify({"status": "success", "message": "TickTick disconnected"})
        else:
            return jsonify({"error": "TickTick not connected"}), 404

    except Exception as e:
        logger.error(f"Error disconnecting TickTick: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check with database connectivity test."""
    health_data = {
        "status": "ok",
        "org_dir": str(config.org_dir),
        "org_dir_exists": config.org_dir.exists(),
        "allowed_commands": sorted(config.allowed_commands),
        "tools": tool_registry.list_tools(),
        "ticktick_configured": ticktick_oauth is not None,
    }

    # Test database connectivity
    try:
        db = get_db()
        # Try a simple query to verify connection works
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        health_data["database"] = "connected"
        db.close()
    except Exception as e:
        health_data["database"] = "error"
        health_data["database_error"] = str(e)
        health_data["status"] = "degraded"

    if config.logseq_dir:
        health_data["logseq_dir"] = str(config.logseq_dir)
        health_data["logseq_dir_exists"] = config.logseq_dir.exists()
    else:
        health_data["logseq_dir"] = None
    return jsonify(health_data)


@app.route('/api/events')
def sse_events():
    """Server-Sent Events endpoint for real-time notifications."""
    import json
    import queue
    from flask import request

    # Get session_id from query parameter
    session_id = request.args.get('session_id', None)

    def event_stream():
        """Generator for SSE events."""
        client_queue = event_manager.add_client(session_id=session_id)
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected', 'data': {}, 'timestamp': int(time.time())})}\n\n"

            # Stream events from queue
            while True:
                try:
                    # Wait for messages with timeout to allow checking connection
                    message = client_queue.get(timeout=30)
                    yield f"data: {json.dumps(message)}\n\n"
                except queue.Empty:
                    # Send keepalive comment every 30 seconds
                    yield ": keepalive\n\n"
        except GeneratorExit:
            # Client disconnected, clean up silently
            pass
        finally:
            event_manager.remove_client(client_queue)

    return app.response_class(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Connection': 'keep-alive'
        }
    )


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("gco-pkm-llm Bridge Server")
    logger.info("=" * 60)
    logger.info(f"Model: {config.model}")
    logger.info(f"Org files (primary): {config.org_dir}")
    if config.logseq_dir:
        logger.info(f"Logseq notes (archival): {config.logseq_dir}")
    logger.info(f"Allowed commands: {', '.join(sorted(config.allowed_commands))}")
    logger.info(f"Tools: {', '.join(tool_registry.list_tools())}")
    logger.info(f"Server starting at http://{config.host}:{config.port}")
    logger.info(f"Log level: {config.log_level}")
    if config.debug:
        logger.info("Browser hot-reload enabled")
    logger.info("=" * 60)

    # Start file watcher for SSE notifications
    watch_dirs = [config.org_dir]
    if config.logseq_dir:
        watch_dirs.append(config.logseq_dir)
    event_manager.start_file_watcher(watch_dirs)
    logger.info(f"File watcher started for {len(watch_dirs)} directories")

    # In production, use proper WSGI server (gunicorn, waitress, etc.)
    # Enable threaded mode to handle concurrent requests (e.g., context loading + user queries)
    try:
        app.run(host=config.host, port=config.port, debug=config.debug, threaded=True)
    finally:
        # Clean up file watcher on shutdown
        event_manager.stop_file_watcher()
        logger.info("File watcher stopped")
