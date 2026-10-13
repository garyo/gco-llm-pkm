"""A Markdown note as query blocks see it: frontmatter, fenced code, headings, sections,
blocks of text, and the machine-maintained regions that are never matched or copied."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterator

OPEN_RE = re.compile(r"^[ \t]{0,3}<!--\s*query:\s*(?P<query>.*?)\s*-->\s*$")
CLOSE_RE = re.compile(r"^[ \t]{0,3}<!--\s*/query(?:\s+(?P<hash>[0-9a-f]{8}))?\s*-->\s*$")
_BACKLINKS_OPEN_RE = re.compile(r"^\s*<!--\s*backlinks\s*-->\s*$")
_BACKLINKS_CLOSE_RE = re.compile(r"^\s*<!--\s*/backlinks\s*-->\s*$")

FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_YAML_KEY_RE = re.compile(r"^[\w-]+\s*:")
_TITLE_RE = re.compile(r"^title:\s*(.*?)\s*$")

_LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ANCHOR_SUFFIX_RE = re.compile(r"\s+\^[\w-]+$")
_SLUG_DELETE_RE = re.compile(r"[\[\]!\"#$%&'()*+,./:;<=>?@\\^`{|}~]")
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
_URL_RE = re.compile(r"<?https?://[^\s>)]+>?")
_TAG_RE = re.compile(r"(?<!\w)#(\w[\w-]*)")

# Sections written by scheduled jobs rather than by the user.
MACHINE_HEADING_RE = re.compile(r"^(heartbeat|ticktick review)\b", re.I)
_JOURNAL_RE = re.compile(r"^journals/(\d{4})[-_](\d{2})[-_](\d{2})\.md$")


@dataclass
class QueryBlock:
    open_line: int
    close_line: int | None  # None until the block has been filled in
    query: str
    hash: str | None

    @property
    def end_line(self) -> int:
        """Last line belonging to the block."""
        return self.open_line if self.close_line is None else self.close_line


@dataclass(frozen=True)
class Block:
    first: int
    end: int
    kind: str  # "item", "para" or "code"
    text: str  # plain text, for matching
    tags: frozenset[str]


@dataclass
class Section:
    level: int  # 0 for the text before the first heading
    path: tuple[str, ...]  # cleaned heading texts, outermost first
    start: int  # the heading line; for level 0, the line before the text
    body_end: int  # end of the section's own text, before any sub-section
    end: int  # end of the section including its sub-sections
    slug: str
    machine: bool
    blocks: tuple[Block, ...] = ()

    @property
    def path_text(self) -> str:
        return " › ".join(self.path)


@dataclass
class Note:
    rel_path: str
    lines: list[str]  # with query and backlinks blocks blanked out
    title: str
    date: date | None
    sections: list[Section]
    query_blocks: list[QueryBlock]

    def heading_level_before(self, line: int) -> int:
        """Level of the last heading above `line`, 0 if none."""
        level = 0
        for s in self.sections:
            if s.start >= line:
                break
            if s.level:
                level = s.level
        return level


def outside_fences(lines: list[str], start: int = 0, end: int | None = None) -> Iterator[int]:
    """Indices of lines outside fenced code; fence lines themselves are skipped."""
    fence = ""
    for i in range(start, len(lines) if end is None else end):
        m = FENCE_RE.match(lines[i])
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
        elif m:
            fence = m.group(1)
        else:
            yield i


def open_fence(lines: list[str]) -> str:
    """The fence left open at the end of `lines`, or ""."""
    fence = ""
    for line in lines:
        m = FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
        elif m:
            fence = m.group(1)
    return fence


def find_query_blocks(lines: list[str]) -> list[QueryBlock]:
    blocks: list[QueryBlock] = []
    current: QueryBlock | None = None
    for i in outside_fences(lines):
        if m := OPEN_RE.match(lines[i]):
            if current:
                blocks.append(current)
            current = QueryBlock(i, None, m.group("query"), None)
        elif current and (m := CLOSE_RE.match(lines[i])):
            current.close_line, current.hash = i, m.group("hash")
            blocks.append(current)
            current = None
    if current:
        blocks.append(current)
    return blocks


def is_marker(line: str) -> bool:
    return any(r.match(line) for r in (OPEN_RE, CLOSE_RE, _BACKLINKS_OPEN_RE, _BACKLINKS_CLOSE_RE))


def query_block_ranges(text: str) -> list[tuple[int, int]]:
    """0-based inclusive line ranges of the query blocks in `text`, markers included."""
    if "<!--" not in text:
        return []
    return [(b.open_line, b.end_line) for b in find_query_blocks(text.split("\n"))]


def query_result_lines(text: str) -> set[int]:
    """0-based numbers of the lines holding query results, copies of other notes: from the
    line after each query line through its closing line."""
    return {i for first, last in query_block_ranges(text) for i in range(first + 1, last + 1)}


def strip_machine_blocks(text: str) -> str:
    """`text` with query blocks and backlinks lists blanked out, line numbers kept."""
    if "<!--" not in text:
        return text
    return "\n".join(_blank_machine_blocks(text.split("\n")))


def _blank_machine_blocks(lines: list[str]) -> list[str]:
    lines = list(lines)
    spans = [(b.open_line, b.end_line) for b in find_query_blocks(lines)]
    opened = None
    for i in outside_fences(lines):
        if opened is None and _BACKLINKS_OPEN_RE.match(lines[i]):
            opened = i
        elif opened is not None and _BACKLINKS_CLOSE_RE.match(lines[i]):
            spans.append((opened, i))
            opened = None
    for first, last in spans:
        lines[first : last + 1] = [""] * (last + 1 - first)
    return lines


def clean_heading(text: str) -> str:
    """Heading text as displayed: links flattened, HTML tags and `^anchor` suffix removed."""
    text = _HTML_TAG_RE.sub("", _LINK_TEXT_RE.sub(r"\1", text))
    return " ".join(_ANCHOR_SUFFIX_RE.sub("", text).split())


def heading_slug(text: str) -> str:
    """GitHub's anchor for a heading: the displayed text (links flattened), lowercased,
    with punctuation deleted and each space turned into a hyphen."""
    s = _SLUG_DELETE_RE.sub("", _LINK_TEXT_RE.sub(r"\1", text.strip().lower()))
    return re.sub(r"\s", "-", s)


def _heading_slugs(lines: list[str], start: int) -> dict[int, str]:
    """The anchor of every heading by line, a repeated one numbered -1, -2, … in document
    order the way editors number them, headings inside query blocks included."""
    seen: dict[str, int] = {}
    slugs = {}
    for i in outside_fences(lines, start):
        if m := HEADING_RE.match(lines[i]):
            slug = heading_slug(m.group(2) or "")
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            slugs[i] = f"{slug}-{n}" if n else slug
    return slugs


def plain_text(text: str) -> str:
    """Text without link targets and URLs, whose words aren't the author's prose."""
    return _URL_RE.sub(" ", _LINK_TARGET_RE.sub("]", text))


def tags(text: str) -> frozenset[str]:
    return frozenset(t.lower() for t in _TAG_RE.findall(plain_text(text)))


def indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def split_blocks(lines: list[str], start: int, end: int) -> list[tuple[int, int, str]]:
    """(first, end, kind) of each paragraph, top-level list item or code block in a range.

    A list item keeps its indented continuation lines and sub-items, blank lines between
    them included; kind is "item", "para" or "code".
    """
    spans = []
    i = start
    while i < end:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        first = i
        if m := FENCE_RE.match(line):
            i = _fence_end(lines, i, end, m.group(1))
            spans.append((first, i, "code"))
        elif _LIST_ITEM_RE.match(line) and indent(line) < 2:
            i = _item_end(lines, i + 1, end)
            spans.append((first, i, "item"))
        else:
            i += 1
            while i < end and lines[i].strip() and not _interrupts_paragraph(lines[i]):
                i += 1
            spans.append((first, i, "para"))
    return spans


def _interrupts_paragraph(line: str) -> bool:
    return bool(FENCE_RE.match(line) or (_LIST_ITEM_RE.match(line) and indent(line) < 2))


def _fence_end(lines: list[str], i: int, end: int, fence: str) -> int:
    for j in range(i + 1, end):
        m = FENCE_RE.match(lines[j])
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
            return j + 1
    return end


def _item_end(lines: list[str], i: int, end: int) -> int:
    while i < end:
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < end and not lines[j].strip():
                j += 1
            if j < end and indent(lines[j]) >= 2:
                i = j
                continue
            return i
        if indent(line) < 2 and _interrupts_paragraph(line):
            return i
        i += 1
    return end


def parse_note(rel_path: str, text: str) -> Note:
    raw = text.split("\n")
    lines = _blank_machine_blocks(raw)
    body_start, title = _frontmatter(lines)

    headings = []  # (line, level, cleaned text)
    for i in outside_fences(lines, body_start):
        if m := HEADING_RE.match(lines[i]):
            headings.append((i, len(m.group(1)), clean_heading(m.group(2) or "")))

    sections: list[Section] = []
    first_heading = headings[0][0] if headings else len(lines)
    if any(line.strip() for line in lines[body_start:first_heading]):
        sections.append(Section(0, (), body_start - 1, first_heading, first_heading, "", False))

    slugs = _heading_slugs(raw, body_start)
    stack: list[tuple[int, str]] = []
    for k, (i, level, cleaned) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, cleaned))
        body_end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
        end = next((h[0] for h in headings[k + 1 :] if h[1] <= level), len(lines))
        sections.append(
            Section(
                level,
                tuple(t for _, t in stack),
                i,
                body_end,
                end,
                slugs[i],
                bool(MACHINE_HEADING_RE.match(cleaned)),
            )
        )

    for s in sections:
        s.blocks = tuple(
            _block(lines, *span) for span in split_blocks(lines, s.start + 1, s.body_end)
        )

    m = _JOURNAL_RE.match(rel_path)
    note_date = date(int(m[1]), int(m[2]), int(m[3])) if m else None
    if not title:
        stem = rel_path.rsplit("/", 1)[-1].removesuffix(".md")
        title = next((t for _, lvl, t in headings if lvl == 1), "") or stem
    return Note(rel_path, lines, title, note_date, sections, find_query_blocks(raw))


def _block(lines: list[str], first: int, end: int, kind: str) -> Block:
    text = "\n".join(lines[first:end])
    return Block(first, end, kind, plain_text(text), frozenset() if kind == "code" else tags(text))


def _frontmatter(lines: list[str]) -> tuple[int, str]:
    """(first line after the YAML frontmatter, its title or "")."""
    if not lines or lines[0].strip() != "---" or len(lines) < 2 or not _YAML_KEY_RE.match(lines[1]):
        return 0, ""
    title = ""
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return i + 1, title
        if m := _TITLE_RE.match(lines[i]):
            title = m.group(1).strip("\"'")
    return 0, ""
