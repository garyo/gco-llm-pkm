"""Journal note addition tool."""

import json
import subprocess
from pathlib import Path
from typing import Dict, Any
from .base import BaseTool


class JournalNoteTool(BaseTool):
    """Add notes to today's journal entry using Emacs batch mode."""

    def __init__(self, logger, org_dir: Path):
        """Initialize journal note tool.

        Args:
            logger: Logger instance
            org_dir: Primary org-mode directory containing journals
        """
        super().__init__(logger)
        self.org_dir = org_dir
        self.journal_path = org_dir / "journals"

    @property
    def name(self) -> str:
        return "add_journal_note"

    @property
    def description(self) -> str:
        return f"""Add a note to today's journal entry in {self.journal_path}.

This tool uses Emacs batch mode to properly handle the hierarchical journal structure:
- Creates today's entry if it doesn't exist (Year > Month > Day)
- Adds note as a bullet point under today's heading

The note can contain any text including quotes, newlines, hashtags, and links.
Always use this tool instead of manually editing journal.org."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note content to add to today's journal entry"}
            },
            "required": ["note"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Add a note to today's journal entry.

        Args:
            params: Dict with 'note' content

        Returns:
            Success message or error
        """
        note = params["note"]

        # Use json.dumps for proper elisp string escaping (handles quotes, newlines, backslashes, etc.)
        # This produces a JSON string which has the same escaping rules as elisp strings
        # ensure_ascii=False preserves Unicode characters like emoji
        note_escaped = json.dumps("- " + note, ensure_ascii=False)

        # Construct the elisp script
        elisp_script = f"""(progn
    (add-to-list 'load-path "~/.config/emacs/lisp")
    (let ((default-directory (expand-file-name "~/.config/emacs/var/elpaca/builds")))
      (when (file-directory-p default-directory)
        (normal-top-level-add-subdirs-to-load-path)))
    (require 'init-org)
    (require 'gco-pkm)
    (let ((inhibit-file-locks t))
      (gco-pkm-journal-today) ; go to journal for today, creating if needed
      (insert {note_escaped})
      (save-buffer)
      (message "Added note to today's journal")))"""

        # Log the operation
        self.logger.info(f"Adding journal note: {note[:100]}{'...' if len(note) > 100 else ''}")
        self.logger.debug(f"Emacs batch command: emacs --batch --eval <elisp>")
        self.logger.debug(f"Elisp script:\n{elisp_script}")

        try:
            result = subprocess.run(
                ["emacs", "--batch", "--eval", elisp_script],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.org_dir)
            )

            if result.returncode != 0:
                self.logger.error(f"Emacs batch failed with exit code {result.returncode}")
                self.logger.error(f"Stderr: {result.stderr}")
                return f"❌ Failed to add journal note (exit code {result.returncode})\nStderr: {result.stderr}"

            if result.stderr:
                self.logger.warning(f"Emacs batch stderr: {result.stderr}")

            output = result.stdout.strip() if result.stdout else "Note added successfully"
            self.logger.info(f"Journal note added successfully")
            return output

        except subprocess.TimeoutExpired:
            error_msg = "❌ Emacs batch command timed out after 30 seconds"
            self.logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"❌ Error adding journal note: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return error_msg
