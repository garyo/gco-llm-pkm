"""Shell command execution tools."""

import subprocess
import time
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple
from .base import BaseTool


def validate_command(command: str, dangerous_patterns: List[str]) -> Tuple[bool, str]:
    """Validate command against blacklist of dangerous patterns.

    Args:
        command: Shell command to validate
        dangerous_patterns: List of regex patterns to block

    Returns:
        Tuple of (is_valid, error_message)
    """
    for pattern in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE | re.MULTILINE):
            return False, f"Command blocked by safety pattern: {pattern}"

    return True, ""


class ExecuteShellTool(BaseTool):
    """Execute shell commands and pipelines in PKM environment."""

    def __init__(self, logger, dangerous_patterns: List[str], org_dir: Path, logseq_dir: Path|None = None):
        """Initialize shell execution tool.

        Args:
            logger: Logger instance
            dangerous_patterns: List of regex patterns to block
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.dangerous_patterns = dangerous_patterns
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

        return f"""Execute shell commands, pipelines, or scripts in the PKM environment.

You can:
- Run any standard Unix commands (ripgrep, fd, emacs, git, sed, awk, etc.)
- Use pipes, redirects, and command substitution freely
- Chain commands with && or ||
- Write complex operations as shell scripts (use write_and_execute_script tool for clarity)

Available tools:
- ripgrep (rg): fast PCRE2 search
- fd: better find replacement (faster, regex patterns, smart case)
- emacs: batch mode for org-ql, etc.
- sed/awk: text processing
- Standard Unix tools: cat, head, tail, sort, uniq, etc.

FD (fast file find) useful args:
- Basic structure: fd [args] <pattern> <path...>
- Pattern is a regex, not glob
- --max-results N
- --type file: search for files only (not dirs)
- --extension .md --extension .org: search for those extensions only
- --changed-within date|duration (e.g.: 1d, 2w, or 2025-07-15)
  --changed-before date|duration
- --exec-batch <cmd>: execute cmd once with all files, using {{}} as placeholder

Ripgrep (rg) useful args:
- --iglob=GLOB: include or exclude (with !) files/dirs.
- --type=md and/or --type=org: search md and/or org files.
- -C/-B/-A <num>: include context around/before/after match
- --with-filename: print filename for each matching line (always use this, and --no-heading)
- --sortr=path: sort reverse by file path (gives most recent first for journals, but is slower)
- --files-with-matches: only show filenames containing matches.
- --ignore-case: search case-insensitively.
- --multiline: search across multiple lines (add --multiline-dotall to make "." match newlines)

Directories:
{dirs_info}

