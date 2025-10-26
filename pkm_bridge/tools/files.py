"""File listing tool."""

import datetime
from pathlib import Path
from typing import Dict, Any
from .base import BaseTool


class ListFilesTool(BaseTool):
    """List files in PKM directories with optional stats."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path|None = None):
        """Initialize file listing tool.

        Args:
            logger: Logger instance
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        dirs_info = f"PRIMARY (org-mode): {self.org_dir}"
        if self.logseq_dir:
            dirs_info += f"\nSECONDARY (Logseq, read-only): {self.logseq_dir}"
        return f"""List files in PKM directories. Directories:\n{dirs_info}"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern ('*.org', '**/*.org')"},
                "show_stats": {"type": "boolean", "description": "Show sizes & mtimes", "default": False},
                "directory": {"type": "string", "description": "org-mode (default), logseq, or both", "default": "org-mode"},
            }
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """List files matching pattern in specified directories.

        Args:
            params: Dict with 'pattern', 'show_stats', and 'directory'

        Returns:
            List of matching files or error message
        """
        pattern = params.get("pattern", "*")
        show_stats = params.get("show_stats", False)
        directory = params.get("directory", "org-mode")

        try:
            def list_from_dir(base_dir: Path, dir_label: str):
                if "**" in pattern:
                    files = list(base_dir.rglob(pattern.replace("**", "*")))
                else:
                    files = list(base_dir.glob(pattern))

                # Hide dotfiles and .git
                files = [f for f in files if '.git' not in f.parts and not any(part.startswith('.') for part in f.parts)]
                if not files:
                    return []

                if show_stats:
                    files.sort(key=lambda f: f.stat().st_mtime if f.is_file() else 0, reverse=True)
                else:
                    files.sort()

                MAX_FILES = 100
                if len(files) > MAX_FILES:
                    files = files[:MAX_FILES]
                    truncated = True
                else:
                    truncated = False

                output = []
                for f in files:
                    rel_path = f.relative_to(base_dir)
                    if show_stats and f.is_file():
                        size = f.stat().st_size
                        size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                        mtime = f.stat().st_mtime
                        mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                        output.append(f"[{dir_label}] {rel_path} ({size_str}, modified {mtime_str})")
                    else:
                        output.append(f"[{dir_label}] {rel_path}")

                if truncated:
                    output.append(f"\n... (showing {MAX_FILES} files; truncated)")

                return output

            all_output = []

            if directory in ["org-mode", "both"]:
                all_output.extend(list_from_dir(self.org_dir, "org-mode"))

            if directory in ["logseq", "both"] and self.logseq_dir:
                all_output.extend(list_from_dir(self.logseq_dir, "logseq"))
            elif directory == "logseq" and not self.logseq_dir:
                error_msg = "Logseq directory not configured or does not exist"
                self.logger.error(error_msg)
                return error_msg

            if not all_output:
                return f"No files matching pattern: {pattern}"

            return "\n".join(all_output)

        except Exception as e:
            error_msg = f"Error listing files: {str(e)}"
            self.logger.error(f"{error_msg} (pattern: {pattern}, directory: {directory})")
            return error_msg
