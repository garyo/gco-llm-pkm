"""Tool registry for managing and accessing tools."""

from typing import Dict, List, Any
from .base import BaseTool


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

    def execute_tool(self, name: str, params: Dict[str, Any]) -> str:
        """Execute a tool by name.

        Args:
            name: Tool name
            params: Tool parameters

        Returns:
            Tool execution result
        """
        try:
            tool = self.get_tool(name)
            return tool.execute(params)
        except KeyError as e:
            error_msg = f"❌ Unknown tool: {name}"
            # Log if tools have logger access
            return error_msg
        except Exception as e:
            error_msg = f"❌ Tool execution failed for {name}: {str(e)}"
            # Would log here if we had logger access
            return error_msg

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
