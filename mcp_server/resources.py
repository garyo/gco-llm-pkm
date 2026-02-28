"""MCP resources for PKM Bridge.

Exposes read-only resources that Claude can reference:
- pkm://prompt-context — assembled system prompt + rules + user profile
- pkm://skills — listing of available skills
"""

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp_server.resources")


def register_resources(mcp: FastMCP):
    """Register MCP resources on the server."""

    @mcp.resource("pkm://prompt-context")
    def prompt_context_resource() -> str:
        """Full PKM context: system prompt, rules, user profile, journals."""
        from mcp_server.tools import _get_config

        config = _get_config()
        parts: list[str] = []

        # System prompt
        system_prompt = config.get_system_prompt()
        parts.append(system_prompt)

        # Learned rules
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
            logger.debug(f"Could not load learned rules: {e}")

        return "\n".join(parts)

    @mcp.resource("pkm://skills")
    def skills_resource() -> str:
        """Listing of all available PKM skills."""
        from mcp_server.tools import _execute_tool

        return _execute_tool("list_skills", {"tag": "", "search": ""})
