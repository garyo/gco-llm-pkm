#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "anthropic>=0.39.0",
#   "flask>=3.0.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""
gco-pkm-llm Bridge Server

Provides natural language access to org-mode Personal Knowledge Management
system via Claude API with tools.
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any

from anthropic import Anthropic
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

ORG_DIR = Path(os.getenv("ORG_DIR", "~/Documents/org-agenda")).expanduser()
if not ORG_DIR.exists():
    raise ValueError(f"ORG_DIR does not exist: {ORG_DIR}")

LOGSEQ_DIR = Path(os.getenv("LOGSEQ_DIR", "~/Logseq Notes")).expanduser()
# LOGSEQ_DIR is optional - only validate if it exists or user explicitly set it
if os.getenv("LOGSEQ_DIR") and not LOGSEQ_DIR.exists():
    print(f"⚠️  Warning: LOGSEQ_DIR does not exist: {LOGSEQ_DIR}")
    LOGSEQ_DIR = None
elif not LOGSEQ_DIR.exists():
    LOGSEQ_DIR = None

SKILLS_DIR = Path(os.getenv("SKILLS_DIR", "./skills")).expanduser()
SKILLS_DIR.mkdir(exist_ok=True)

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")

ALLOWED_COMMANDS = set(
    os.getenv("ALLOWED_COMMANDS", "rg,ripgrep,grep,find,cat,ls,emacs,git").split(",")
)

