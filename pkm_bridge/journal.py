"""Daily journal files and the Markdown sections inside them.

A day's journal is `journals/YYYY-MM-DD.md` under ORG_DIR: YAML frontmatter,
then `#` sections. The editor creates journals too (shared/editor/journal.ts)
and must produce the same path and template.
"""

import re
import uuid
from datetime import date

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
# Trailing block anchors (`^travel`) and inline #tags are not part of a heading's title.
_HEADING_SUFFIX = re.compile(r"(\s+(\^[\w-]+|#[\w/-]+))+$")


def journal_rel_path(date_str: str) -> str:
    """Path of a day's journal relative to ORG_DIR. Raises ValueError unless YYYY-MM-DD."""
    if date.fromisoformat(date_str).isoformat() != date_str:
        raise ValueError(f"Expected YYYY-MM-DD, got '{date_str}'")
    return f"journals/{date_str}.md"


def journal_template(date_str: str, note_id: str | None = None) -> str:
    """Initial content of a new journal: frontmatter only."""
    note_id = note_id or str(uuid.uuid4()).upper()
    return f'---\ntitle: "{date_str}"\nid: {note_id}\ndate: {date_str}\n---\n\n'


def _title_key(title: str) -> str:
    return _HEADING_SUFFIX.sub("", title.strip()).casefold()


def parse_heading(line: str) -> tuple[int, str] | None:
    """(level, title) for a Markdown ATX heading line, else None."""
    m = _HEADING.match(line)
    return (len(m.group(1)), m.group(2)) if m else None


def _heading_lines(lines: list[str]) -> list[tuple[int, int, str]]:
    """(index, level, title) of every heading outside frontmatter and code fences."""
    start = 0
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if closing is not None:
            start = closing + 1

    headings = []
    in_fence = False
    for i in range(start, len(lines)):
        if _FENCE.match(lines[i]):
            in_fence = not in_fence
        elif not in_fence and (h := parse_heading(lines[i])):
            headings.append((i, *h))
    return headings


def append_to_section(content: str, text: str, heading: str | None = None) -> str:
    """Return `content` with `text` appended, never altering existing lines.

    With no heading, `text` goes at the end of the file. With a heading
    ('Music', or '## Music' to pin the level), it goes after the last
    non-blank line of the first matching heading's own text, ahead of any
    subheading (matched case-insensitively, at any level unless one is given);
    a missing heading is created at the end of the file at the given level,
    default `#`.
    """
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines()
    new_lines = text.strip("\r\n").rstrip().splitlines()

    if heading is not None:
        level, title = parse_heading(heading.strip()) or (0, heading.strip())
        key = _title_key(title)
        headings = _heading_lines(lines)
        for n, (idx, lvl, ttl) in enumerate(headings):
            if _title_key(ttl) == key and level in (0, lvl):
                end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
                while end > idx + 1 and not lines[end - 1].strip():
                    end -= 1
                lines[end:end] = new_lines
                return newline.join(lines) + newline
        new_lines = [f"{'#' * (level or 1)} {title}", *new_lines]

    if new_lines and parse_heading(new_lines[0]) and lines and lines[-1].strip():
        lines.append("")
    return newline.join(lines + new_lines) + newline
