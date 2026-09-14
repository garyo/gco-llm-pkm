"""Three-way merge of note text.

Line-level diff3 (via merge3) on purpose: notes are line-oriented, and an exact
algorithm reports a conflict wherever both sides touched the same lines instead
of guessing. A wrong automatic merge is worse than asking.
"""

from dataclasses import dataclass

from merge3 import Merge3

CONFLICT_START = "<<<<<<<"
CONFLICT_MID = "======="
CONFLICT_END = ">>>>>>>"


@dataclass(frozen=True)
class MergeResult:
    """`merged` is the text to write when `clean`; otherwise the conflict-marked text."""

    merged: str
    clean: bool


def three_way_merge(base: str, mine: str, theirs: str) -> MergeResult:
    """Merge `mine` and `theirs`, both derived from `base`.

    Returns clean text when the two sides changed different lines, and a
    conflict-marked text (mine / disk) when they overlap.
    """
    if mine == theirs or base == theirs:
        return MergeResult(mine, True)
    if base == mine:
        return MergeResult(theirs, True)

    crlf = theirs.count("\r\n") > theirs.count("\n") // 2
    base_l, mine_l, theirs_l = (_lines(t) for t in (base, mine, theirs))

    m3 = Merge3(base_l, mine_l, theirs_l)
    clean = all(region[0] != "conflict" for region in m3.merge_regions())
    merged = "".join(
        m3.merge_lines(
            name_a="mine",
            name_b="disk",
            start_marker=CONFLICT_START,
            mid_marker=CONFLICT_MID,
            end_marker=CONFLICT_END,
        )
    )
    if crlf:
        merged = merged.replace("\n", "\r\n")
    return MergeResult(merged, clean)


def _lines(text: str) -> list[str]:
    """Split into newline-terminated lines with LF endings.

    A missing final newline is added so that one side adding it is not
    mistaken for a change to the last line.
    """
    text = text.replace("\r\n", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text.splitlines(keepends=True)
