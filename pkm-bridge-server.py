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

- Pseudo-skills live in: skills/<name>/SKILL.md  (YAML front-matter optional; recommended)
- The model can:
    * load_skill(name): read front-matter + body to follow procedures
    * run_skill(name, vars, template?): render {var} placeholders and execute locally
"""

import os
import re
import subprocess
import time
import logging
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from contextlib import contextmanager

import yaml
from anthropic import Anthropic
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

# -------------------------
# Setup & Configuration
# -------------------------

load_dotenv()

# Configure logging (with emoji indicators)
class EmojiFormatter(logging.Formatter):
    """Custom formatter that adds emoji indicators to log levels."""

    EMOJI_MAP = {
        logging.DEBUG: '🔍',
        logging.INFO: 'ℹ️ ',
        logging.WARNING: '⚠️ ',
        logging.ERROR: '❌',
        logging.CRITICAL: '🔥'
    }

    def format(self, record):
        emoji = self.EMOJI_MAP.get(record.levelno, '')
        record.emoji = emoji
        return super().format(record)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
handler = logging.StreamHandler()
handler.setFormatter(EmojiFormatter(
    fmt='%(asctime)s %(emoji)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.addHandler(handler)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

MODEL = os.getenv("MODEL", "claude-haiku-4-5")

ORG_DIR = Path(os.getenv("ORG_DIR", "~/Documents/org-agenda")).expanduser()
if not ORG_DIR.exists():
    raise ValueError(f"ORG_DIR does not exist: {ORG_DIR}")

LOGSEQ_DIR = Path(os.getenv("LOGSEQ_DIR", "~/Logseq Notes")).expanduser()
if os.getenv("LOGSEQ_DIR") and not LOGSEQ_DIR.exists():
    logger.warning(f"LOGSEQ_DIR does not exist: {LOGSEQ_DIR}")
    LOGSEQ_DIR = None
elif not LOGSEQ_DIR.exists():
    LOGSEQ_DIR = None

SKILLS_DIR = Path(os.getenv("SKILLS_DIR", "./skills")).expanduser()
SKILLS_DIR.mkdir(exist_ok=True)

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")

# See .env which can override this
ALLOWED_COMMANDS = set(
    os.getenv("ALLOWED_COMMANDS", "date,rg,ripgrep,grep,fd,find,cat,ls,emacs,git,sed").split(",")
)

# Flask + Anthropic client
app = Flask(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory sessions
sessions: Dict[str, Dict[str, Any]] = {}

# Local skills registry: name -> {"path": str, "frontmatter": dict, "body": str}
LOCAL_SKILLS: Dict[str, Dict[str, Any]] = {}

# -------------------------
# Utilities
# -------------------------

@contextmanager
def timer(label: str):
    start = time.time()
    try:
        yield
    finally:
        logger.debug(f"{label}: {time.time() - start:.3f}s")


def _split_front_matter(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Extract YAML front matter from markdown if present; return (front_matter, body)."""
    t = text.lstrip()
    if not t.startswith('---'):
        return None, text
    head, sep, rest = t[3:].partition('\n---')
    if not sep:
        return None, text
    try:
        fm = yaml.safe_load(head) or {}
    except Exception:
        fm = None
    body = rest.lstrip('\n')
    return fm, body


def _discover_local_skills(skills_root: Path) -> Dict[str, Dict[str, Any]]:
    """Discover skills/<name>/SKILL.md files and load their metadata and body."""
    registry: Dict[str, Dict[str, Any]] = {}
    for skill_file in skills_root.glob("*/SKILL.md"):
        try:
            name = skill_file.parent.name
            raw = skill_file.read_text(encoding="utf-8", errors="ignore")
            fm, body = _split_front_matter(raw)
            registry[name] = {
                "path": str(skill_file),
                "frontmatter": fm or {},
                "body": (body if fm else raw).strip()
            }
            logger.info(f'Discovered skill "{name}": {registry[name]["frontmatter"]}')
        except Exception as e:
            logger.warning(f"Failed to load skill {skill_file}: {e}")
    return registry