# Initialize Flask and Anthropic client
app = Flask(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Session storage (in-memory for now; use Redis for production)
sessions: Dict[str, Dict[str, Any]] = {}


def get_system_prompt() -> str:
    """Generate system prompt for Claude"""
    logseq_info = ""
    if LOGSEQ_DIR:
        logseq_info = f"\nLogseq notes (archival, read-only): {LOGSEQ_DIR}"

    return f"""You are an AI assistant with direct access to the user's Personal Knowledge Management system.

PRIMARY (org-mode, for writing): {ORG_DIR}{logseq_info}

You have three powerful tools:
1. execute_shell: Run commands (ripgrep, emacs, find, etc.)
2. read_skill: Load detailed instructions for complex tasks
3. list_files: Browse available files

IMPORTANT DIRECTORY USAGE:
- When SEARCHING: Search both org-mode and Logseq directories
- When ADDING/WRITING: Always use org-mode directory ({ORG_DIR})
- Logseq notes are ARCHIVAL - read-only reference material

The user's PKM system uses org-mode with:
- Hierarchical journal: Year > Month > Day
- Active timestamps: <2025-10-24 Thu>
- Property drawers with IDs
- Inline hashtags: #emacs #music
- Wiki links: [[wiki:topic]]
- TODO items with priorities

The Logseq archive uses markdown with:
- Dated journal files: YYYY-MM-DD.md
- Block references: ((block-id))
- Page links: [[Page Name]]
- Tags: #tag

When the user asks to search or analyze their notes:
1. Search BOTH directories (org-mode AND Logseq if available)
2. Indicate which directory results came from
3. Consider if a skill exists that would help
4. Parse and present results naturally

When the user asks to add/write content:
1. ALWAYS write to org-mode directory only
2. Use appropriate org-mode syntax
3. Never modify Logseq files

Be proactive: If you see the user would benefit from a command or skill, use it without asking.
Keep responses concise but complete. Don't show raw org syntax unless asked.
"""


def get_tools() -> List[Dict[str, Any]]:
    """Define tools available to Claude"""

    # Get available skills
    available_skills = [s.stem for s in SKILLS_DIR.glob("*.md")]
    skills_list = "\n".join(f"- {s}" for s in available_skills) if available_skills else "(No skills installed yet)"

    # Build directory info
    dirs_info = f"PRIMARY (org-mode): {ORG_DIR}"
    if LOGSEQ_DIR:
        dirs_info += f"\nSECONDARY (Logseq, read-only): {LOGSEQ_DIR}"

    search_examples = """Examples:
- Search org-mode only: rg -i '#emacs' {org_dir}
- Search Logseq only: rg -i '#emacs' {logseq_dir}
- Search both: rg -i '#emacs' {org_dir} {logseq_dir}
- Find recent files: find {org_dir} -type f -mtime -7
- Run org-ql: emacs --batch --eval '(progn (require \\'org-ql) ...)'
- Count entries: rg -c '^\\*\\*\\*' journal.org"""

    if LOGSEQ_DIR:
        search_examples = search_examples.format(
            org_dir=ORG_DIR,
            logseq_dir=LOGSEQ_DIR
        )
    else:
        search_examples = search_examples.format(
            org_dir=ORG_DIR,
            logseq_dir="(not available)"
        ).replace(" {logseq_dir}", "")

    return [
        {
            "name": "execute_shell",
            "description": f"""Execute a shell command with access to PKM files and tools.

Available tools:
- ripgrep (rg): Fast text search with PCRE regex
- emacs: Batch mode for org-ql queries and file manipulation
- find: Locate files by name, date, etc.
- cat, head, tail: Read files
- git: Version control operations (log, diff, etc.)

Directories:
{dirs_info}

IMPORTANT: When searching, search BOTH directories. When writing, use org-mode directory only.

{search_examples}

Security: Only whitelisted commands allowed: {', '.join(sorted(ALLOWED_COMMANDS))}
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute"
                    },
                    "working_dir": {
                        "type": "string",
                        "description": f"Working directory (defaults to {ORG_DIR})"
                    }
                },
                "required": ["command"]
            }
        },
        {
            "name": "read_skill",
            "description": f"""Read a skill file to get detailed instructions for a specific task.

Skills provide detailed guidance for complex operations like:
- Searching with org-ql queries
- Parsing org-mode structure
- Working with org-roam
- Custom PKM workflows

Available skills:
{skills_list}

Load a skill when you need detailed instructions for a complex task.
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of skill to load (without .md extension)"
                    }
                },
                "required": ["skill_name"]
            }
        },
        {
            "name": "list_files",
            "description": f"""List files in PKM directories with optional filtering.

By default lists from org-mode directory. Use directory parameter to specify which to list.

Available directories:
- org-mode: {ORG_DIR}
{'- logseq: ' + str(LOGSEQ_DIR) if LOGSEQ_DIR else ''}
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Optional glob pattern (e.g., '*.org', '*.md', 'journal.*', '**/*.org' for recursive)"
                    },
                    "show_stats": {
                        "type": "boolean",
                        "description": "Show file sizes and modification times",
                        "default": False
                    },
                    "directory": {
                        "type": "string",
                        "description": "Which directory to list: 'org-mode' (default), 'logseq', or 'both'",
                        "default": "org-mode"
                    }
                },
                "required": []
            }
        }
    ]


def execute_shell_command(command: str, working_dir: str = None) -> str:
    """Execute shell command with safety checks"""
    
    # Use default working directory if not specified
    if working_dir is None:
        working_dir = str(ORG_DIR)
    
    # Security: Check if command starts with allowed binary
    cmd_binary = command.split()[0]
    if cmd_binary not in ALLOWED_COMMANDS:
        return f"❌ Command not allowed: {cmd_binary}\nAllowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
    
    # Log the command (important for debugging and security)
    print(f"[EXEC] {command} (cwd: {working_dir})")
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30  # Prevent hanging
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        
        # Truncate very long output
        if len(output) > 20000:
            output = output[:20000] + "\n\n... (output truncated, too long)"
        
        return output if output else "[No output]"
    
    except subprocess.TimeoutExpired:
        return "❌ Command timed out after 30 seconds"
    except Exception as e:
        return f"❌ Error executing command: {str(e)}"


def read_skill_file(skill_name: str) -> str:
    """Read a skill file"""
    skill_file = SKILLS_DIR / f"{skill_name}.md"
    
    if not skill_file.exists():
        available = [s.stem for s in SKILLS_DIR.glob("*.md")]
        return f"❌ Skill not found: {skill_name}\n\nAvailable skills: {', '.join(available) if available else '(none)'}"
    
    try:
        return skill_file.read_text()
    except Exception as e:
        return f"❌ Error reading skill: {str(e)}"


def list_org_files(pattern: str = "*", show_stats: bool = False, directory: str = "org-mode") -> str:
    """List files in PKM directories"""
    try:
        import datetime

        def list_from_dir(base_dir: Path, dir_label: str):
            """Helper to list files from a single directory"""
            if "**" in pattern:
                # Recursive glob
                files = list(base_dir.rglob(pattern.replace("**", "*")))
            else:
                # Non-recursive glob
                files = list(base_dir.glob(pattern))

            if not files:
                return []

            # Sort by name
            files.sort()

            # Format output
            output = []
            for f in files:
                rel_path = f.relative_to(base_dir)
                if show_stats and f.is_file():
                    size = f.stat().st_size
                    size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                    mtime = f.stat().st_mtime
                    mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    output.append(f"[{dir_label}] {rel_path} ({size_str}, modified {mtime_str})")
                else:
                    output.append(f"[{dir_label}] {rel_path}")

            return output

        all_output = []

        if directory in ["org-mode", "both"]:
            all_output.extend(list_from_dir(ORG_DIR, "org-mode"))

        if directory in ["logseq", "both"] and LOGSEQ_DIR:
            all_output.extend(list_from_dir(LOGSEQ_DIR, "logseq"))
        elif directory == "logseq" and not LOGSEQ_DIR:
            return "❌ Logseq directory not configured or does not exist"

        if not all_output:
            return f"No files matching pattern: {pattern}"

        return "\n".join(all_output)

    except Exception as e:
        return f"❌ Error listing files: {str(e)}"


def execute_tool(name: str, params: Dict[str, Any]) -> str:
    """Execute a tool and return results"""

    if name == "execute_shell":
        return execute_shell_command(
            params["command"],
            params.get("working_dir")
        )

    elif name == "read_skill":
        return read_skill_file(params["skill_name"])

    elif name == "list_files":
        return list_org_files(
            params.get("pattern", "*"),
            params.get("show_stats", False),
            params.get("directory", "org-mode")
        )

    return f"❌ Unknown tool: {name}"


@app.route('/')
def index():
    """Serve the web interface"""
    return render_template('index.html')


@app.route('/query', methods=['POST'])
def query():
    """Handle user queries"""
    data = request.json
    session_id = data.get('session_id', 'default')
    user_message = data['message']
    
    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = {
            'history': [],
            'system_prompt': get_system_prompt()
        }
    
    session = sessions[session_id]
    
    # Add user message to history
    session['history'].append({
        "role": "user",
        "content": user_message
    })
    
    print(f"[USER] {user_message}")
    
    try:
        # Call Claude with tools
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=session['system_prompt'],
            messages=session['history'],
            tools=get_tools()
        )
        
        # Handle tool use loop
        while response.stop_reason == "tool_use":
            # Extract tool calls and execute them
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"[TOOL] {block.name} with params: {block.input}")
                    result = execute_tool(block.name, block.input)
                    print(f"[RESULT] {result[:200]}{'...' if len(result) > 200 else ''}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })
            
            # Add assistant message and tool results to history
            session['history'].append({
                "role": "assistant",
                "content": response.content
            })
            session['history'].append({
                "role": "user",
                "content": tool_results
            })
            
            # Continue conversation
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8192,
                system=session['system_prompt'],
                messages=session['history'],
                tools=get_tools()
            )
        
        # Extract final text response
        assistant_text = ""
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text
        
        # Add to history
        session['history'].append({
            "role": "assistant",
            "content": response.content
        })
        
        print(f"[ASSISTANT] {assistant_text[:200]}{'...' if len(assistant_text) > 200 else ''}")
        
        return jsonify({
            "response": assistant_text,
            "session_id": session_id
        })
    
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({
            "response": f"❌ Error: {str(e)}",
            "session_id": session_id
        }), 500


@app.route('/sessions/<session_id>/history', methods=['GET'])
def get_history(session_id):
    """Get conversation history for a session"""
    if session_id not in sessions:
        return jsonify([])
    
    # Return simplified history for display
    history = []
    for msg in sessions[session_id]['history']:
        if msg['role'] in ['user', 'assistant']:
            # Extract text content
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
    """Clear conversation history"""
    if session_id in sessions:
        del sessions[session_id]
        print(f"[SESSION] Cleared session: {session_id}")
    return jsonify({"status": "ok"})


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    health_data = {
        "status": "ok",
        "org_dir": str(ORG_DIR),
        "org_dir_exists": ORG_DIR.exists(),
        "skills_dir": str(SKILLS_DIR),
        "skills_available": [s.stem for s in SKILLS_DIR.glob("*.md")],
        "allowed_commands": sorted(ALLOWED_COMMANDS)
    }

    if LOGSEQ_DIR:
        health_data["logseq_dir"] = str(LOGSEQ_DIR)
        health_data["logseq_dir_exists"] = LOGSEQ_DIR.exists()
    else:
        health_data["logseq_dir"] = None

    return jsonify(health_data)


if __name__ == '__main__':
    print("=" * 60)
    print("gco-pkm-llm Bridge Server")
    print("=" * 60)
    print(f"Org files (primary): {ORG_DIR}")
    if LOGSEQ_DIR:
        print(f"Logseq notes (archival): {LOGSEQ_DIR}")
    print(f"Skills dir: {SKILLS_DIR}")
    print(f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}")
    print(f"\nServer starting at http://{HOST}:{PORT}")
    print("=" * 60)

    # In production, use proper WSGI server (gunicorn, waitress, etc.)
    app.run(host=HOST, port=PORT, debug=True)
