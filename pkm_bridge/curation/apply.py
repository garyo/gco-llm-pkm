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
instead of mis-applying). Edits may share a file: each file's edits are
located in one snapshot, must not overlap, and are applied together as one
write. Writes go through FileEditor (atomic temp-file + rename), based on the
snapshot's hash, so a concurrent change elsewhere in the file is merged in.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..file_editor import ConflictError, FileEditor

VALID_KINDS = ("add_links", "new_page", "insight")


@dataclass
class _FileWrite:
    """One file's edits applied in memory, ready to write."""

    path: str  # canonical path after pages/ fallback
    base_hash: str  # hash of the snapshot the edits were located in
    content: str


def _plan_edits(
    edits: List[Dict[str, Any]], editor: FileEditor
) -> Tuple[List[_FileWrite], List[str]]:
    """Locate every edit's anchor and build each target file's new content.

    Returns (writes, problems); writes are only meaningful when problems is empty.
    """
    problems: List[str] = []
    reads: Dict[str, Dict[str, Any]] = {}  # requested path -> read_file result
    snapshots: Dict[str, Dict[str, Any]] = {}  # canonical path -> read_file result
    spans: Dict[str, List[Tuple[int, int, str, str]]] = {}  # path -> (start, end, repl, label)

    for i, edit in enumerate(edits):
        file = edit.get("file", "")
        find = edit.get("find", "")
        replace = edit.get("replace")
        label = f"edit {i + 1} ({file or 'no file'})"

        if not file or not find or replace is None:
            problems.append(f"{label}: needs 'file', 'find', and 'replace'")
            continue
        if find == replace:
            problems.append(f"{label}: 'find' and 'replace' are identical")
            continue
        try:
            if file not in reads:
                reads[file] = editor.read_file(file, max_chars=None)
        except ValueError as e:
            problems.append(f"{label}: {e}")
            continue
        # One snapshot per file, even when edits name it differently (pages/ fallback).
        path = reads[file]["path"]
        content = snapshots.setdefault(path, reads[file])["content"]
        count = content.count(find)
        if count == 0:
            problems.append(f"{label}: anchor text not found in file")
        elif count > 1:
            problems.append(f"{label}: anchor text occurs {count} times (must be unique)")
        else:
            start = content.index(find)
            spans.setdefault(path, []).append((start, start + len(find), replace, label))

    writes: List[_FileWrite] = []
    for path, file_spans in spans.items():
        file_spans.sort()
        for (_, prev_end, _, prev_label), (start, _, _, label) in zip(file_spans, file_spans[1:]):
            if start < prev_end:
                problems.append(f"{prev_label} and {label} overlap")
        content = snapshots[path]["content"]
        for start, end, replace, _ in reversed(file_spans):
            content = content[:start] + replace + content[end:]
        writes.append(_FileWrite(path, snapshots[path]["hash"], content))

    return writes, problems


def validate_payload(kind: str, payload: Dict[str, Any], editor: FileEditor) -> List[str]:
    """Check a proposal payload against the current state of the files.

    Returns a list of problems; empty means the payload is applicable right now.
    """
    return _check_payload(kind, payload, editor)[1]


def _check_payload(
    kind: str, payload: Dict[str, Any], editor: FileEditor
) -> Tuple[List[_FileWrite], List[str]]:
    """validate_payload, also returning the file writes that would apply it."""
    problems: List[str] = []

    if kind not in VALID_KINDS:
        return [], [f"Unknown proposal kind: '{kind}'"]

    edits = payload.get("edits", [])
    if not isinstance(edits, list):
        return [], ["'edits' must be a list"]

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

    writes, edit_problems = _plan_edits(edits, editor)
    return writes, problems + edit_problems


def apply_proposal(
    kind: str,
    payload: Dict[str, Any],
    editor: FileEditor,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """Apply a validated proposal to the note files.

    Every file is checked and its new content built before anything is
    written. Returns {"status": "applied", "written": [paths]} on success, or
    {"status": "stale", "problems": [...]} when anchors no longer match
    (nothing written), or {"status": "conflict", "written": [...],
    "problems": [...]} when a file changed during apply in a way that can't be
    merged (earlier writes in the batch may have landed — reported honestly).
    """
    writes, problems = _check_payload(kind, payload, editor)
    if problems:
        return {"status": "stale", "problems": problems}

    written: List[str] = []

    if kind == "new_page":
        page = payload["page"]
        result = editor.write_file(page["file"], page["content"], create_only=True)
        if result["status"] == "exists":
            return {"status": "stale", "problems": [f"Page already exists: {page['file']}"]}
        written.append(result["path"])

    for write in writes:
        try:
            editor.write_file(write.path, write.content, base_hash=write.base_hash)
        except ConflictError as e:
            logger.warning(f"Proposal apply conflict on {write.path}: {e}")
            return {
                "status": "conflict",
                "written": written,
                "problems": [f"{write.path} changed during apply: {e}"],
            }
        written.append(write.path)

    logger.info(f"Applied {kind} proposal: wrote {len(written)} file(s)")
    return {"status": "applied", "written": written}
