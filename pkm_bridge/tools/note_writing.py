"""Note-writing tools: append to a journal, edit or create a note.

Both write through FileEditor with the hash of the text they read, so a
change made meanwhile in the editor, in Emacs or via Syncthing is three-way
merged rather than overwritten. Unlike shell writes, the result is also
recorded in the version store the editor merges against.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..anchored_edits import FIND_SCHEMA, AnchorError, apply_edits
from ..file_editor import ConflictError, FileEditor
from ..journal import append_to_section, journal_rel_path, journal_template
from .base import BaseTool

# A write whose base changed on the same lines (two appends at the end of a
# file, say) is redone on the fresh text; this bounds how often.
MAX_ATTEMPTS = 3


def rewrite_note(editor: FileEditor, path: str, transform: Callable[[str], str]) -> Dict[str, Any]:
    """Read `path`, write back `transform(content)`, based on the text read.

    `transform` is re-run on the current text if the file changes in a way
    that cannot be merged, so it must be computed from its argument alone.

    Raises:
        ValueError: bad path or missing file (or whatever `transform` raises).
        ConflictError: the file kept changing underneath every attempt.
    """
    attempts_left = MAX_ATTEMPTS
    while True:
        current = editor.read_file(path, max_chars=None)
        new_content = transform(current["content"])
        try:
            return editor.write_file(current["path"], new_content, base_hash=current["hash"])
        except ConflictError:
            attempts_left -= 1
            if not attempts_left:
                raise


def _prefixed(path: str) -> str:
    """Default unprefixed paths to ORG, where current notes live."""
    return path if ":" in path else f"org:{path}"


class _NoteWriterBase(BaseTool):
    def __init__(self, logger, org_dir: Path, logseq_dir: Optional[Path] = None):
        super().__init__(logger)
        self.editor = FileEditor(logger, str(org_dir), str(logseq_dir) if logseq_dir else None)


class JournalAppendTool(_NoteWriterBase):
    """Append text to a day's journal, under a heading if given."""

    @property
    def name(self) -> str:
        return "journal_append"

    @property
    def description(self) -> str:
        return (
            "Add Markdown text to the journal for a date (org:journals/YYYY-MM-DD.md), "
            "creating the journal if needed. With `heading`, the text goes at the end of "
            "that section, and a missing heading is added at the end of the file; without "
            "it, at the end of the file. Existing text is never changed. Use this for every "
            "journal addition rather than shell commands."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Journal date, YYYY-MM-DD"},
                "text": {
                    "type": "string",
                    "description": "Markdown lines to add, e.g. '- Rehearsed for the gig'",
                },
                "heading": {
                    "type": "string",
                    "description": (
                        "Section to add under, e.g. 'Music' (matches any level) or "
                        "'## Heartbeat (14:05)' (that exact level). Case-insensitive."
                    ),
                },
            },
            "required": ["date", "text"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        date_str = str(params.get("date", "")).strip()
        text = params.get("text") or ""
        heading = (params.get("heading") or "").strip() or None
        if not text.strip():
            return "❌ Error: text is empty"
        if heading and not heading.strip("# "):
            return "❌ Error: heading has no title"
        try:
            path = f"org:{journal_rel_path(date_str)}"
        except ValueError:
            return f"❌ Error: date must be YYYY-MM-DD, got '{date_str}'"

        try:
            created = self.editor.write_file(path, journal_template(date_str), create_only=True)
            result = rewrite_note(
                self.editor, path, lambda content: append_to_section(content, text, heading)
            )
        except ConflictError as e:
            return f"❌ {path} kept changing while appending; nothing written ({e})"
        except ValueError as e:
            return f"❌ Error: {e}"

        where = f" under '{heading}'" if heading else ""
        new = " (created the journal)" if created["status"] == "saved" else ""
        return f"✅ Appended to {result['path']}{where}{new}:\n{text.strip()}"


class EditNoteTool(_NoteWriterBase):
    """Apply anchored find/replace edits to a note, or create a new one."""

    @property
    def name(self) -> str:
        return "edit_note"

    @property
    def description(self) -> str:
        return (
            "Change a note with exact find/replace edits, or create a new note. Read the "
            "note first: each `find` must quote text that occurs exactly once in it. All "
            "edits are checked before anything is written, and none are applied if any "
            "fails. Changes made to the file meanwhile are merged, not overwritten. To create "
            "a note, pass `content` and no edits (refused if the file exists). For journal "
            "additions, use journal_append. Prefer this to shell commands for any note change."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Note path, e.g. 'org:pages/travel.md' (default prefix org:)",
                },
                "edits": {
                    "type": "array",
                    "description": "Edits to an existing note, all applied or none",
                    "items": {
                        "type": "object",
                        "properties": {
                            "find": FIND_SCHEMA,
                            "replace": {
                                "type": "string",
                                "description": "Replacement text ('' deletes)",
                            },
                        },
                        "required": ["find", "replace"],
                    },
                },
                "content": {
                    "type": "string",
                    "description": "Full Markdown content for a NEW note only",
                },
            },
            "required": ["path"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        path = _prefixed(str(params.get("path", "")).strip())
        edits = params.get("edits") or []
        content = params.get("content")
        if path.endswith(":"):
            return "❌ Error: path is empty"
        if bool(edits) == (content is not None):
            return "❌ Error: pass either edits (to change a note) or content (to create one)"

        try:
            if content is not None:
                return self._create(path, content)
            result = rewrite_note(self.editor, path, lambda text: apply_edits(text, edits))
        except AnchorError as e:
            problems = "\n".join(f"- {p}" for p in e.problems)
            return (
                f"❌ No changes made to {path}:\n{problems}\n"
                "Re-read the note and quote its current text exactly."
            )
        except ConflictError as e:
            return f"❌ {path} kept changing while editing; nothing written ({e})"
        except ValueError as e:
            return f"❌ Error: {e}"

        merged = (
            " — merged with changes made since it was read" if result["status"] == "merged" else ""
        )
        return f"✅ Edited {result['path']} ({len(edits)} edit(s)){merged}"

    def _create(self, path: str, content: str) -> str:
        if not path.endswith(".md"):
            return f"❌ Error: new notes are Markdown; '{path}' must end in .md"
        result = self.editor.write_file(path, content, create_only=True)
        if result["status"] == "exists":
            return f"❌ {result['path']} already exists; read it and pass edits to change it"
        return f"✅ Created {result['path']} ({result['size']} bytes)"