def _resolve_skill(name: str) -> Optional[Dict[str, Any]]:
    """Resolve a skill by exact or case-insensitive name."""
    if name in LOCAL_SKILLS:
        return LOCAL_SKILLS[name]
    lname = name.lower()
    for k, v in LOCAL_SKILLS.items():
        if k.lower() == lname:
            return v
    return None


def _render_template(tpl: str, vars: Dict[str, Any]) -> str:
    """Simple {var} placeholder replacement; no code execution."""
    return re.sub(r"\{([a-zA-Z0-9_]+)\}", lambda m: str(vars.get(m.group(1), m.group(0))), tpl)


# -------------------------
# Initialize Skills (before Flask fork)
# -------------------------

# Discover skills at module load time so Flask reloader child process has them
logger.info(f"Discovering skills in {SKILLS_DIR}...")
LOCAL_SKILLS = _discover_local_skills(SKILLS_DIR)
logger.info(f"Found {len(LOCAL_SKILLS)} skills: {', '.join(sorted(LOCAL_SKILLS.keys())) or '(none)'}")

# -------------------------
# Prompts & Tools
# -------------------------

def get_system_prompt() -> str:
    logseq_info = ""
    if LOGSEQ_DIR:
        logseq_info = f"\nLogseq notes (archival, read-only): {LOGSEQ_DIR}"

    return f"""You are an AI assistant with direct access to the user's Personal Knowledge Management system.

PRIMARY (org-mode, for writing): {ORG_DIR}{logseq_info}

TOOLS:
- add_journal_note: Add a note to user's journal for today
- execute_shell: Run local commands (ripgrep, fd, emacs batch, git…) on PKM files.
- list_files: Browse org-mode and (optionally) Logseq.
- load_skill: Read procedural instructions from a local SKILL.md file.
- run_skill: Render a skill's command template and execute it locally.

**SKILL-FIRST WORKFLOW:**
- Before using `execute_shell` for common PKM tasks, **always check if a relevant skill exists**
- Common skill categories: journal-*, note-*, search-*, archive-*, etc.
- When uncertain, load the skill to inspect its template before deciding whether to use it
- If a skill provides a `template` or `command` key in front-matter, use `run_skill` with appropriate variables
- Only fall back to raw `execute_shell` when no skill applies or skill doesn't have an executable template


IMPORTANT DIRECTORY USAGE:
- When SEARCHING: Search both org-mode and Logseq directories
- When ADDING/WRITING: Always use org-mode directory ({ORG_DIR})
- Logseq notes are ARCHIVAL - read-only reference material

The user's PKM uses org-mode with:
- Hierarchical journal: Year > Month > Day
- Active timestamps: <2025-10-24 Thu>
- Property drawers with IDs
- Inline hashtags: #emacs #music
- Wiki links: [[wiki:topic]]
- TODO items with priorities

Logseq archive (if present) uses markdown with journals/pages/assets.

Be proactive and concise. Avoid raw org unless asked.

**CRITICAL SECURITY/EFFICIENCY CONSTRAINT:**
You have access to ONLY these directories:
- /Users/garyo/Documents/org-agenda (PRIMARY - read/write)
- /Users/garyo/Logseq Notes (SECONDARY - read-only)
- This server's directory, i.e. the current working dir on startup

ANY file operation (execute_shell, list_files, etc.) MUST be scoped to one of these directories.
NEVER run commands like 'find /', 'find /Users', or 'ls' without a path argument.
ALWAYS use relative paths (e.g., '.') when in org-agenda, or specify the full path to one of the two allowed directories.
If a task requires accessing files outside these directories, REFUSE and explain the constraint.
"""


