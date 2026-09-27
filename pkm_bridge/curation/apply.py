"""Validate and apply note-organization proposals.

Payload shapes (stored in NoteProposal.payload):

add_links:
    {"edits": [{"file": "logseq:pages/Foo.md",
                "find": "exact text currently in the file",
                "replace": "same text with [[links]] added"}, ...]}

new_page:
    {"page": {"file": "logseq:pages/New Page.md", "content": "full draft"},
     "edits": [...]}   # backlinks and journal excisions, same shape as add_links

insight:
    {"edits": []}      # no file changes — a pure observation (connections,
                       # patterns, outliers, things worth looking into); the
                       # title/rationale carry the content, and "applying" it
                       # just marks it reviewed

Every edit is anchored to exact text that must occur exactly once in the
target file. Anchors are checked at proposal time (so the curator can't file
broken proposals) and re-checked at apply time (so a file edited in between —
locally, or via Syncthing from another machine — makes the proposal stale
instead of mis-applying). Writes go through FileEditor: atomic temp-file +
rename, three-way merged with anything that changed between our read and write.
"""

import logging
from typing import Any, Dict, List

from ..anchored_edits import AnchorError, apply_edits
from ..file_editor import ConflictError, FileEditor

VALID_KINDS = ("add_links", "new_page", "insight")


def validate_payload(kind: str, payload: Dict[str, Any], editor: FileEditor) -> List[str]:
    """Check a proposal payload against the current state of the files.

    Returns a list of problems; empty means the payload is applicable right now.
    """
    problems: List[str] = []

    if kind not in VALID_KINDS:
        return [f"Unknown proposal kind: '{kind}'"]

    edits = payload.get("edits", [])
    if not isinstance(edits, list):
        return ["'edits' must be a list"]

    if kind == "add_links" and not edits:
        problems.append("add_links proposal has no edits")

    if kind == "insight" and (edits or payload.get("page")):
        problems.append("insight proposals carry no file changes (use add_links/new_page)")

    if kind == "new_page":
        page = payload.get("page") or {}
        page_file = page.get("file", "")
        content = page.get("content", "")
        if not page_file or not content:
            problems.append("new_page proposal needs page.file and page.content")
        else:
            try:
                full_path = editor._resolve_prefixed_path(page_file)
                if full_path.exists():
                    problems.append(f"Page already exists: {page_file}")
            except ValueError as e:
                problems.append(f"Invalid page path '{page_file}': {e}")

    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for i, edit in enumerate(edits, 1):
        if not edit.get("file"):
            problems.append(f"edit {i}: needs 'file'")
        else:
            by_file.setdefault(edit["file"], []).append(edit)

    for file, file_edits in by_file.items():
        try:
            apply_edits(editor.read_file(file, max_chars=None)["content"], file_edits)
        except AnchorError as e:
            problems.extend(f"{file}: {p}" for p in e.problems)
        except ValueError as e:
            problems.append(f"{file}: {e}")

    return problems


def apply_proposal(
    kind: str,
    payload: Dict[str, Any],
    editor: FileEditor,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """Apply a validated proposal to the note files.

    Returns {"status": "applied", "written": [paths]} on success, or
    {"status": "stale", "problems": [...]} when anchors no longer match
    (nothing written), or {"status": "conflict", "written": [...],
    "problems": [...]} when a file changed between our read and write
    (earlier writes in the batch may have landed — reported honestly).
    """
    problems = validate_payload(kind, payload, editor)
    if problems:
        return {"status": "stale", "problems": problems}

    # Read each edit target once, up front, so the apply works from one
    # snapshot and a file with several edits is written once.
    snapshots: Dict[str, Dict[str, Any]] = {}
    for edit in payload.get("edits", []):
        if edit["file"] not in snapshots:
            snapshots[edit["file"]] = {
                **editor.read_file(edit["file"], max_chars=None),
                "edits": [],
            }
        snapshots[edit["file"]]["edits"].append(edit)

    written: List[str] = []

    if kind == "new_page":
        page = payload["page"]
        result = editor.write_file(page["file"], page["content"], create_only=True)
        if result["status"] == "exists":
            return {"status": "stale", "problems": [f"Page already exists: {page['file']}"]}
        written.append(result["path"])

    for snap in snapshots.values():
        new_content = apply_edits(snap["content"], snap["edits"])
        try:
            # snap["path"] is canonical after the pages/ fallback.
            editor.write_file(snap["path"], new_content, base_hash=snap["hash"])
        except ConflictError as e:
            logger.warning(f"Proposal apply conflict on {snap['path']}: {e}")
            return {
                "status": "conflict",
                "written": written,
                "problems": [f"{snap['path']} changed during apply: {e}"],
            }
        written.append(snap["path"])

    logger.info(f"Applied {kind} proposal: wrote {len(written)} file(s)")
    return {"status": "applied", "written": written}
