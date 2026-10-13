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
    Task,
    find_query_blocks,
    is_marker,
    normalize_task_line,
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
    tasks: tuple[Task, ...] = ()  # in task views, the matching items, outermost only


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
    if query.dated and not query.task:  # task views date each item instead
        if note.date is None:
            return False
        first, before = query.date_range(today)
        if (first and note.date < first) or (before and note.date >= before):
            return False
    return True


def find_entries(query: Query, notes: list[Note], exclude: str, today: date) -> list[Entry]:
    """Matching sections, blocks or task items: journals by date, newest first unless
    sort:oldest, then pages by path."""
    entries = []
    for note in notes:
        if note.rel_path != exclude and _in_scope(note, query, today):
            if query.task:
                entries.extend(_task_entries(query, note, today))
            else:
                entries.extend(_note_entries(query.expr, note))
    sign = -1 if query.sort == "newest" else 1

    def order(e: Entry):
        if e.note.date:
            return (0, sign * e.note.date.toordinal(), "", e.section.start)
        return (1, 0, e.note.rel_path, e.section.start)

    return sorted(entries, key=order)


def _task_entries(query: Query, note: Note, today: date) -> list[Entry]:
    """The note's open (is:todo) or checked (is:done) task items that match, one entry
    per section. A section whose heading path matches brings all its items, sub-sections
    included; otherwise each item is tested with its heading path."""
    first, before = query.date_range(today)
    want_done = query.task == "done"

    def wanted(t: Task) -> bool:
        if t.done != want_done:
            return False
        when = (t.stamp or note.date) if want_done else note.date
        if not query.dated:
            return True
        return when is not None and not (first and when < first) and not (before and when >= before)

    entries = []
    covered = -1
    for s in note.sections:
        if s.start < covered:
            continue
        if s.machine:
            covered = s.end
            continue
        heading = s.path_text
        if s.level and query.expr is not None and _holds(query.expr, heading, "", frozenset()):
            pool = [t for d in _subtree(note, s) for t in d.tasks]
            covered = s.end
        else:
            pool = [t for t in s.tasks if _holds(query.expr, heading, t.text, t.tags)]
        if tasks := _outermost([t for t in pool if wanted(t)]):
            entries.append(Entry(note, s, [], whole=False, tasks=tuple(tasks)))
    return entries


def _subtree(note: Note, s: Section) -> list[Section]:
    """Section `s` and its sub-sections, leaving out machine-written ones."""
    machine = [(m.start, m.end) for m in note.sections if m.machine]
    return [
        d
        for d in note.sections
        if s.start <= d.start < s.end and not any(a <= d.start < b for a, b in machine)
    ]


def _outermost(tasks: list[Task]) -> list[Task]:
    """`tasks`, in document order, without those nested inside another of them."""
    out: list[Task] = []
    for t in tasks:
        if not out or t.first >= out[-1].end:
            out.append(t)
    return out


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


def _dedent(lines: list[str], width: int) -> list[str]:
    """`lines` shifted left by up to `width` columns: a nested item shown on its own."""
    if not width:
        return lines
    out = []
    for line in lines:
        expanded = line.expandtabs(4)
        out.append(expanded[min(width, len(expanded) - len(expanded.lstrip())) :])
    return out


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
    """The closing line's hash of `lines`: blind to task states and stamps, so ticking a
    copy in place isn't a hand edit."""
    return _digest([normalize_task_line(line) for line in lines])


def _digest(lines: list[str]) -> str:
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
    if query.task and query.show != "titles":
        report.entries = sum(len(e.tasks) for e in entries)
        return _render_tasks(query, entries, page_rel, level), report
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


def _render_tasks(query: Query, entries: list[Entry], page_rel: str, level: int) -> list[str]:
    """Task items grouped under a heading per section, or with show:list one flat
    checklist, each item followed by a link to its source."""
    items = [(e, t) for e in entries for t in e.tasks]
    if not items:
        return ["", "*No matching items.*", ""]
    if query.show == "list":
        items.sort(key=lambda item: _item_order(*item, query))
    lines = [""]
    shown_entry = None
    for e, t in items[: query.limit]:
        src = e.note.rel_path
        link = _note_link(src, page_rel, e.section.slug)
        copied = _copy_lines(_dedent(e.note.lines[t.first : t.end], t.indent), 0, src, page_rel)
        if query.show == "list":
            copied[0] += f" · [{_escape_brackets(_list_label(e))}]({link})"
        elif e is not shown_entry:
            if shown_entry is not None:
                lines.append("")
            lines.append(f"{'#' * level} [{_escape_brackets(_entry_title(e))}]({link})")
            shown_entry = e
        lines.extend(copied)
    if len(items) > query.limit:
        lines += ["", f"*{len(items) - query.limit} more items not shown.*"]
    lines.append("")
    return lines


def _item_order(e: Entry, t: Task, query: Query) -> tuple:
    """Sort key for a show:list item: by completion date for is:done, else by journal
    date; undated page items last."""
    when = (t.stamp or e.note.date) if query.task == "done" else e.note.date
    if when is None:
        return (1, 0, e.note.rel_path, t.first)
    sign = -1 if query.sort == "newest" else 1
    return (0, sign * when.toordinal(), e.note.rel_path, t.first)


def _list_label(e: Entry) -> str:
    """A show:list item's short source: date and section, or page and section."""
    heading = e.section.path[-1] if e.section.path else ""
    if e.note.date:
        return f"{e.note.date.isoformat()} {heading}".strip()
    return " › ".join(x for x in (e.note.title, heading) if x)


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
        # A hash from before results_hash ignored ticks is accepted too.
        report.hand_edited = (
            old is not None
            and block.hash is not None
            and block.hash not in (results_hash(old), _digest(old))
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