def get_tools() -> List[Dict[str, Any]]:
    dirs_info = f"PRIMARY (org-mode): {ORG_DIR}"
    if LOGSEQ_DIR:
        dirs_info += f"\nSECONDARY (Logseq, read-only): {LOGSEQ_DIR}"

    return [
        {
            "name": "execute_shell",
            "description": f"""Execute a shell command with access to PKM files and tools.

Available tools:
- ripgrep (rg): fast PCRE2 search
- fd: better find replacement (faster, regex patterns, smart case; always prefer this over find)
- emacs: batch mode for org-ql, etc.
- cat/head/tail, git

Directories:\n{dirs_info}

Security: Only whitelisted commands allowed: {', '.join(sorted(ALLOWED_COMMANDS))}
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "working_dir": {"type": "string", "description": f"Working directory (defaults to {ORG_DIR})"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "list_files",
            "description": f"""List files in PKM directories. Directories:\n{dirs_info}""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern ('*.org', '**/*.org')"},
                    "show_stats": {"type": "boolean", "description": "Show sizes & mtimes", "default": False},
                    "directory": {"type": "string", "description": "org-mode (default), logseq, or both", "default": "org-mode"},
                }
            }
        },
        {
            "name": "load_skill",
            "description": "Load a local skill (reads skills/<name>/SKILL.md, returns front-matter + body).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill folder name (e.g., 'journal-navigation')"}
                },
                "required": ["name"]
            }
        },
        {
            "name": "run_skill",
            "description": "Render & execute a skill's template command using {var} substitution.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill folder name"},
                    "vars": {"type": "object", "description": "Variables for template placeholders"},
                    "template": {"type": "string", "description": "Optional override of the skill's template/command"}
                },
                "required": ["name"]
            }
        },
        {
            "name": "add_journal_note",
            "description": f"""Add a note to today's journal entry in {ORG_DIR}/journal.org.

This tool uses Emacs batch mode to properly handle the hierarchical journal structure:
- Creates today's entry if it doesn't exist (Year > Month > Day)
- Creates parent structure as needed
- Uses org-ml for reliable AST-based manipulation
- Adds note as a bullet point under today's heading

The note can contain any text including quotes, newlines, hashtags, and links.
Always use this tool instead of manually editing journal.org.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "The note content to add to today's journal entry"}
                },
                "required": ["note"]
            }
        }
    ]


# -------------------------
# Tool Implementations
# -------------------------

def execute_shell_command(command: str, working_dir: str = None) -> str:
    """Execute a whitelisted command in a sandboxed way (first token must be allowed)."""
    if working_dir is None:
        working_dir = str(ORG_DIR)

    cmd_binary = (command.split() or [""])[0]
    if cmd_binary not in ALLOWED_COMMANDS:
        return f"❌ Command not allowed: {cmd_binary}\nAllowed: {', '.join(sorted(ALLOWED_COMMANDS))}"

    logger.info(f"Executing: {command} (cwd: {working_dir})")
    try:
        start_time = time.time()
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        elapsed = time.time() - start_time
        logger.debug(f"Shell command completed in {elapsed:.3f}s")

        output = result.stdout or ""
        if result.stderr:
            logger.error(f"Shell command stderr: {result.stderr}")
            output += f"\n[stderr]: {result.stderr}"
        if result.returncode != 0:
            logger.error(f"Shell command failed with exit code {result.returncode}: {command}")
            output += f"\n[exit code: {result.returncode}]"

        if len(output) > 20000:
            output = output[:20000] + "\n\n... (output truncated)"

        return output if output else "[No output]"

    except subprocess.TimeoutExpired:
        error_msg = "❌ Command timed out after 60 seconds"
        logger.error(f"{error_msg}: {command}")
        return error_msg
    except Exception as e:
        error_msg = f"❌ Error executing command: {str(e)}"
        logger.error(f"{error_msg} (command: {command})")
        return error_msg


def list_org_files(pattern: str = "*", show_stats: bool = False, directory: str = "org-mode") -> str:
    """List files in org and/or logseq with optional stats; hides dotfiles/.git by default."""
    try:
        import datetime

        def list_from_dir(base_dir: Path, dir_label: str):
            if "**" in pattern:
                files = list(base_dir.rglob(pattern.replace("**", "*")))
            else:
                files = list(base_dir.glob(pattern))

            files = [f for f in files if '.git' not in f.parts and not any(part.startswith('.') for part in f.parts)]
            if not files:
                return []

            if show_stats:
                files.sort(key=lambda f: f.stat().st_mtime if f.is_file() else 0, reverse=True)
            else:
                files.sort()

            MAX_FILES = 100
            if len(files) > MAX_FILES:
                files = files[:MAX_FILES]
                truncated = True
            else:
                truncated = False

            output = []
            for f in files:
                rel_path = f.relative_to(base_dir)
                if show_stats and f.is_file():
                    size = f.stat().st_size
                    size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                    mtime = f.stat().st_mtime
                    mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    output.append(f"[{dir_label}] {rel_path} ({size_str}, modified {mtime_str})")
                else:
                    output.append(f"[{dir_label}] {rel_path}")

            if truncated:
                output.append(f"\n... (showing {MAX_FILES} files; truncated)")

            return output

        all_output = []

        if directory in ["org-mode", "both"]:
            all_output.extend(list_from_dir(ORG_DIR, "org-mode"))

        if directory in ["logseq", "both"] and LOGSEQ_DIR:
            all_output.extend(list_from_dir(LOGSEQ_DIR, "logseq"))
        elif directory == "logseq" and not LOGSEQ_DIR:
            error_msg = "❌ Logseq directory not configured or does not exist"
            logger.error(error_msg)
            return error_msg

        if not all_output:
            return f"No files matching pattern: {pattern}"

        return "\n".join(all_output)

    except Exception as e:
        error_msg = f"❌ Error listing files: {str(e)}"
        logger.error(f"{error_msg} (pattern: {pattern}, directory: {directory})")
        return error_msg


def load_skill(name: str) -> str:
    """Return a skill's front-matter + body for the model to follow."""
    sk = _resolve_skill(name)
    if not sk:
        available = ", ".join(sorted(LOCAL_SKILLS.keys())) or "(none)"
        return f"❌ Skill not found: {name}\nAvailable: {available}"
    fm = yaml.safe_dump(sk["frontmatter"], sort_keys=False).strip()
    body = sk["body"]
    return f"---\n{fm}\n---\n\n{body}" if fm else body


def run_skill(name: str, vars: Optional[Dict[str, Any]], template_override: Optional[str]) -> str:
    """Render a skill template (front-matter: template|command) and execute via execute_shell."""
    sk = _resolve_skill(name)
    if not sk:
        return f"❌ Skill not found: {name}"
    fm = sk["frontmatter"] or {}
    tpl = template_override or fm.get("template") or fm.get("command") or ""
    if not tpl:
        return "❌ Skill has no 'template' or 'command' in front-matter, and no override provided."
    rendered = _render_template(tpl, vars or {})
    return execute_shell_command(rendered)


def add_journal_note(note: str) -> str:
    """Add a note to today's journal using Emacs batch mode with proper org structure handling.

    Uses the user's gco-pkm-journal-today function which properly creates/navigates
    the hierarchical journal structure (Year > Month > Day).
    """
    journal_path = ORG_DIR / "journal.org"

    # Use json.dumps for proper elisp string escaping (handles quotes, newlines, backslashes, etc.)
    # This produces a JSON string which has the same escaping rules as elisp strings
    # ensure_ascii=False preserves Unicode characters like emoji
    note_escaped = json.dumps("- " + note, ensure_ascii=False)

    # Construct the elisp script
    elisp_script = f"""(progn
    (add-to-list 'load-path "~/.config/emacs/lisp")
    (let ((default-directory (expand-file-name "~/.config/emacs/var/elpaca/builds")))
      (when (file-directory-p default-directory)
        (normal-top-level-add-subdirs-to-load-path)))
    (require 'init-org)
    (require 'gco-pkm)
    (let ((inhibit-file-locks t))
      (find-file "{journal_path}")
      (gco-pkm-journal-today)
      (insert {note_escaped})
      (save-buffer)
      (message "Added note to today's journal")))"""

    # Log the full command for debugging
    logger.info(f"Adding journal note: {note[:100]}{'...' if len(note) > 100 else ''}")
    logger.debug(f"Emacs batch command: emacs --batch --eval <elisp>")
    logger.debug(f"Elisp script:\n{elisp_script}")

    try:
        result = subprocess.run(
            ["emacs", "--batch", "--eval", elisp_script],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ORG_DIR)
        )

        if result.returncode != 0:
            logger.error(f"Emacs batch failed with exit code {result.returncode}")
            logger.error(f"Stderr: {result.stderr}")
            return f"❌ Failed to add journal note (exit code {result.returncode})\nStderr: {result.stderr}"

        if result.stderr:
            logger.warning(f"Emacs batch stderr: {result.stderr}")

        output = result.stdout.strip() if result.stdout else "Note added successfully"
        logger.info(f"Journal note added successfully")
        return output

    except subprocess.TimeoutExpired:
        error_msg = "❌ Emacs batch command timed out after 30 seconds"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"❌ Error adding journal note: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def execute_tool(name: str, params: Dict[str, Any]) -> str:
    """Route tool calls to their handlers."""
    try:
        if name == "execute_shell":
            return execute_shell_command(params["command"], params.get("working_dir"))
        elif name == "list_files":
            return list_org_files(
                params.get("pattern", "*"),
                params.get("show_stats", False),
                params.get("directory", "org-mode")
            )
        elif name == "load_skill":
            return load_skill(params["name"])
        elif name == "run_skill":
            return run_skill(params["name"], params.get("vars"), params.get("template"))
        elif name == "add_journal_note":
            return add_journal_note(params["note"])

        error_msg = f"❌ Unknown tool: {name}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"❌ Tool execution failed for {name}: {str(e)}"
        logger.error(f"{error_msg} (params: {params})")
        return error_msg


