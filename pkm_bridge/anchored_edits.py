"""Find/replace edits anchored to exact text that must occur exactly once.

Shared by the note-organization proposals and the edit_note tool. An anchor
that is missing or ambiguous is refused rather than guessed at, so an edit
made against an older view of a note can never land in the wrong place.
Edits to a query block's results are refused too: the results are a copy that
the server regenerates, so the change would be lost.
"""

import re
from itertools import accumulate
from os.path import commonprefix
from typing import Any, List, Mapping, Sequence

from .queries import query_block_ranges

FIND_SCHEMA = {
    "type": "string",
    "description": "Exact text currently in the file (must occur exactly once)",
}

# The first line of an entry in a query block's results: `## [title](link)`,
# or `- [title](link)` with show:titles.
_ENTRY_RE = re.compile(r"(#{1,6} |- )\[.*\]\(([^)\s]+)\)\s*$")


class AnchorError(ValueError):
    """One or more edits could not be anchored; `problems` lists each."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class QueryBlockError(AnchorError):
    """One or more edits would change a query block's results."""


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
        QueryBlockError: listing every edit that would change a query block's results.
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

    problems = [
        f"edit {i}: {problem}"
        for start, end, replace, i in spans
        if (problem := _query_block_problem(content, start, end, replace))
    ]
    if problems:
        raise QueryBlockError(problems)

    for start, end, replace, _ in reversed(spans):
        content = content[:start] + replace + content[end:]
    return content


def _query_block_problem(content: str, start: int, end: int, replace: str) -> str | None:
    """Why replacing content[start:end] with `replace` would change the results of a
    query block, the lines after its query line through its closing line, or None.

    Only the text that actually changes counts, since an anchor may quote a marker or
    a copied line for context. An anchor that takes in all of a block's results may
    replace them, as when removing the block.
    """
    find = content[start:end]
    head = len(commonprefix([find, replace]))
    tail = len(commonprefix([find[head:][::-1], replace[head:][::-1]]))
    changed_start, changed_end = start + head, end - tail

    lines = content.split("\n")
    line_ends = list(accumulate(len(line) + 1 for line in lines))  # just past each newline
    for first, last in query_block_ranges(content):
        query_end = line_ends[first] - 1  # the end of the query line
        results_end = line_ends[last] - 1  # the end of the closing line
        # Text changed or added at the end of the query line runs into the results.
        touched = changed_start < results_end and changed_end >= query_end
        replaced = start <= line_ends[first] and end >= results_end
        if first < last and touched and not replaced:
            line = content.count("\n", 0, changed_start)
            link = _entry_link(lines[first + 1 : line + 1])
            where = f": {link} (relative to this note)" if link else ""
            return (
                "that would change a query block's results, a copy the server regenerates; "
                f"edit the source instead{where}"
            )
    return None


def _entry_link(results: List[str]) -> str | None:
    """Link target of the last entry that starts in `results`, the first lines of a
    query block's results, or None."""
    entries = [m for line in results if (m := _ENTRY_RE.match(line))]
    if not entries:
        return None
    # Every entry starts like the first one; headings copied into entries are deeper.
    return next(m[2] for m in reversed(entries) if m[1] == entries[0][1])
