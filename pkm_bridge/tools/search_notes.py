"""Note-searching tool."""

import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Set
from .base import BaseTool


class SearchNotesTool(BaseTool):
    """Search all notes in PKM directories."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path|None = None):
        """Initialize search_notes tool.

        Args:
            logger: Logger instance
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir
        self.context = 3
        self.limit = 50000

    @property
    def name(self) -> str:
        return "search_notes"

    @property
    def description(self) -> str:
        dirs_info = f"PRIMARY (org-mode): {self.org_dir}"
        if self.logseq_dir:
            dirs_info += f"\nSECONDARY (Logseq, read-only): {self.logseq_dir}"

        return f"""Search for a pattern in the PKM dirs.
pattern: regex pattern to search for
context: lines of context to return on each side
limit: approx size limit of returned string

Directories:
{dirs_info}
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "context": {"type": "number", "default": 3, "description": "Lines of context to return on each side of each match"},
                "limit": {"type": "number", "default": "10000", "description": "Approx length limit of returned strign"},
            },
            "required": ["pattern"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Execute a whitelisted shell command.

        Args:
            params: Dict with args

        Returns:
            Command output or error message
        """
        pattern = params["pattern"]
        context = params.get("context", self.context)
        limit = params.get("limit", self.limit)
        org_dir = params.get("org_dir", self.org_dir)
        logseq_dir = params.get("logseq_dir", self.logseq_dir)

        self.logger.info(f"Searching for \"{pattern}\", context={context}, limit={limit}")

        command = ["rg", "-i", f"-C{context}", pattern, org_dir, logseq_dir]
        try:
            start_time = time.time()
            self.logger.debug(f"Running command: {command}")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout = 15
            )
            elapsed = time.time() - start_time
            self.logger.debug(f"Shell command completed in {elapsed:.3f}s")

            output = result.stdout[:limit]

            if result.stderr:
                self.logger.error(f"Shell command stderr: {result.stderr}")
                output += f"\n[stderr]: {result.stderr}"
            if result.returncode != 0:
                self.logger.error(f"Search (rg) command failed with exit code {result.returncode}: {command}")
                output += f"\n[exit code: {result.returncode}]"

            return output if output else "[No output]"

        except subprocess.TimeoutExpired:
            error_msg = "❌ Command timed out"
            self.logger.error(f"{error_msg}: {command}")
            return error_msg
        except Exception as e:
            error_msg = f"❌ Error executing command: {str(e)}"
            self.logger.error(f"{error_msg} (command: {command})")
            return error_msg
