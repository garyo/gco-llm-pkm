"""Base class for all tools."""

from abc import ABC, abstractmethod
from typing import Dict, Any
import logging


class BaseTool(ABC):
    """Abstract base class for tools exposed to the Claude API.

    Subclasses must implement:
    - name: Tool name
    - description: Tool description for Claude
    - input_schema: JSON schema for tool parameters
    - execute(): Tool execution logic
    """

    def __init__(self, logger: logging.Logger):
        """Initialize tool with logger.

        Args:
            logger: Logger instance for this tool
        """
        self.logger = logger

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name as exposed to Claude API."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for Claude API."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Dict[str, Any]:
        """JSON schema for tool input parameters."""
        pass

    @abstractmethod
    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute the tool with given parameters.

        Args:
            params: Tool parameters matching input_schema
            context: Optional execution context (e.g., session_id for SSE events)

        Returns:
            Tool execution result as string

        Raises:
            Exception: On tool execution failure
        """
        pass

    def to_anthropic_tool(self) -> Dict[str, Any]:
        """Convert tool to Anthropic API tool definition.

        Returns:
            Tool definition dict for Anthropic API
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema
        }

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"{self.__class__.__name__}(name={self.name})"
