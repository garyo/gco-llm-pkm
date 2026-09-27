"""MCP resources for PKM Bridge.

Exposes read-only resources that Claude can reference:
- pkm://prompt-context — core instructions plus the read_prompt_context content
- pkm://skills — listing of available skills
"""

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp_server.resources")


def register_resources(mcp: FastMCP):
    """Register MCP resources on the server."""

    @mcp.resource("pkm://prompt-context")
    def prompt_context_resource() -> str:
        """Full PKM context: core instructions, user context, rules, recent journals."""
        from mcp_server.tools import build_prompt_context

        return f"{mcp.instructions}\n\n{build_prompt_context()}"

    @mcp.resource("pkm://skills")
    def skills_resource() -> str:
        """Listing of all available PKM skills."""
        from mcp_server.tools import _execute_tool

        return _execute_tool("list_skills", {"tag": "", "search": ""})