Security notes:
- Environment is Docker-isolated with access only to PKM files
- Dangerous patterns (rm -rf /, fork bombs, package installs) are blocked
- All commands are logged with stdout, stderr, and exit codes
- Real security comes from container isolation and git backups
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command or pipeline to execute"},
                "working_dir": {"type": "string", "description": f"Working directory (defaults to {self.org_dir})"}
            },
            "required": ["command"]
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute a shell command.

        Args:
            params: Dict with 'command' and optional 'working_dir'

        Returns:
            Command output with stderr and exit code information
        """
        command = params["command"]
        working_dir = params.get("working_dir") or str(self.org_dir)

        # Validate against blacklist
        is_valid, error = validate_command(command, self.dangerous_patterns)
        if not is_valid:
            self.logger.warning(f"[SHELL_BLOCKED] command={command[:200]}, reason={error}")
            return f"❌ {error}"

        # Log for audit
        self.logger.info(f"[SHELL_EXEC] cwd={working_dir}, command={command[:200]}")

        try:
            start_time = time.time()
            result = subprocess.run(
                command,
                shell=True,
                executable='/bin/bash',  # Use bash for brace expansion, process substitution, etc.
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            elapsed = time.time() - start_time

            # Build detailed output
            output_parts = []

            if result.stdout:
                output_parts.append(result.stdout.rstrip())

            if result.stderr:
                output_parts.append(f"\n[stderr]:\n{result.stderr.rstrip()}")

            if result.returncode != 0:
                output_parts.append(f"\n[exit code: {result.returncode}]")

            output = "\n".join(output_parts) if output_parts else "[No output]"

            # Log results
            self.logger.info(
                f"[SHELL_RESULT] elapsed={elapsed:.3f}s, "
                f"returncode={result.returncode}, "
                f"stdout_bytes={len(result.stdout or '')}, "
                f"stderr_bytes={len(result.stderr or '')}"
            )

            if result.returncode != 0:
                self.logger.warning(
                    f"[SHELL_ERROR] command={command[:100]}, "
                    f"returncode={result.returncode}, "
                    f"stdout={result.stdout[:500] if result.stdout else 'none'},"
                    f"stderr={result.stderr[:500] if result.stderr else 'none'}"
                )

            # Truncate if too long
            if len(output) > 20000:
                output = output[:20000] + "\n\n... (output truncated to 20000 chars)"

            return output

        except subprocess.TimeoutExpired:
            error_msg = "❌ Command timed out after 60 seconds"
            self.logger.error(f"[SHELL_TIMEOUT] command={command[:200]}")
            return error_msg
        except Exception as e:
            error_msg = f"❌ Error executing command: {str(e)}"
            self.logger.error(f"[SHELL_EXCEPTION] command={command[:200]}, error={str(e)}")
            return error_msg


class WriteAndExecuteScriptTool(BaseTool):
    """Write a shell script to /tmp and execute it."""

    def __init__(self, logger, dangerous_patterns: List[str], org_dir: Path, logseq_dir: Path|None = None):
        """Initialize script execution tool.

        Args:
            logger: Logger instance
            dangerous_patterns: List of regex patterns to block
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.dangerous_patterns = dangerous_patterns
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir

    @property
    def name(self) -> str:
        return "write_and_execute_script"

    @property
    def description(self) -> str:
        dirs_info = f"PRIMARY (org-mode): {self.org_dir}"
        if self.logseq_dir:
            dirs_info += f"\nSECONDARY (Logseq, read-only): {self.logseq_dir}"

        return f"""Write a shell script to /tmp and execute it.

Use this for multi-step operations that are clearer as a script than a single command.

Script features:
- Automatically gets shebang (#!/bin/bash)
- Runs with 'set -euo pipefail' (exit on error, undefined vars, pipe failures)
- Errors will stop execution immediately due to -e flag
- Written to /tmp/script-<timestamp>.sh for debugging

Directories:
{dirs_info}

Security notes:
- Same dangerous pattern blocking as execute_shell
- Script content is fully logged for audit
- All output (stdout/stderr) and exit codes are captured
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "script_content": {
                    "type": "string",
                    "description": "Shell script content (without shebang - added automatically)"
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this script does (for audit log)"
                },
                "working_dir": {
                    "type": "string",
                    "description": f"Working directory for script execution (defaults to {self.org_dir})"
                }
            },
            "required": ["script_content", "description"]
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Write and execute a shell script.

        Args:
            params: Dict with 'script_content', 'description', and optional 'working_dir'

        Returns:
            Script output with stderr and exit code information
        """
        script_content = params["script_content"]
        description = params["description"]
        working_dir = params.get("working_dir") or str(self.org_dir)

        # Validate against blacklist
        is_valid, error = validate_command(script_content, self.dangerous_patterns)
        if not is_valid:
            self.logger.warning(
                f"[SCRIPT_BLOCKED] description={description}, reason={error}"
            )
            return f"❌ {error}"

        # Create script file
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        script_path = f"/tmp/script-{timestamp}.sh"

        # Add shebang and safety options
        full_script = "#!/bin/bash\n"
        full_script += "set -euo pipefail  # Exit on error, undefined vars, pipe failures\n\n"
        full_script += script_content

        # Write script
        try:
            with open(script_path, 'w') as f:
                f.write(full_script)
            Path(script_path).chmod(0o755)
        except Exception as e:
            error_msg = f"❌ Error writing script: {str(e)}"
            self.logger.error(f"[SCRIPT_WRITE_ERROR] path={script_path}, error={str(e)}")
            return error_msg

        # Log for audit
        self.logger.info(
            f"[SCRIPT_EXEC] description={description}, "
            f"path={script_path}, cwd={working_dir}"
        )
        self.logger.info(f"[SCRIPT_CONTENT]\n{full_script}")

        # Execute script
        try:
            start_time = time.time()
            result = subprocess.run(
                [script_path],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=120  # Longer timeout for scripts
            )
            elapsed = time.time() - start_time

            # Build detailed output
            output_parts = [f"Script: {script_path}"]

            if result.stdout:
                output_parts.append(f"\n[stdout]:\n{result.stdout.rstrip()}")

            if result.stderr:
                output_parts.append(f"\n[stderr]:\n{result.stderr.rstrip()}")

            output_parts.append(f"\n[exit code: {result.returncode}]")
            output_parts.append(f"[elapsed: {elapsed:.3f}s]")

            output = "\n".join(output_parts)

            # Log results
            self.logger.info(
                f"[SCRIPT_RESULT] path={script_path}, elapsed={elapsed:.3f}s, "
                f"returncode={result.returncode}, "
                f"stdout_bytes={len(result.stdout or '')}, "
                f"stderr_bytes={len(result.stderr or '')}"
            )

            if result.returncode != 0:
                self.logger.warning(
                    f"[SCRIPT_ERROR] path={script_path}, "
                    f"returncode={result.returncode}, "
                    f"stdout={result.stdout[:500] if result.stdout else 'none'},"
                    f"stderr={result.stderr[:500] if result.stderr else 'none'}"
                )

            # Truncate if too long
            if len(output) > 20000:
                output = output[:20000] + "\n\n... (output truncated to 20000 chars)"

            return output

        except subprocess.TimeoutExpired:
            error_msg = f"❌ Script timed out after 120 seconds: {script_path}"
            self.logger.error(f"[SCRIPT_TIMEOUT] path={script_path}")
            return error_msg
        except Exception as e:
            error_msg = f"❌ Error executing script: {str(e)}"
            self.logger.error(f"[SCRIPT_EXCEPTION] path={script_path}, error={str(e)}")
            return error_msg
