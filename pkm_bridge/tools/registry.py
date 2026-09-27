"""Tool registry for managing and accessing tools."""

import logging
from typing import Any, Dict, List

from .base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for managing all available tools."""

    def __init__(self):
        """Initialize empty tool registry."""
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        """Register a tool.

        Args:
            tool: Tool instance to register
        """
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool:
        """Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance

        Raises:
            KeyError: If tool not found
        """
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        return self._tools[name]

    def execute_tool(
        self, name: str, params: Dict[str, Any], context: Dict[str, Any] = None
    ) -> str:
        """Execute a tool by name.

        Args:
            name: Tool name
            params: Tool parameters
            context: Optional execution context (e.g., session_id)

        Returns:
            Tool execution result
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"❌ Unknown tool: {name}. Available: {', '.join(self.list_tools())}"

        try:
            return tool.execute(params, context=context)
        except KeyError as e:
            # Tools read required params as params["x"]; say which one is missing
            key = e.args[0] if e.args else None
            if key in tool.input_schema.get("properties", {}) and key not in params:
                logger.warning(f"Tool {name} called without parameter {key!r}")
                return f"❌ Tool {name} failed: missing required parameter {key!r}"
            logger.error(f"Tool {name} failed: KeyError {key!r}", exc_info=True)
            return f"❌ Tool {name} failed: internal error (KeyError {key!r})"
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}", exc_info=True)
            return f"❌ Tool {name} failed: {type(e).__name__}: {e}"

    def get_anthropic_tools(self) -> List[Dict[str, Any]]:
        """Get all tools formatted for Anthropic API.

        Returns:
            List of tool definitions for Anthropic API
        """
        return [tool.to_anthropic_tool() for tool in self._tools.values()]

    def list_tools(self) -> List[str]:
        """Get list of registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())

    def __len__(self) -> int:
        """Get number of registered tools."""
        return len(self._tools)

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"ToolRegistry({len(self)} tools: {', '.join(self.list_tools())})"
