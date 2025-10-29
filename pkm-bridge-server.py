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
from flask import Flask, request, jsonify, render_template, send_from_directory
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

# Import tool components
from pkm_bridge.tools.registry import ToolRegistry
from pkm_bridge.tools.shell import ExecuteShellTool
from pkm_bridge.tools.files import ListFilesTool
from pkm_bridge.tools.search_notes import SearchNotesTool
from pkm_bridge.tools.journal import JournalNoteTool

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

# In-memory sessions
sessions: Dict[str, Dict[str, Any]] = {}

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
tool_registry.register(JournalNoteTool(logger, config.org_dir))

logger.info(f"Registered {len(tool_registry)} tools: {', '.join(tool_registry.list_tools())}")

# Initialize file editor
from pkm_bridge.file_editor import FileEditor
file_editor = FileEditor(logger, config.org_dir, config.logseq_dir)

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


# -------------------------
# Web Endpoints
# -------------------------

@app.route('/')
def index():
    """Serve the main web interface."""
    return render_template('index.html')


@app.route('/_astro/<path:filename>')
def serve_astro_assets(filename):
    """Serve Astro build assets (JS, CSS, etc)."""
    templates_dir = Path(app.template_folder)
    return send_from_directory(templates_dir / '_astro', filename)


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

    # Create session
    if session_id not in sessions:
        sessions[session_id] = {
            'history': [],
            'system_prompt': config.get_system_prompt()
        }
    session = sessions[session_id]

    # Append user message
    session['history'].append({
        "role": "user",
        "content": user_message
    })

    logger.info(f"User: {user_message[:100]}{'...' if len(user_message) > 100 else ''}")

    try:
        # Build beta headers
        beta_features = ["context-management-2025-06-27"]
        if thinking:
            beta_features.append("interleaved-thinking-2025-05-14")

        # Build API call parameters
        api_params = {
            "model": model,
            "max_tokens": 8192,
            "system": [
                {"type": "text", "text": session['system_prompt'], "cache_control": {"type": "ephemeral"}}
            ],
            "messages": session['history'],
            "tools": tool_registry.get_anthropic_tools(),
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
                        result = tool_registry.execute_tool(block.name, block.input)
                    # Log if tool result contains an error
                    if result.startswith("❌"):
                        logger.error(f"Tool {block.name} returned error: {result[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            session['history'].append({"role": "assistant", "content": response.content})
            session['history'].append({"role": "user", "content": tool_results})

            # Update API params with new history
            api_params["messages"] = session['history']

            api_call_count += 1
            with timer(f"Claude API call #{api_call_count} ({model})"):
                response = client.messages.create(**api_params)

        # Final text
        assistant_text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                assistant_text += block.text

        session['history'].append({"role": "assistant", "content": response.content})

        total_elapsed = time.time() - request_start
        logger.info(f"Assistant: {assistant_text[:400]}{'...' if len(assistant_text) > 200 else ''}")
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

    if session_id not in sessions:
        return jsonify([])

    history = []
    for msg in sessions[session_id]['history']:
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

    if session_id in sessions:
        del sessions[session_id]
        logger.info(f"Cleared session: {session_id}")
    return jsonify({"status": "ok"})


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


@app.route('/health', methods=['GET'])
def health():
    """Basic health info."""
    health_data = {
        "status": "ok",
        "org_dir": str(config.org_dir),
        "org_dir_exists": config.org_dir.exists(),
        "allowed_commands": sorted(config.allowed_commands),
        "tools": tool_registry.list_tools(),
    }
    if config.logseq_dir:
        health_data["logseq_dir"] = str(config.logseq_dir)
        health_data["logseq_dir_exists"] = config.logseq_dir.exists()
    else:
        health_data["logseq_dir"] = None
    return jsonify(health_data)


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

    # In production, use proper WSGI server (gunicorn, waitress, etc.)
    # Enable threaded mode to handle concurrent requests (e.g., context loading + user queries)
    app.run(host=config.host, port=config.port, debug=config.debug, threaded=True)
