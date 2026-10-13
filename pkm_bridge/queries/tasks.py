"""Checking a task off through its copy in a query block: find the item the copy came
from, tick or untick it there with a completion stamp, and flip the copy to match.

Pure functions on texts, so the server (through FileEditor) and Emacs (through the CLI,
with its buffers' texts) run the same rules and each writes where its files are.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote

from .notes import (
    TASK_RE,
    Note,
    Section,
    Task,
    find_query_blocks,
    parse_note,
    set_task_state,
    task_key,
    task_state,
    with_task_state,
)
from .syntax import QuerySyntaxError, parse_query

# An entry heading in results, `## [title](link)`; headings copied inside entries are deeper.
_ENTRY_RE = re.compile(r"^(?P<level>#{1,6}) \[.*\]\((?P<link>[^)\s]+)\)\s*$")
# A show:list item's first line ends with its source: ` · [DATE HEADING](LINK)`.
_LIST_SOURCE_RE = re.compile(r" · \[(?:\\.|[^\]\\])*\]\((?P<link>[^)\s]+)\)\s*$")


class TaskError(ValueError):
    """A copied task can't be checked off; the message says why, for the user."""


@dataclass(frozen=True)
class TaskRef:
    source_path: str  # relative to the notes root
    section: str  # anchor of its section; "" for text before the first heading
    key: str  # see task_key
    occurrence: int  # copies with the same key before it, from the same source section
    copies: int  # copies with the same key in all, from that section
    list_item: bool  # a show:list item, whose first line ends with its source


@dataclass(frozen=True)
class TaskChange:
    page_text: str
    source_path: str
    source_text: str
    source_line: int  # 0-based
    item: str


def locate_task(page_text: str, page_path: str, line: int) -> TaskRef:
    """Which note and section the copied task on `line` (0-based) of the page came from."""
    lines = page_text.split("\n")
    block = next(
        (
            b
            for b in find_query_blocks(lines)
            if b.close_line is not None and b.open_line < line < b.close_line
        ),
        None,
    )
    if block is None or block.close_line is None:
        raise TaskError("that line isn't in a query block's results")
    key = task_key(lines[line]) if TASK_RE.match(lines[line]) else None
    if key is None:
        raise TaskError("that line isn't a task")
    try:
        list_item = parse_query(block.query).show == "list"
    except QuerySyntaxError:
        list_item = False

    if list_item:
        source = _LIST_SOURCE_RE.search(lines[line])
        if source is None:
            raise TaskError("can't tell which note this item came from")
        link = source.group("link")
        key = task_key(lines[line][: source.start()])
        scope = range(block.open_line + 1, block.close_line)
    else:
        found = _entry_bounds(lines, block.open_line, block.close_line, line)
        if found is None:
            raise TaskError("can't tell which note this item came from")
        link, scope = found

    same = [i for i in scope if _copy_key(lines[i], list_item, link) == key]
    source_path, section = _resolve(link, page_path)
    return TaskRef(source_path, section, key, same.index(line), len(same), list_item)


def check_task(
    page_text: str, page_path: str, line: int, source_text: str, checked: bool, today: date
) -> TaskChange:
    """Tick (`checked`) or untick the source of the copied task on `line` (0-based), and
    flip the copy the same way. Raises TaskError rather than guess at an item."""
    ref = locate_task(page_text, page_path, line)
    source = parse_note(ref.source_path, source_text)
    section = next((s for s in source.sections if s.slug == ref.section), None)
    if section is None:
        raise TaskError(f"{ref.source_path} has no section #{ref.section} now; refresh the page")
    candidates = [t for t in _section_tasks(source, section) if t.key == ref.key]
    if ref.occurrence >= len(candidates):
        raise TaskError("this copy is out of date; refresh it")
    if len(candidates) > 1 and len(candidates) != ref.copies:
        raise TaskError(f"{ref.source_path} has several items like this one; tick it there")
    task = candidates[ref.occurrence]

    source_lines = source_text.split("\n")
    # Already so (a stale copy): leave the source, and its stamp, as they are.
    if task.done != checked:
        source_lines[task.first] = set_task_state(source_lines[task.first], checked, today)
    page_lines = page_text.split("\n")
    page_lines[line] = _mirror(page_lines[line], source_lines[task.first], ref.list_item)
    return TaskChange(
        "\n".join(page_lines), ref.source_path, "\n".join(source_lines), task.first, ref.key
    )


def _entry_level(line: str) -> int:
    """Heading level of an entry heading line, 0 for any other line."""
    m = _ENTRY_RE.match(line)
    return len(m.group("level")) if m else 0


def _entry_bounds(
    lines: list[str], open_line: int, close_line: int, line: int
) -> tuple[str, range] | None:
    """The link of the entry holding `line`, and the lines under its heading. Entries are
    the headings at the level of the block's first one."""
    first = next((i for i in range(open_line + 1, close_line) if _entry_level(lines[i])), None)
    if first is None or first > line:
        return None
    level = _entry_level(lines[first])
    head = next(i for i in range(line, first - 1, -1) if _entry_level(lines[i]) == level)
    end = next(
        (i for i in range(head + 1, close_line) if _entry_level(lines[i]) == level), close_line
    )
    m = _ENTRY_RE.match(lines[head])
    return (m.group("link"), range(head + 1, end)) if m else None


def _copy_key(line: str, list_item: bool, link: str) -> str | None:
    """task_key of a copied task line from the source `link`, or None for other lines."""
    if not TASK_RE.match(line):
        return None
    if list_item:
        source = _LIST_SOURCE_RE.search(line)
        if source is None or source.group("link") != link:
            return None
        line = line[: source.start()]
    return task_key(line)


def _resolve(link: str, page_path: str) -> tuple[str, str]:
    """A link in a page as (path relative to the notes root, anchor)."""
    path, _, fragment = link.partition("#")
    full = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), unquote(path)))
    if full == ".." or full.startswith(("../", "/")):
        raise TaskError(f"{link} points outside the notes")
    return full, unquote(fragment)


def _section_tasks(note: Note, section: Section) -> list[Task]:
    """Task items in `section` and its sub-sections, leaving out machine-written ones."""
    machine = [(s.start, s.end) for s in note.sections if s.machine]
    return [
        t
        for s in note.sections
        if section.start <= s.start < section.end and not any(a <= s.start < b for a, b in machine)
        for t in s.tasks
    ]


def _mirror(copy: str, source_line: str, list_item: bool) -> str:
    """The copied task line with its source line's state and stamp. A show:list item keeps
    its source link after the stamp."""
    checked, stamp = task_state(source_line)
    source = _LIST_SOURCE_RE.search(copy) if list_item else None
    if source is None:
        return with_task_state(copy, checked, stamp)
    return with_task_state(copy[: source.start()], checked, stamp) + copy[source.start() :]
