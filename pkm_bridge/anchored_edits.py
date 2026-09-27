"""Find/replace edits anchored to exact text that must occur exactly once.

Shared by the note-organization proposals and the edit_note tool. An anchor
that is missing or ambiguous is refused rather than guessed at, so an edit
made against an older view of a note can never land in the wrong place.
"""

from typing import Any, List, Mapping, Sequence

FIND_SCHEMA = {
    "type": "string",
    "description": "Exact text currently in the file (must occur exactly once)",
}


class AnchorError(ValueError):
    """One or more edits could not be anchored; `problems` lists each."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def anchor_problem(content: str, find: str) -> str | None:
    """Why `find` cannot anchor an edit in `content`, or None if it can."""
    count = content.count(find)
    if count == 0:
        return "anchor text not found in file"
    if count > 1:
        return f"anchor text occurs {count} times (must be unique)"
    return None


def edit_problem(edit: Mapping[str, Any]) -> str | None:
    """Structural problems with a single {find, replace} edit, or None."""
    find = edit.get("find")
    replace = edit.get("replace")
    if not isinstance(find, str) or not find or not isinstance(replace, str):
        return "needs non-empty 'find' and a 'replace' string"
    if find == replace:
        return "'find' and 'replace' are identical"
    return None


def apply_edits(content: str, edits: Sequence[Mapping[str, Any]]) -> str:
    """Apply every edit to `content`, or none of them.

    Each anchor is located in the original text, so one edit's replacement
    cannot create or destroy another edit's anchor. Anchors must not overlap.

    Raises:
        AnchorError: listing every edit that is malformed, unanchored or overlapping.
    """
    problems: List[str] = []
    spans: List[tuple[int, int, str, int]] = []
    for i, edit in enumerate(edits, 1):
        problem = edit_problem(edit) or anchor_problem(content, edit["find"])
        if problem:
            problems.append(f"edit {i}: {problem}")
            continue
        start = content.index(edit["find"])
        spans.append((start, start + len(edit["find"]), edit["replace"], i))

    spans.sort()
    for (_, prev_end, _, prev_i), (start, _, _, i) in zip(spans, spans[1:]):
        if start < prev_end:
            problems.append(f"edit {i}: anchor overlaps edit {prev_i}")
    if problems:
        raise AnchorError(problems)

    for start, end, replace, _ in reversed(spans):
        content = content[:start] + replace + content[end:]
    return content