# -------------------------
# Web Endpoints & Loop
# -------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/query', methods=['POST'])
def query():
    """Main query endpoint with tool-use loop."""
    request_start = time.time()

    data = request.json
    session_id = data.get('session_id', 'default')
    user_message = data['message']
    model = data.get('model', MODEL)

    # create session
    if session_id not in sessions:
        sessions[session_id] = {
            'history': [],
            'system_prompt': get_system_prompt()
        }
    session = sessions[session_id]

    # append user message
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
                tools=get_tools()
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
                        result = execute_tool(block.name, block.input)
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
                    tools=get_tools()
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
    global LOCAL_SKILLS
    LOCAL_SKILLS = _discover_local_skills(SKILLS_DIR)
    return jsonify({"skills": sorted(LOCAL_SKILLS.keys())})


@app.route('/health', methods=['GET'])
def health():
    """Basic health info + local skills list."""
    health_data = {
        "status": "ok",
        "org_dir": str(ORG_DIR),
        "org_dir_exists": ORG_DIR.exists(),
        "skills_dir": str(SKILLS_DIR),
        "allowed_commands": sorted(ALLOWED_COMMANDS),
        "local_skills": sorted(LOCAL_SKILLS.keys()),
    }
    if LOGSEQ_DIR:
        health_data["logseq_dir"] = str(LOGSEQ_DIR)
        health_data["logseq_dir_exists"] = LOGSEQ_DIR.exists()
    else:
        health_data["logseq_dir"] = None
    return jsonify(health_data)


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("gco-pkm-llm Bridge Server — Local Skills Edition")
    logger.info("=" * 60)
    logger.info(f"Model: {MODEL}")
    logger.info(f"Org files (primary): {ORG_DIR}")
    if LOGSEQ_DIR:
        logger.info(f"Logseq notes (archival): {LOGSEQ_DIR}")
    logger.info(f"Skills dir: {SKILLS_DIR}")
    logger.info(f"Local skills: {', '.join(sorted(LOCAL_SKILLS.keys())) or '(none)'}")
    logger.info(f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}")
    logger.info(f"Server starting at http://{HOST}:{PORT}")
    logger.info(f"Log level: {LOG_LEVEL}")
    logger.info("=" * 60)

    # In production, use proper WSGI server (gunicorn, waitress, etc.)
    app.run(host=HOST, port=PORT, debug=True)
