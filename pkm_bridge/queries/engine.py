"""Evaluate query blocks against the notes and write their results back into a note."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .notes import (
    HEADING_RE,
    Block,
    Note,
    Section,
    find_query_blocks,
    is_marker,
    open_fence,
    outside_fences,
    parse_note,
)
from .syntax import (
    HEADING_STOP_WORDS,
    And,
    Node,
    Not,
    Or,
    Query,
    QuerySyntaxError,
    Term,
    parse_query,
)

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SKIP_DIRS = frozenset({"bak", "version-files", "org-persist", "assets", "node_modules"})
_INLINE_LINK_RE = re.compile(r"(\]\()(<[^>\n]*>|[^)\s]+)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][\w+.-]*:")
_UNHASHED_BLOCK = (
    "the closing line has no hash and the block isn't empty; empty it so the query can fill it"
)


@dataclass
class BlockReport:
    query: str
    entries: int = 0  # matches before the limit
    error: str | None = None
    changed: bool = False
    hand_edited: bool = False


@dataclass
class RefreshResult:
    text: str
    changed: bool
    blocks: list[BlockReport] = field(default_factory=list)


@dataclass
class Entry:
    note: Note
    section: Section
    lines: list[str]
    whole: bool  # the whole section matched by its heading, sub-sections included


class Corpus:
    """The notes under an org root, parsed once and re-parsed only when they change."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self._cache: dict[str, tuple[tuple[int, int], Note]] = {}
        self._lock = threading.Lock()

    def notes(self) -> list[Note]:
        with self._lock:
            fresh = {}
            for path in self._files():
                rel = path.relative_to(self.root).as_posix()
                try:
                    st = path.stat()
                    key = (st.st_mtime_ns, st.st_size)
                    hit = self._cache.get(rel)
                    if hit is None or hit[0] != key:
                        hit = (
                            key,
                            parse_note(rel, path.read_text(encoding="utf-8", errors="replace")),
                        )
                except OSError:
                    continue  # deleted or unreadable mid-scan
                fresh[rel] = hit
            self._cache = fresh
            return [note for _, note in fresh.values()]

    def pages_with_queries(self) -> list[str]:
        return sorted(n.rel_path for n in self.notes() if n.query_blocks)

    def _files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
            for name in filenames:
                if (
                    name.endswith(".md")
                    and not name.startswith(".")
                    and ".sync-conflict-" not in name
                ):
                    yield Path(dirpath) / name


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _holds(node: Node | None, heading: str, text: str, block_tags: frozenset[str]) -> bool:
    if node is None:
        return True
    if isinstance(node, And):
        return all(_holds(a, heading, text, block_tags) for a in node.args)
    if isinstance(node, Or):
        return any(_holds(a, heading, text, block_tags) for a in node.args)
    if isinstance(node, Not):
        return not _holds(node.arg, heading, text, block_tags)
    return _term_holds(node, heading, text, block_tags)


def _term_holds(term: Term, heading: str, text: str, block_tags: frozenset[str]) -> bool:
    if term.scope == "heading":
        return bool(term.pattern.search(heading))
    if term.scope == "text":
        return bool(term.pattern.search(heading) or term.pattern.search(text))
    word = term.text.lower()
    if term.is_word and word in block_tags:
        return True
    return word not in HEADING_STOP_WORDS and bool(term.pattern.search(heading))


def _in_scope(note: Note, query: Query, today: date) -> bool:
    if query.scope == "journals" and note.date is None:
        return False
    if query.scope == "pages" and note.rel_path.startswith("journals/"):
        return False
    if query.dated:
        if note.date is None:
            return False
        first, before = query.date_range(today)
        if (first and note.date < first) or (before and note.date >= before):
            return False
    return True


