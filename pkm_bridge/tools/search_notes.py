"""Note-searching tool."""

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..note_paths import RG_NOTE_FILTER, is_journal, note_roots, recency
from ..queries import query_result_lines
from .base import BaseTool
from .utils import run_command_with_error_handling

# One file's worth of output: (sort key, prefixed path, rg lines for that file)
_Hit = Tuple[tuple, str, str]
# rg -n starts each line with its number: 'N:match' or 'N-context'
_RG_NUMBER = re.compile(r"\d+")
_RG_MATCH = re.compile(r"\d+:")


def _line_index(rg_line: str) -> int:
    """0-based index of the note line that a line of rg -n output shows, or -1."""
    m = _RG_NUMBER.match(rg_line)
    return int(m[0]) - 1 if m else -1


class SearchNotesTool(BaseTool):
    """Search all notes in PKM directories."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path | None = None):
        """Initialize search_notes tool.

        Args:
            logger: Logger instance
            org_dir: Primary notes directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir
        self.context = 3
        self.default_limit = 10000
        self.max_limit = 200000

    @property
    def name(self) -> str:
        return "search_notes"

    @property
    def description(self) -> str:
        dirs_info = f"org: = primary notes ({self.org_dir})"
        if self.logseq_dir:
            dirs_info += f"\nlogseq: = older Logseq graphs ({self.logseq_dir})"

        return f"""Case-insensitive regex search over all notes, with context lines.
Order: org journals newest first, then other org notes (most recently modified
first), then Logseq the same way. Output is grouped per file under a prefixed
path (org:journals/2026-09-27.md) that read_note accepts; lines are
'N:match' or 'N-context'.
For broad terms use files_only=true to get just the matching files with
match counts, then read or search the relevant ones.

Directories:
{dirs_info}
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "context": {
                    "type": "number",
                    "default": 3,
                    "description": "Lines of context to return on each side of each match",
                },
                "limit": {
                    "type": "number",
                    "default": 10000,
                    "description": "Approx character limit of returned results (max 200000)",
                },
                "files_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "List matching files with match counts instead of lines",
                },
            },
            "required": ["pattern"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Search every note directory and return hits newest first.

        Args:
            params: Dict with pattern, and optional context, limit, files_only

        Returns:
            Search results or error message
        """
        pattern = params["pattern"]
        context_lines = int(params.get("context", self.context))
        limit = min(max(int(params.get("limit", self.default_limit)), 100), self.max_limit)
        files_only = bool(params.get("files_only", False))
        org_dir = params.get("org_dir", self.org_dir)
        logseq_dir = params.get("logseq_dir", self.logseq_dir)

        self.logger.info(
            f'Searching for "{pattern}", context={context_lines}, limit={limit}, '
            f"files_only={files_only}"
        )
        start_time = time.time()

        hits: List[_Hit] = []
        errors: List[str] = []
        for rank, (prefix, root) in enumerate(note_roots(org_dir, logseq_dir)):
            if not root.is_dir():
                continue
            # Run from the root so rg reports short relative paths
            cmd = ["rg", "-i", *RG_NOTE_FILTER]
            if files_only:
                cmd.append("--count-matches")
            else:
                cmd += ["--heading", "-n", f"-C{context_lines}"]
            cmd += ["-e", pattern, "."]
            stdout, stderr, returncode = run_command_with_error_handling(
                cmd, timeout=15, logger=self.logger, cwd=root
            )
            # 1 = no matches; an invalid regex fails identically in every root
            if returncode not in (0, 1) and not any(stderr.strip() in e for e in errors):
                errors.append(f"⚠️ rg error (exit {returncode}) in {prefix}: {stderr.strip()}")
            for rel, body in self._parse(stdout, files_only):
                body = self._drop_query_results(pattern, root / rel, body, files_only)
                if body:
                    key = (rank, not is_journal(rel), -recency(root / rel))
                    hits.append((key, f"{prefix}:{rel}", body))

        hits.sort(key=lambda h: h[0])
        output = self._format(hits, files_only, limit)
        self.logger.debug(
            f"Search completed in {time.time() - start_time:.3f}s: "
            f"{len(hits)} files, {len(output)} chars"
        )

        if errors:
            output = "\n".join(errors) + ("\n\n" + output if output else "")
        return output or f"[No matches found for pattern '{pattern}']"

    def _drop_query_results(self, pattern: str, path: Path, body: str, files_only: bool) -> str:
        """`body` without the matches in query-block results, which copy other notes whose
        own matches are listed already; "" if none is left."""
        try:
            copied = query_result_lines(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return body
        if not copied:
            return body

        if files_only:
            # -o puts each match on a numbered line of its own
            stdout, _, _ = run_command_with_error_handling(
                ["rg", "-i", "-n", "-o", "-e", pattern, str(path)], logger=self.logger
            )
            count = sum(_line_index(line) not in copied for line in stdout.splitlines())
            return str(count) if count else ""

        groups = []
        for group in body.split("\n--\n"):
            lines = [line for line in group.split("\n") if _line_index(line) not in copied]
            if any(_RG_MATCH.match(line) for line in lines):
                groups.append("\n".join(lines))
        return "\n--\n".join(groups)

    @staticmethod
    def _parse(stdout: str, files_only: bool) -> List[Tuple[str, str]]:
        """Split rg output into (relative path, body) per file."""
        results = []
        if files_only:
            for line in stdout.splitlines():
                path, _, count = line.rpartition(":")
                if path:
                    results.append((path.removeprefix("./"), count))
        else:
            # --heading output: a path line, then numbered lines; files are
            # separated by a blank line (file content lines are never empty,
            # since they always carry a line number).
            for block in stdout.strip("\n").split("\n\n"):
                path, _, body = block.partition("\n")
                if path:
                    results.append((path.removeprefix("./"), body))
        return results

    @staticmethod
    def _format(hits: List[_Hit], files_only: bool, limit: int) -> str:
        """Render hits in order, stopping at a file boundary once over limit."""
        if files_only:
            entries = [f"{path}: {count}" for _, path, count in hits]
            sep = "\n"
        else:
            entries = [f"{path}\n{body}" for _, path, body in hits]
            sep = "\n\n"

        shown: List[str] = []
        size = 0
        for entry in entries:
            if size + len(entry) > limit:
                if not shown:
                    shown.append(entry[:limit])
                break
            shown.append(entry)
            size += len(entry) + len(sep)

        text = sep.join(shown)
        if len(shown) < len(entries):
            hint = "" if files_only else " Use files_only=true to list them all, or narrow it."
            text = (
                f"⚠️ Showing {len(shown)} of {len(entries)} matching files "
                f"(limit {limit} chars).{hint}\n\n{text}"
            )
        return text
