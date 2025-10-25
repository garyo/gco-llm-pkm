"""Shell command execution tool."""

import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Set
from .base import BaseTool


class ExecuteShellTool(BaseTool):
    """Execute whitelisted shell commands in PKM directories."""

    def __init__(self, logger, allowed_commands: Set[str], org_dir: Path, logseq_dir: Path = None):
        """Initialize shell execution tool.

        Args:
            logger: Logger instance
            allowed_commands: Set of allowed command names
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.allowed_commands = allowed_commands
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir

    @property
    def name(self) -> str:
        return "execute_shell"

    @property
    def description(self) -> str:
        dirs_info = f"PRIMARY (org-mode): {self.org_dir}"
        if self.logseq_dir:
            dirs_info += f"\nSECONDARY (Logseq, read-only): {self.logseq_dir}"

        return f"""Execute a shell command with access to PKM files and tools.

Available tools:
- ripgrep (rg): fast PCRE2 search
- fd: better find replacement (faster, regex patterns, smart case; always prefer this over find)
- emacs: batch mode for org-ql, etc.
- cat/head/tail, git

Directories:
{dirs_info}

Security: Only whitelisted commands allowed: {', '.join(sorted(self.allowed_commands))}
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "working_dir": {"type": "string", "description": f"Working directory (defaults to {self.org_dir})"}
            },
            "required": ["command"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Execute a whitelisted shell command.

        Args:
            params: Dict with 'command' and optional 'working_dir'

        Returns:
            Command output or error message
        """
        command = params["command"]
        working_dir = params.get("working_dir") or str(self.org_dir)

        # Check if command is whitelisted
        cmd_binary = (command.split() or [""])[0]
        if cmd_binary not in self.allowed_commands:
            return f"❌ Command not allowed: {cmd_binary}\nAllowed: {', '.join(sorted(self.allowed_commands))}"

        self.logger.info(f"Executing: {command} (cwd: {working_dir})")

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
            self.logger.debug(f"Shell command completed in {elapsed:.3f}s")

            output = result.stdout or ""
            if result.stderr:
                self.logger.error(f"Shell command stderr: {result.stderr}")
                output += f"\n[stderr]: {result.stderr}"
            if result.returncode != 0:
                self.logger.error(f"Shell command failed with exit code {result.returncode}: {command}")
                output += f"\n[exit code: {result.returncode}]"

            if len(output) > 20000:
                output = output[:20000] + "\n\n... (output truncated)"

            return output if output else "[No output]"

        except subprocess.TimeoutExpired:
            error_msg = "❌ Command timed out after 60 seconds"
            self.logger.error(f"{error_msg}: {command}")
            return error_msg
        except Exception as e:
            error_msg = f"❌ Error executing command: {str(e)}"
            self.logger.error(f"{error_msg} (command: {command})")
            return error_msg