def find_entries(query: Query, notes: list[Note], exclude: str, today: date) -> list[Entry]:
    """Matching sections and blocks, journals newest first, then pages by path."""
    entries = []
    for note in notes:
        if note.rel_path != exclude and _in_scope(note, query, today):
            entries.extend(_note_entries(query.expr, note))

    def order(e: Entry):
        if e.note.date:
            return (0, -e.note.date.toordinal(), "", e.section.start)
        return (1, 0, e.note.rel_path, e.section.start)

    return sorted(entries, key=order)


def _note_entries(expr: Node | None, note: Note) -> list[Entry]:
    entries = []
    covered = -1
    for s in note.sections:
        if s.start < covered:
            continue
        if s.machine:
            covered = s.end
            continue
        heading = s.path_text
        if expr is None or (s.level and _holds(expr, heading, "", frozenset())):
            entries.append(Entry(note, s, _section_lines(note, s), whole=True))
            covered = s.end
            continue
        hits = [b for b in s.blocks if _holds(expr, heading, b.text, b.tags)]
        if hits:
            entries.append(Entry(note, s, _join_blocks(note.lines, hits), whole=False))
    return entries


def _section_lines(note: Note, s: Section) -> list[str]:
    """A section's lines after its heading, without machine-written sub-sections."""
    skip = [(d.start, d.end) for d in note.sections if d.machine and s.start < d.start < s.end]
    return [
        note.lines[i]
        for i in range(s.start + 1, s.end)
        if not any(first <= i < end for first, end in skip)
    ]


def _join_blocks(lines: list[str], blocks: list[Block]) -> list[str]:
    out: list[str] = []
    prev = None
    for b in blocks:
        if out and not (b.kind == "item" and prev and prev.kind == "item"):
            out.append("")
        out.extend(lines[b.first : b.end])
        prev = b
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def results_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:8]


def render_query(
    source: str, page_rel: str, notes: list[Note], level: int, today: date
) -> tuple[list[str], BlockReport]:
    """The result lines for one query placed in `page_rel` with entries at `level`."""
    report = BlockReport(source)
    try:
        query = parse_query(source)
    except QuerySyntaxError as e:
        report.error = str(e)
        return ["", f"*Query error: {e}*", ""], report
    entries = find_entries(query, notes, page_rel, today)
    report.entries = len(entries)
    if not entries:
        return ["", "*No matching entries.*", ""], report

    lines = [""]
    for k, entry in enumerate(entries[: query.limit]):
        title = _escape_brackets(_entry_title(entry))
        link = _note_link(entry.note.rel_path, page_rel, entry.section.slug)
        if query.show == "titles":
            lines.append(f"- [{title}]({link})")
            continue
        if k:
            lines.append("")
        lines.append(f"{'#' * level} [{title}]({link})")
        shift = level - entry.section.level if entry.whole else 0
        lines.extend(_copy_lines(entry.lines, shift, entry.note.rel_path, page_rel))
    if len(entries) > query.limit:
        lines += ["", f"*{len(entries) - query.limit} more entries not shown.*"]
    lines.append("")
    return lines, report


def _entry_title(entry: Entry) -> str:
    note, path = entry.note, list(entry.section.path)
    if note.date:
        day = f"{note.date.isoformat()} {_WEEKDAYS[note.date.weekday()]}"
        return f"{day} · {' › '.join(path)}" if path else day
    if path and path[0].lower() == note.title.lower():
        path = path[1:]
    return " › ".join([note.title, *path])


def _escape_brackets(text: str) -> str:
    return text.replace("[", r"\[").replace("]", r"\]")


def _note_link(src_rel: str, page_rel: str, slug: str) -> str:
    target = posixpath.relpath(src_rel, posixpath.dirname(page_rel) or ".").replace(" ", "%20")
    return f"{target}#{slug}" if slug else target


