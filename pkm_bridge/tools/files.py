"""File listing and reading tools."""

import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..note_paths import recency
from .base import BaseTool

MAX_LISTED_FILES = 100

# Logseq's backup copies (also in the note dirs' .gitignore)
_BACKUP_DIRS = {"bak", "version-files"}


class ListFilesTool(BaseTool):
    """List files in PKM directories with optional stats."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path | None = None):
        """Initialize file listing tool.

        Args:
            logger: Logger instance
            org_dir: Primary notes directory
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
        dirs_info = f"org: = primary notes, .org and/or .md ({self.org_dir})"
        if self.logseq_dir:
            dirs_info += f"\nlogseq: = Logseq graphs, read-only ({self.logseq_dir})"
        return (
            "List files in the PKM directories, newest first (by the date in a "
            f"journal's filename, else modification time), at most {MAX_LISTED_FILES} "
            "per directory. Paths come back prefixed (org:journals/2026-09-27.md) as "
            f"read_note accepts. Directories:\n{dirs_info}"
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern relative to each directory; '**' matches any depth. "
                        "Notes are .md or .org ('*', '**/*.md', 'journals/2026-09-*', "
                        "'**/*sciatica*'). An 'org:' or 'logseq:' prefix selects the directory."
                    ),
                },
                "show_stats": {
                    "type": "boolean",
                    "description": "Show sizes & mtimes",
                    "default": False,
                },
                "directory": {
                    "type": "string",
                    "description": (
                        "Which directory: 'both' (default), 'org-mode' "
                        "(the primary notes dir, holding .org and/or .md), or 'logseq'"
                    ),
                    "default": "both",
                },
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """List files matching pattern in specified directories.

        Args:
            params: Dict with 'pattern', 'show_stats', and 'directory'

        Returns:
            List of matching files or error message
        """
        pattern = params.get("pattern") or "*"
        show_stats = params.get("show_stats", False)
        directory = params.get("directory", "both")

        prefix, sep, rest = pattern.partition(":")
        if sep and prefix in ("org", "logseq"):
            pattern = rest or "*"
            directory = "org-mode" if prefix == "org" else "logseq"

        if directory == "logseq" and not self.logseq_dir:
            error_msg = "Logseq directory not configured or does not exist"
            self.logger.error(error_msg)
            return error_msg

        roots = []
        if directory in ("org-mode", "both"):
            roots.append(("org", Path(self.org_dir)))
        if directory in ("logseq", "both") and self.logseq_dir:
            roots.append(("logseq", Path(self.logseq_dir)))

        try:
            all_output = []
            for label, base_dir in roots:
                all_output.extend(self._list_dir(base_dir, label, pattern, show_stats))
        except (NotImplementedError, ValueError) as e:
            # pathlib rejects absolute and malformed patterns
            return f"Invalid pattern '{pattern}': {e}. Use a pattern relative to the directory."
        except Exception as e:
            error_msg = f"Error listing files: {str(e)}"
            self.logger.error(f"{error_msg} (pattern: {pattern}, directory: {directory})")
            return error_msg

        if not all_output:
            return f"No files matching pattern: {pattern}"
        return "\n".join(all_output)

    @staticmethod
    def _list_dir(base_dir: Path, label: str, pattern: str, show_stats: bool) -> List[str]:
        """Matches in one directory, newest first, capped at MAX_LISTED_FILES."""
        if not base_dir.is_dir():
            return []
        matches = []
        for f in base_dir.glob(pattern):
            rel = f.relative_to(base_dir)
            # Hide dotfiles/dirs (.git, .pkm, sync temp files), backups, and
            # anything a '..' pattern reaches outside the directory.
            if any(part.startswith(".") or part in _BACKUP_DIRS for part in rel.parts):
                continue
            matches.append((recency(f), f, rel))
        matches.sort(key=lambda m: m[0], reverse=True)

        output = []
        for _, f, rel in matches[:MAX_LISTED_FILES]:
            line = f"{label}:{rel}{'/' if f.is_dir() else ''}"
            if show_stats and f.is_file():
                stat = f.stat()
                size = stat.st_size
                size_str = f"{size:,} bytes" if size < 1024 else f"{size / 1024:.1f} KB"
                mtime_str = datetime.datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                line += f" ({size_str}, modified {mtime_str})"
            output.append(line)

        if len(matches) > MAX_LISTED_FILES:
            output.append(
                f"... showing {MAX_LISTED_FILES} of {len(matches)} in {label}: "
                "(newest first); narrow the pattern to see older ones"
            )
        return output


class ReadNoteTool(BaseTool):
    """Read a note file's content (read-only, path-contained)."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path | None = None):
        super().__init__(logger)
        from ..file_editor import FileEditor

        self.editor = FileEditor(logger, str(org_dir), str(logseq_dir) if logseq_dir else None)

    @property
    def name(self) -> str:
        return "read_note"

    @property
    def description(self) -> str:
        return (
            "Read a note file. Accepts prefixed paths ('org:notes.org', "
            "'org:journals/2026-02-13.md', 'logseq:pages/Foo.md') or plain "
            "relative paths. Large files are "
            "truncated with an offset hint for paging."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the note file"},
                "offset": {
                    "type": "integer",
                    "description": "Character offset for paging",
                    "default": 0,
                },
            },
            "required": ["filepath"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        filepath = params.get("filepath", "").strip()
        if not filepath:
            return "❌ Error: No filepath provided"
        try:
            result = self.editor.read_file(filepath, offset=int(params.get("offset", 0)))
            return f"[{result['path']}]\n{result['content']}"
        except ValueError as e:
            return f"❌ Error: {e}"
