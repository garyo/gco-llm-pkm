#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "anthropic>=0.39.0",
#   "flask>=3.0.0",
#   "python-dotenv>=1.0.0",
#   "pyyaml>=6.0.2",
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
from flask import Flask, request, jsonify, render_template

# Import configuration and logging
from config.settings import Config
from pkm_bridge.logging_config import setup_logging

# Import tool components
from pkm_bridge.tools.registry import ToolRegistry
from pkm_bridge.tools.shell import ExecuteShellTool
from pkm_bridge.tools.files import ListFilesTool
from pkm_bridge.tools.journal import JournalNoteTool
from pkm_bridge.tools.skills import SkillRegistry, LoadSkillTool, RunSkillTool

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

# In-memory sessions
sessions: Dict[str, Dict[str, Any]] = {}

# -------------------------
# Initialize Tools
# -------------------------

# Create skill registry
skill_registry = SkillRegistry(config.skills_dir, logger)

# Create tool registry and register all tools
tool_registry = ToolRegistry()

# Register tools (order doesn't matter, but logical grouping helps)
execute_shell_tool = ExecuteShellTool(
    logger, config.allowed_commands, config.org_dir, config.logseq_dir
)
tool_registry.register(execute_shell_tool)

tool_registry.register(ListFilesTool(logger, config.org_dir, config.logseq_dir))
tool_registry.register(JournalNoteTool(logger, config.org_dir))
tool_registry.register(LoadSkillTool(logger, skill_registry))
tool_registry.register(RunSkillTool(logger, skill_registry, execute_shell_tool))

logger.info(f"Registered {len(tool_registry)} tools: {', '.join(tool_registry.list_tools())}")

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


@app.route('/query', methods=['POST'])
def query():
    """Main query endpoint with tool-use loop."""
    request_start = time.time()

    data = request.json
    session_id = data.get('session_id', 'default')
    user_message = data['message']
    model = data.get('model', config.model)

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
        # Initial call
        with timer(f"Claude API call (initial, {model})"):
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=[
                    {"type": "text", "text": session['system_prompt'], "cache_control": {"type": "ephemeral"}}
                ],
                messages=session['history'],
                tools=tool_registry.get_anthropic_tools()
            )

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

            api_call_count += 1
            with timer(f"Claude API call #{api_call_count} ({model})"):
                response = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=[
                        {"type": "text", "text": session['system_prompt'], "cache_control": {"type": "ephemeral"}}
                    ],
                    messages=session['history'],
                    tools=tool_registry.get_anthropic_tools()
                )

        # Final text
        assistant_text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                assistant_text += block.text

        session['history'].append({"role": "assistant", "content": response.content})

        total_elapsed = time.time() - request_start
        logger.info(f"Assistant: {assistant_text[:200]}{'...' if len(assistant_text) > 200 else ''}")
        logger.info(f"Request completed in {total_elapsed:.3f}s ({api_call_count} API calls, {tool_call_count} tool calls)")

        return jsonify({"response": assistant_text, "session_id": session_id})

    except Exception as e:
        logger.error(f"Query error: {str(e)}", exc_info=True)
        return jsonify({"response": f"❌ Error: {str(e)}", "session_id": session_id}), 500


@app.route('/sessions/<session_id>/history', methods=['GET'])
def get_history(session_id):
    """Return a simplified text-only history for debugging UI."""
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
def clear_session(session_id):
    """Clear a conversation session."""
    if session_id in sessions:
        del sessions[session_id]
        logger.info(f"Cleared session: {session_id}")
    return jsonify({"status": "ok"})


@app.route('/skills/refresh', methods=['POST'])
def refresh_skills():
    """Re-scan skills/<name>/SKILL.md without restarting the server."""
    skill_registry.discover_skills()
    return jsonify({"skills": sorted(skill_registry.skills.keys())})


@app.route('/health', methods=['GET'])
def health():
    """Basic health info + local skills list."""
    health_data = {
        "status": "ok",
        "org_dir": str(config.org_dir),
        "org_dir_exists": config.org_dir.exists(),
        "skills_dir": str(config.skills_dir),
        "allowed_commands": sorted(config.allowed_commands),
        "local_skills": sorted(skill_registry.skills.keys()),
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
    logger.info(f"Skills dir: {config.skills_dir}")
    logger.info(f"Local skills: {', '.join(sorted(skill_registry.skills.keys())) or '(none)'}")
    logger.info(f"Allowed commands: {', '.join(sorted(config.allowed_commands))}")
    logger.info(f"Tools: {', '.join(tool_registry.list_tools())}")
    logger.info(f"Server starting at http://{config.host}:{config.port}")
    logger.info(f"Log level: {config.log_level}")
    logger.info("=" * 60)

    # In production, use proper WSGI server (gunicorn, waitress, etc.)
    app.run(host=config.host, port=config.port, debug=config.debug)
