"""Note-searching tool."""

import time
from pathlib import Path
from typing import Dict, Any
from .base import BaseTool
from .utils import run_command_with_error_handling


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
        self.limit = 200000  # Increased from 100k to 200k

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
        """Execute search with date-sorted results.

        Args:
            params: Dict with args

        Returns:
            Search results (newest files first) or error message
        """
        pattern = params["pattern"]
        context = params.get("context", self.context)
        limit = params.get("limit", self.limit)
        if limit < self.limit:
            limit = self.limit  # Allow client to add more, but not less.
        org_dir = params.get("org_dir", self.org_dir)
        logseq_dir = params.get("logseq_dir", self.logseq_dir)

        self.logger.info(f"Searching for \"{pattern}\", context={context}, limit={limit}")

        try:
            start_time = time.time()
            output_parts = []
            total_size = 0

            # Search Logseq journals (newest first via --sortr path on date-named files)
            if logseq_dir:
                logseq_journals = [f"{logseq_dir}/Personal/journals", f"{logseq_dir}/DSS/journals"]
                logseq_cmd = ["rg", "-i", "--sortr", "path", f"-C{context}", pattern, *logseq_journals]
                self.logger.debug(f"Searching Logseq journals: {logseq_cmd}")

                stdout, stderr, returncode = run_command_with_error_handling(
                    logseq_cmd,
                    timeout=15,
                    logger=self.logger
                )

                self.logger.debug(f"... returns {len(stdout)}b, exit code {returncode}")

                # Handle errors
                if returncode > 1:  # 0=matches, 1=no matches, 2+=error
                    error_msg = f"⚠️ Logseq search error (exit {returncode})"
                    if stderr:
                        error_msg += f": {stderr}"
                    output_parts.append(error_msg + "\n")
                    self.logger.error(error_msg)

                if stdout:
                    result_size = len(stdout)
                    if total_size + result_size > limit:
                        # Truncate to fit
                        remaining = limit - total_size
                        output_parts.append(f"⚠️ LOGSEQ RESULTS TRUNCATED at {limit} chars\n")
                        if remaining > 0:
                            output_parts.append(stdout[:remaining])
                        total_size = limit
                    else:
                        output_parts.append(stdout)
                        total_size += result_size

            # Search org-mode (chronological order - oldest first in file)
            # TODO: Reverse org results in future iteration
            if total_size < limit and org_dir:
                remaining_space = limit - total_size
                org_cmd = ["rg", "-i", f"-C{context}", pattern, str(org_dir)]
                self.logger.debug(f"Searching org-mode: {org_cmd}")

                stdout, stderr, returncode = run_command_with_error_handling(
                    org_cmd,
                    timeout=15,
                    logger=self.logger
                )

                # Handle errors
                if returncode > 1:  # 0=matches, 1=no matches, 2+=error
                    error_msg = f"⚠️ Org-mode search error (exit {returncode})"
                    if stderr:
                        error_msg += f": {stderr}"
                    output_parts.append(error_msg + "\n")
                    self.logger.error(error_msg)

                if stdout:
                    result_size = len(stdout)
                    if result_size > remaining_space:
                        output_parts.append(f"\n⚠️ ORG RESULTS TRUNCATED (chronological, oldest first)\n")
                        output_parts.append(stdout[:remaining_space])
                        total_size = limit
                    else:
                        if output_parts:
                            output_parts.append("\n--- ORG-MODE RESULTS (chronological, oldest first) ---\n")
                        output_parts.append(stdout)
                        total_size += result_size

            elapsed = time.time() - start_time
            self.logger.debug(f"Search completed in {elapsed:.3f}s, {total_size} bytes")

            return ''.join(output_parts) if output_parts else f"[No matches found for pattern '{pattern}']"

        except Exception as e:
            error_msg = f"❌ Error executing search: {str(e)}"
            self.logger.error(f"{error_msg}")
            return error_msg
