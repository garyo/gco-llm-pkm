"""MCP tool wrappers for existing PKM Bridge tools.

Each MCP tool wraps an existing BaseTool.execute() implementation.
The existing tools return strings, which maps directly to MCP text results.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp_server.tools")

# Lazy-initialized shared state
_config = None
_tool_registry = None
_context_retriever = None
_file_editor = None


def _get_config():
    """Lazy-load Config (avoids import at module level before env is loaded)."""
    global _config
    if _config is None:
        from config.settings import Config

        _config = Config()
        logger.info(f"Config loaded: org_dir={_config.org_dir}, logseq_dir={_config.logseq_dir}")
    return _config


def _get_tool_registry():
    """Lazy-initialize the tool registry with all existing tools."""
    global _tool_registry
    if _tool_registry is not None:
        return _tool_registry

    from pkm_bridge.tools.files import ListFilesTool
    from pkm_bridge.tools.find_context import FindContextTool
    from pkm_bridge.tools.registry import ToolRegistry
    from pkm_bridge.tools.schedule_task import ScheduleTaskTool
    from pkm_bridge.tools.search_notes import SearchNotesTool
    from pkm_bridge.tools.shell import ExecuteShellTool, WriteAndExecuteScriptTool
    from pkm_bridge.tools.skills import ListSkillsTool, NoteToSelfTool, SaveSkillTool, UseSkillTool

    config = _get_config()
    tool_logger = logging.getLogger("pkm_bridge.tools")

    registry = ToolRegistry()

    # Core file/search tools
    registry.register(
        ExecuteShellTool(tool_logger, config.dangerous_patterns, config.org_dir, config.logseq_dir)
    )
    registry.register(
        WriteAndExecuteScriptTool(
            tool_logger, config.dangerous_patterns, config.org_dir, config.logseq_dir
        )
    )
    registry.register(ListFilesTool(tool_logger, config.org_dir, config.logseq_dir))
    registry.register(SearchNotesTool(tool_logger, config.org_dir, config.logseq_dir))
    registry.register(FindContextTool(tool_logger, config.org_dir, config.logseq_dir))

    # Skills tools
    registry.register(SaveSkillTool(tool_logger, config.org_dir, config.dangerous_patterns))
    registry.register(ListSkillsTool(tool_logger, config.org_dir))
    registry.register(UseSkillTool(tool_logger, config.org_dir))
    registry.register(NoteToSelfTool(tool_logger))
    registry.register(ScheduleTaskTool(tool_logger))

    # Optional: TickTick (reads credentials from env vars internally)
    try:
        from pkm_bridge.ticktick_oauth import TickTickOAuth
        from pkm_bridge.tools.ticktick import TickTickTool

        ticktick_oauth = TickTickOAuth()
        if ticktick_oauth.client_id:
            registry.register(TickTickTool(tool_logger, ticktick_oauth))
            logger.info("TickTick tool registered")
    except Exception as e:
        logger.info(f"TickTick not available: {e}")

    # Optional: Google Calendar (reads credentials from env vars internally)
    try:
        from pkm_bridge.google_oauth import GoogleOAuth
        from pkm_bridge.tools.google_calendar import GoogleCalendarTool

        google_oauth = GoogleOAuth(
            scopes=[
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar.events",
            ],
            redirect_uri_env="GOOGLE_REDIRECT_URI",
        )
        registry.register(GoogleCalendarTool(tool_logger, google_oauth))
        logger.info("Google Calendar tool registered")
    except Exception as e:
        logger.info(f"Google Calendar not available: {e}")

    # Optional: Google Gmail
    try:
        from pkm_bridge.google_oauth import GoogleOAuth
        from pkm_bridge.tools.google_gmail import GoogleGmailTool

        google_gmail_oauth = GoogleOAuth(
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            redirect_uri_env="GOOGLE_GMAIL_REDIRECT_URI",
        )
        registry.register(GoogleGmailTool(tool_logger, google_gmail_oauth))
        logger.info("Google Gmail tool registered")
    except Exception as e:
        logger.info(f"Google Gmail not available: {e}")

    # Optional: Semantic search (needs pgvector + Voyage)
    try:
        retriever = _get_context_retriever()
        if retriever:
            from pkm_bridge.tools.semantic_search import SemanticSearchTool

            registry.register(SemanticSearchTool(tool_logger, retriever))
            logger.info("Semantic search tool registered")
    except Exception as e:
        logger.info(f"Semantic search not available: {e}")

    _tool_registry = registry
    logger.info(f"Tool registry: {len(registry)} tools: {', '.join(registry.list_tools())}")
    return registry


def _get_context_retriever():
    """Lazy-initialize the context retriever for semantic search."""
    global _context_retriever
    if _context_retriever is not None:
        return _context_retriever

    voyage_key = os.getenv("VOYAGE_API_KEY")
    if not voyage_key:
        return None

    try:
        from pkm_bridge.context_retriever import ContextRetriever
        from pkm_bridge.database import init_db
        from pkm_bridge.embeddings.voyage_client import VoyageClient

        init_db()
        voyage_client = VoyageClient(api_key=voyage_key)
        _context_retriever = ContextRetriever(voyage_client)
        return _context_retriever
    except Exception as e:
        logger.warning(f"Failed to init context retriever: {e}")
        return None


def _get_file_editor():
    """Lazy-initialize the FileEditor."""
    global _file_editor
    if _file_editor is None:
        from pkm_bridge.file_editor import FileEditor

        config = _get_config()
        _file_editor = FileEditor(
            logging.getLogger("pkm_bridge.file_editor"),
            str(config.org_dir),
            str(config.logseq_dir) if config.logseq_dir else None,
        )
    return _file_editor


def _log_tool_execution(tool_name: str, params: dict, result: str, duration_ms: int):
    """Log tool execution to the ToolExecutionLog table."""
    try:
        from pkm_bridge.database import get_db
        from pkm_bridge.db_repository import ToolExecutionLogRepository

        db = get_db()
        try:
            ToolExecutionLogRepository.create_log(
                db=db,
                session_id="mcp",
                query_id=f"mcp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                user_message="(via MCP)",
                tool_name=tool_name,
                tool_params=params,
                result_summary=result[:500] if result else "",
                exit_code=0,
                execution_time_ms=duration_ms,
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"Failed to log tool execution: {e}")


def _execute_tool(name: str, params: dict, context: dict | None = None) -> str:
    """Execute a tool from the registry with logging."""
    registry = _get_tool_registry()
    start = time.time()
    result = registry.execute_tool(name, params, context=context)
    duration_ms = int((time.time() - start) * 1000)
    _log_tool_execution(name, params, result, duration_ms)
    return result


def register_all_tools(mcp: FastMCP):
    """Register all MCP tools on the FastMCP server."""

    # --- Core search tools ---

    @mcp.tool()
    def search_notes(
        pattern: str,
        context: int = 3,
        limit: int = 200000,
    ) -> str:
        """Search PKM notes for a regex pattern. Returns matches from journals (newest first).

        Args:
            pattern: Regex pattern to search for (case-insensitive)
            context: Lines of context around each match
            limit: Approximate character limit for results
        """
        return _execute_tool("search_notes", {
            "pattern": pattern,
            "context": context,
            "limit": limit,
        })

    @mcp.tool()
    def find_context(
        pattern: str,
        paths: list[str] | None = None,
        newer: str | None = None,
        max_results: int = 50,
    ) -> str:
        """Find notes matching a regex with full hierarchical context (headings, parent bullets).

        Returns YAML with filename, date, matched text, and surrounding context structure.
        Results sorted by date (newest first). Only first match per file.

        Args:
            pattern: Regex pattern to search for (case-insensitive)
            paths: Optional list of specific files/directories to search
            newer: Optional YYYY-MM-DD date filter (only notes >= this date)
            max_results: Maximum number of results
        """
        params: dict[str, Any] = {"pattern": pattern, "max_results": max_results}
        if paths:
            params["paths"] = paths
        if newer:
            params["newer"] = newer
        return _execute_tool("find_context", params)

    @mcp.tool()
    def semantic_search(
        query: str,
        limit: int = 10,
        min_similarity: float = 0.6,
        newer: str | None = None,
    ) -> str:
        """Search notes using semantic similarity (understands meaning, not just keywords).

        Use this for knowledge-base questions. Returns YAML with filename, similarity score,
        heading path, content, and line number.

        Args:
            query: Natural language search query
            limit: Maximum results
            min_similarity: Minimum similarity threshold 0-1 (higher = more strict)
            newer: Optional YYYY-MM-DD date filter
        """
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "min_similarity": min_similarity,
        }
        if newer:
            params["newer"] = newer
        return _execute_tool("semantic_search", params)

    # --- File tools ---

    @mcp.tool()
    def list_files(
        pattern: str = "*",
        show_stats: bool = False,
        directory: str = "org-mode",
    ) -> str:
        """List files in PKM directories.

        Args:
            pattern: Glob pattern (e.g., '*.org', '**/*.org')
            show_stats: Show file sizes and modification times
            directory: Which directory: 'org-mode' (default), 'logseq', or 'both'
        """
        return _execute_tool("list_files", {
            "pattern": pattern,
            "show_stats": show_stats,
            "directory": directory,
        })

    @mcp.tool()
    def read_file(path: str) -> str:
        """Read a PKM file's content.

        Args:
            path: File path in format 'org:relative/path.org' or 'logseq:relative/path.md'
        """
        editor = _get_file_editor()
        start = time.time()
        try:
            result = editor.read_file(path)
            content = result["content"]
            duration_ms = int((time.time() - start) * 1000)
            _log_tool_execution("read_file", {"path": path}, f"({len(content)} bytes)", duration_ms)
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    @mcp.tool()
    def write_file(path: str, content: str, create_only: bool = False) -> str:
        """Write content to a PKM file. Creates parent directories if needed.

        Args:
            path: File path in format 'org:relative/path.org' or 'logseq:relative/path.md'
            content: File content to write
            create_only: If true, only create the file if it doesn't already exist
        """
        editor = _get_file_editor()
        start = time.time()
        try:
            result = editor.write_file(path, content, create_only=create_only)
            duration_ms = int((time.time() - start) * 1000)
            _log_tool_execution(
                "write_file", {"path": path, "create_only": create_only},
                f"status={result['status']}", duration_ms,
            )
            return f"File {result['status']}: {path} ({result.get('size', 0)} bytes)"
        except Exception as e:
            return f"Error writing file: {e}"

    # --- Shell tools ---

    @mcp.tool()
    def execute_shell(command: str, working_dir: str | None = None) -> str:
        """Execute a shell command in the PKM environment (Docker-isolated).

        Available tools: ripgrep (rg), fd, emacs, git, sed, awk, standard Unix tools.
        Dangerous patterns (rm -rf /, fork bombs, package installs) are blocked.

        Args:
            command: Shell command or pipeline to execute
            working_dir: Working directory (defaults to org-mode directory)
        """
        params: dict[str, Any] = {"command": command}
        if working_dir:
            params["working_dir"] = working_dir
        return _execute_tool("execute_shell", params)

    @mcp.tool()
    def write_and_execute_script(
        script_content: str,
        description: str,
        working_dir: str | None = None,
    ) -> str:
        """Write a shell script to /tmp and execute it. Use for multi-step operations.

        Script automatically gets #!/bin/bash and set -euo pipefail.

        Args:
            script_content: Shell script content (without shebang)
            description: Brief description of what the script does (for audit log)
            working_dir: Working directory for execution
        """
        params: dict[str, Any] = {
            "script_content": script_content,
            "description": description,
        }
        if working_dir:
            params["working_dir"] = working_dir
        return _execute_tool("write_and_execute_script", params)

    # --- TickTick ---

    @mcp.tool()
    def ticktick(action: str, **kwargs) -> str:
        """Query and manage TickTick tasks.

        Actions: 'list_today', 'list_all', 'search', 'create', 'update', 'complete'.

        Args:
            action: The action to perform
            **kwargs: Additional parameters depending on action (e.g., title, due_date, task_id)
        """
        params = {"action": action, **kwargs}
        return _execute_tool("ticktick_query", params)

    # --- Skill tools ---

    @mcp.tool()
    def save_skill(
        skill_name: str,
        skill_type: str,
        description: str,
        content: str,
        trigger: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """Save a reusable skill (shell script or recipe).

        Args:
            skill_name: Kebab-case name (e.g., 'weekly-review'). Max 50 chars, [a-z0-9-].
            skill_type: 'shell' for executable scripts, 'recipe' for procedure descriptions
            description: Brief description of what the skill does
            content: Shell script code or markdown procedure steps
            trigger: When to use this skill (e.g., 'user asks for weekly review')
            tags: Tags for categorization
        """
        return _execute_tool("save_skill", {
            "skill_name": skill_name,
            "skill_type": skill_type,
            "description": description,
            "content": content,
            "trigger": trigger,
            "tags": tags or [],
        })

    @mcp.tool()
    def list_skills(tag: str = "", search: str = "") -> str:
        """List available saved skills with descriptions and usage stats.

        Args:
            tag: Optional tag filter
            search: Optional text to match in name/description
        """
        return _execute_tool("list_skills", {"tag": tag, "search": search})

    @mcp.tool()
    def use_skill(skill_name: str) -> str:
        """Load a saved skill's content. For shell skills, pass to execute_shell.
        For recipe skills, follow the procedure. Tracks usage count.

        Args:
            skill_name: Name of the skill to load
        """
        return _execute_tool("use_skill", {"skill_name": skill_name})

    # --- Working memory ---

    @mcp.tool()
    def note_to_self(
        note: str,
        category: str = "other",
    ) -> str:
        """Save a note to working memory. Notes persist within the MCP session.

        Use for: user preferences, effective strategies, corrections, context to retain.

        Args:
            note: The note to save
            category: One of: user_preference, discovery, strategy, correction, other
        """
        return _execute_tool("note_to_self", {
            "note": note,
            "category": category,
        }, context={"session_id": "mcp"})

    # --- New MCP-specific tools ---

    @mcp.tool()
    def read_prompt_context() -> str:
        """Load PKM assistant context. CALL THIS AT THE START OF EVERY CONVERSATION.

        Returns: system prompt, learned rules, user profile, and recent journal summary.
        This provides the persona, instructions, and background knowledge needed to
        assist effectively.
        """
        start = time.time()
        parts: list[str] = []

        config = _get_config()

        # 1. System prompt (instructions + persona)
        system_prompt = config.get_system_prompt()
        parts.append("# SYSTEM INSTRUCTIONS\n\n" + system_prompt)

        # 2. Learned rules from self-improvement agent
        try:
            from pkm_bridge.database import get_db, init_db
            from pkm_bridge.db_repository import LearnedRuleRepository

            init_db()
            db = get_db()
            try:
                rules = LearnedRuleRepository.get_active(db)
                if rules:
                    rules_text = config._format_learned_rules(rules)
                    if rules_text:
                        parts.append(rules_text)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to load learned rules: {e}")

        # 3. User profile from .pkm/memory/
        memory_dir = config.org_dir / ".pkm" / "memory"
        if memory_dir.exists():
            for name in ["user-profile.md", "observed-patterns.md"]:
                filepath = memory_dir / name
                if filepath.exists():
                    content = filepath.read_text(encoding="utf-8").strip()
                    if content:
                        title = name.replace(".md", "").replace("-", " ").upper()
                        parts.append(f"\n\n# {title}\n\n{content}")

        # 4. Recent journal summary (last 3 days)
        try:
            retriever = _get_context_retriever()
            if retriever:
                journals = retriever.retrieve_recent_journals(days=3)
                if journals:
                    journal_text = "\n\n".join(
                        f"## {j.get('filename', 'unknown')}\n{j.get('content', '')}"
                        for j in journals
                    )
                    parts.append("\n\n# RECENT JOURNALS (last 3 days)\n\n" + journal_text)
        except Exception as e:
            logger.debug(f"Failed to load recent journals: {e}")

        # 5. Current date/time
        tz_str = os.getenv("TIMEZONE", "America/New_York")
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = None
        now = datetime.now(tz) if tz else datetime.now()
        timestring = now.strftime("%A, %B %d, %Y, %H:%M:%S %Z")
        parts.append(
            f"\n\nThe CURRENT date/time is {now.isoformat()} ({timestring}). "
            "Always use this for time-related questions."
        )

        # 6. Editor link instructions
        editor_base = os.getenv("EDITOR_BASE_URL", "https://pkm.oberbrunner.com/editor")
        parts.append(
            f"\n\n# EDITOR LINKS\n\n"
            f"When referencing PKM files, format as clickable editor links:\n"
            f"[display name]({editor_base}/?file=TYPE:PATH)\n"
            f"For org-id links: [description]({editor_base}/?id=UUID)\n"
            f"When asked to open a file, call open_in_editor and include the returned URL."
        )

        result = "\n".join(parts)
        duration_ms = int((time.time() - start) * 1000)
        _log_tool_execution("read_prompt_context", {}, f"({len(result)} chars)", duration_ms)
        return result

    @mcp.tool()
    def log_feedback(
        feedback_type: str,
        message: str,
        query_context: str = "",
    ) -> str:
        """Log feedback for self-improvement analysis. Call at end of significant sessions.

        Args:
            feedback_type: 'positive', 'negative', or 'summary'
            message: Description of what worked well or what didn't
            query_context: Optional context about what was being discussed
        """
        start = time.time()
        try:
            from pkm_bridge.database import get_db, init_db
            from pkm_bridge.db_repository import QueryFeedbackRepository

            init_db()
            db = get_db()
            try:
                query_id = f"mcp-feedback-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                QueryFeedbackRepository.create(
                    db=db,
                    query_id=query_id,
                    session_id="mcp",
                    user_message=query_context or message,
                    had_rag_context=False,
                    rag_context_chars=0,
                    search_tools_used=[],
                    tool_error_count=0,
                    total_tool_calls=0,
                    explicit_feedback=(
                        feedback_type
                        if feedback_type in ("positive", "negative")
                        else None
                    ),
                    feedback_note=message,
                )
                duration_ms = int((time.time() - start) * 1000)
                _log_tool_execution("log_feedback", {"type": feedback_type}, "logged", duration_ms)
                return f"Feedback logged [{feedback_type}]: {message[:100]}"
            finally:
                db.close()
        except Exception as e:
            return f"Failed to log feedback: {e}"

    @mcp.tool()
    def open_in_editor(
        file_path: str,
        line: int | None = None,
        org_id: str | None = None,
    ) -> str:
        """Get a URL to open a file in the standalone PKM editor.

        Returns a clickable HTTPS link. Include it in your response for the user.

        Args:
            file_path: File path (e.g., 'org:journals/2026-02-28.org')
            line: Optional line number to jump to
            org_id: Optional org-id UUID (alternative to file_path)
        """
        editor_base = os.getenv("EDITOR_BASE_URL", "https://pkm.oberbrunner.com/editor")

        if org_id:
            url = f"{editor_base}/?id={org_id}"
        else:
            url = f"{editor_base}/?file={file_path}"
            if line:
                url += f"&line={line}"

        _log_tool_execution("open_in_editor", {"file_path": file_path, "line": line}, url, 0)
        return url

    # --- Schedule task ---

    @mcp.tool()
    def schedule_task(
        action: str,
        name: str | None = None,
        prompt: str | None = None,
        schedule_type: str | None = None,
        schedule_expr: str | None = None,
        enabled: bool | None = None,
        max_turns: int | None = None,
    ) -> str:
        """Manage scheduled tasks (cron jobs that run Claude with specific prompts).

        Args:
            action: 'list', 'create', 'update', 'delete', or 'run_now'
            name: Task name (required for create/update/delete/run_now)
            prompt: Task prompt (required for create)
            schedule_type: 'cron' or 'interval' (required for create)
            schedule_expr: Cron expression or interval spec (required for create)
            enabled: Enable/disable the task
            max_turns: Maximum turns for task execution
        """
        params: dict[str, Any] = {"action": action}
        if name is not None:
            params["name"] = name
        if prompt is not None:
            params["prompt"] = prompt
        if schedule_type is not None:
            params["schedule_type"] = schedule_type
        if schedule_expr is not None:
            params["schedule_expr"] = schedule_expr
        if enabled is not None:
            params["enabled"] = enabled
        if max_turns is not None:
            params["max_turns"] = max_turns
        return _execute_tool("schedule_task", params)