def _copy_lines(lines: list[str], shift: int, src_rel: str, page_rel: str) -> list[str]:
    """Copied lines with headings shifted down by `shift`, relative links re-pointed from
    `src_rel` to `page_rel`, stray markers dropped, blank runs collapsed and fences closed."""
    prose = set(outside_fences(lines))
    out: list[str] = []
    for i, line in enumerate(lines):
        if i not in prose:
            out.append(line)
        elif not line.strip():
            if out and out[-1].strip():
                out.append("")
        elif not is_marker(line):
            if shift and (m := HEADING_RE.match(line)):
                line = "#" * min(6, len(m.group(1)) + shift) + (
                    f" {m.group(2)}" if m.group(2) else ""
                )
            out.append(_rebase_links(line, src_rel, page_rel))
    while out and not out[-1].strip():
        out.pop()
    if fence := open_fence(out):
        out.append(fence)
    while out and not out[0].strip():
        out.pop(0)
    return out


def _rebase_links(line: str, src_rel: str, page_rel: str) -> str:
    src_dir = posixpath.dirname(src_rel)
    page_dir = posixpath.dirname(page_rel) or "."

    def rebase(m: re.Match) -> str:
        target = m.group(2)
        bracketed = target.startswith("<")
        raw = target[1:-1] if bracketed else target
        if not raw or _SCHEME_RE.match(raw) or raw.startswith(("/", "~")):
            return m.group(0)
        path, sep, fragment = raw.partition("#")
        full = posixpath.normpath(posixpath.join(src_dir, path)) if path else src_rel
        new = posixpath.relpath(full, page_dir) + sep + fragment
        if bracketed:
            new = f"<{new}>"
        elif not path:
            new = new.replace(" ", "%20")
        return m.group(1) + new

    return _INLINE_LINK_RE.sub(rebase, line)


# ---------------------------------------------------------------------------
# Refreshing a note
# ---------------------------------------------------------------------------


def refresh_text(text: str, rel_path: str, corpus: Corpus, today: date) -> RefreshResult:
    """`text` (the note at `rel_path`) with every query block's results regenerated."""
    page = parse_note(rel_path, text)
    if not page.query_blocks:
        return RefreshResult(text, False)
    notes = corpus.notes()
    lines = text.split("\n")
    out: list[str] = []
    pos = 0
    reports, written = [], []
    for block in page.query_blocks:
        out += lines[pos : block.open_line + 1]
        pos = block.end_line + 1
        if block.close_line is None:
            old = None
        else:
            old = lines[block.open_line + 1 : block.close_line]
            if block.hash is None and any(x.strip() for x in old):
                reports.append(BlockReport(block.query, error=_UNHASHED_BLOCK))
                out += old + [lines[block.close_line]]
                written.append(old)
                continue
        level = min(6, (page.heading_level_before(block.open_line) or 1) + 1)
        results, report = render_query(block.query, rel_path, notes, level, today)
        closing = f"<!-- /query {results_hash(results)} -->"
        report.hand_edited = (
            old is not None and block.hash is not None and results_hash(old) != block.hash
        )
        report.changed = old != results or lines[block.end_line] != closing
        out += results + [closing]
        reports.append(report)
        written.append(results)
    out += lines[pos:]
    new_text = "\n".join(out)
    _check_round_trip(new_text, written)
    return RefreshResult(new_text, new_text != text, reports)


def _check_round_trip(text: str, written: list[list[str]]) -> None:
    """Refuse output whose blocks wouldn't read back as written: a refresh must never be
    able to grow a page by misreading its own markers."""
    lines = text.split("\n")
    blocks = find_query_blocks(lines)
    ok = len(blocks) == len(written) and all(
        b.close_line is not None and lines[b.open_line + 1 : b.close_line] == w
        for b, w in zip(blocks, written)
    )
    if not ok:
        raise RuntimeError("refreshed query blocks would not read back as written")


def preview(
    source: str, rel_path: str, corpus: Corpus, today: date
) -> tuple[list[str], BlockReport]:
    """The results `source` would get in `rel_path`, without writing anything."""
    return render_query(source, rel_path, corpus.notes(), 2, today)
