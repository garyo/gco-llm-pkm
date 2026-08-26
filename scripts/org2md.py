#!/usr/bin/env python3
"""Convert the org-mode PKM to CommonMark + YAML frontmatter.

Pandoc is the emphasis/list/table engine only.  Everything org-specific --
frontmatter, IDs, drawers, links, heading levels -- is handled here, because
pandoc alone mangles all of it: it escapes `\\<2026-02-13 Fri\\>`, emits
`<div class="BACKLINKS drawer">`, leaves `id:` targets raw, and discards `:ID:`.

Heading convention: `#` is NOT reserved.  Pages and top-level files are
level-preserving (`*` -> `#`).  Journals delete the `* <date>` wrapper and
promote its subtree by one, so their top sections also land at `#`.

The converter fails loudly rather than guessing.  Every known exception is
listed in ANOMALIES below; if the counts stop matching, the converter is wrong,
not the corpus.

Usage:  org2md.py SRC_DIR DST_DIR
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Known exceptions, enumerated from the corpus.  See the plan file.
# --------------------------------------------------------------------------

# Lines starting with `#` are invisible org comments today.  Most are markdown
# that was pasted in and has been hidden ever since; conversion reveals them.
# The org level given is the level the heading should occupy *before* the
# journal promotion runs, so it nests where the surrounding org headings put it.
COMMENT_ACTIONS: dict[str, dict[int, tuple[str, int | None]]] = {
    # Talk outline pasted under `** AI-assisted version` (org level 2).
    "pages/cm-ai-talk-2026.org": {
        93: ("heading", 3),
        100: ("heading", 4),
        110: ("heading", 4),
        124: ("heading", 4),
        138: ("heading", 4),
    },
    # NB: journals/2026-07-13.org has `##` lines too, but they sit inside a
    # #+begin_quote (a pasted Meta support chat).  They are quoted content,
    # not org comments, and block-awareness leaves them alone.
    # Siblings of `** Music`; each is followed by its own `***` children.
    "journals/2025-11-03.org": {
        14: ("heading", 2),
        34: ("heading", 2),
    },
    # Section heading sitting directly under the date wrapper.
    "journals/2026-06-25.org": {7: ("heading", 2)},
    # An inline #tag at line start -- must not become a heading.  The
    # surrounding lines are list items, so it reads correctly as one.
    "journals/2026-05-16.org": {13: ("listitem", None)},
    # A real tombstone comment.
    "journals/2026-01-08a.org": {1: ("htmlcomment", None)},
}

EXPECTED_COMMENT_LINES = sum(len(v) for v in COMMENT_ACTIONS.values())  # 10

# Bare `[[target]]` links with no id:/file:/http: prefix.
BARE_LINK_TARGETS = {
    "analytics.oberbrunner.com": "https://analytics.oberbrunner.com",
    "logseq": "https://logseq.com",
}
EXPECTED_BARE_LINKS = 10

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}
MAX_HEADING_LEVEL = 6
# org-inlinetask-min-level: at or past this, stars mark an inline task, not a
# heading.  The corpus has exactly one (journals/2025-10-27.org).
INLINETASK_MIN_LEVEL = 15

BLOCK_START_RE = re.compile(r"^\s*#\+begin_(\w+)", re.I)
BLOCK_END_RE = re.compile(r"^\s*#\+end_(\w+)", re.I)


def iter_lines(raw: list[str]):
    """Yield (lineno, line, in_block).

    Inside `#+begin_…`/`#+end_…` nothing is a heading, drawer, or comment --
    it is literal content, and treating it otherwise silently rewrites quoted
    text and example code.
    """
    depth = 0
    for i, line in enumerate(raw):
        if BLOCK_START_RE.match(line):
            yield i + 1, line, depth > 0
            depth += 1
            continue
        if BLOCK_END_RE.match(line):
            depth = max(0, depth - 1)
            yield i + 1, line, depth > 0
            continue
        yield i + 1, line, depth > 0

HEADING_RE = re.compile(r"^(\*+)\s+(.*?)\s*$")
TAGS_RE = re.compile(r"\s+(:[A-Za-z0-9_@#%]+(?::[A-Za-z0-9_@#%]+)*:)$")
DRAWER_START_RE = re.compile(r"^\s*:([A-Z][A-Z0-9_]*):\s*$")
KEYWORD_RE = re.compile(r"^#\+([a-zA-Z_]+):\s*(.*)$")
ID_RE = re.compile(r"^\s*:ID:\s+(.+?)\s*$")
UUID_RE = re.compile(
    r"^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$"
)
ORG_LINK_RE = re.compile(r"\[\[([^\]]+)\](?:\[([^\]]*)\])?\]")
ACTIVE_TS_RE = re.compile(r"<(\d{4}-\d{2}-\d{2}[^>\n]*)>")
INACTIVE_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}[^\]\n]*)\]")
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class ConversionError(Exception):
    """Raised when the corpus does not match what the converter expects."""


@dataclass
class Heading:
    """One org heading, with what it carries into markdown."""

    level: int
    text: str
    tags: list[str] = field(default_factory=list)
    anchor: str | None = None


@dataclass
class Note:
    """A parsed org file, ready to emit."""

    rel: Path
    title: str | None = None
    file_id: str | None = None
    filetags: list[str] = field(default_factory=list)
    date: str | None = None
    headings: list[Heading] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)


def slugify(text: str) -> str:
    """A short, stable anchor slug for a heading."""
    text = ORG_LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:48] or "section"


def is_journal(rel: Path) -> bool:
    return rel.parts[0] == "journals"


# --------------------------------------------------------------------------
# Pass 1 -- index every :ID: so links can be resolved to paths
# --------------------------------------------------------------------------


def note_id(raw: str, rel: Path, report: list | None = None) -> str:
    """Return a usable ID for RAW, minting one if RAW is not a UUID.

    Nine journals in this corpus carry an unexpanded shell command as their
    :ID: -- `$(uuidgen)` and friends -- written by something that built the
    file by hand instead of using the journal skill.  Those notes have no
    real identity, so mint one deterministically from the path: both the
    index pass and the convert pass then agree without sharing state.
    """
    if UUID_RE.match(raw):
        return raw
    minted = str(uuid.uuid5(uuid.NAMESPACE_URL, rel.as_posix())).upper()
    if report is not None:
        report.append(f"{rel}: :ID: was not a UUID ({raw!r}); minted {minted}")
    return minted


def build_id_index(files: list[tuple[Path, Path]]) -> dict[str, tuple[Path, str | None]]:
    """Map every :ID: to (relative .md path, anchor-or-None for heading IDs)."""
    index: dict[str, tuple[Path, str | None]] = {}
    for abs_path, rel in files:
        lines = abs_path.read_text(encoding="utf-8").splitlines()
        last_heading: Heading | None = None
        heading_count = 0
        have_file_id = False
        for _lineno, line, in_block in iter_lines(lines):
            if in_block:
                continue
            m = HEADING_RE.match(line)
            if m and len(m.group(1)) < INLINETASK_MIN_LEVEL:
                text = m.group(2)
                tm = TAGS_RE.search(text)
                if tm:
                    text = text[: tm.start()].rstrip()
                last_heading = Heading(len(m.group(1)), text)
                heading_count += 1
                continue
            im = ID_RE.match(line)
            if not im:
                continue
            # A file-level ID is one before any heading, or on the very first
            # heading (journals hang their file ID on the date wrapper).
            if last_heading is None or (
                not have_file_id and last_heading.level == 1 and heading_count <= 1
            ):
                anchor = None  # file-level: before any heading, or on the wrapper
                have_file_id = True
            else:
                anchor = slugify(last_heading.text)
            index[im.group(1)] = (rel.with_suffix(".md"), anchor)
            # also index under the minted ID so either spelling resolves
            fixed = note_id(im.group(1), rel)
            if fixed != im.group(1):
                index[fixed] = (rel.with_suffix(".md"), anchor)
    return index


# --------------------------------------------------------------------------
# Pass 2 -- parse one org file
# --------------------------------------------------------------------------


def parse(abs_path: Path, rel: Path, index, report: list) -> Note:
    raw = abs_path.read_text(encoding="utf-8").splitlines()
    note = Note(rel=rel)
    key = rel.as_posix()
    comment_actions = COMMENT_ACTIONS.get(key, {})

    dm = DATE_IN_NAME_RE.search(rel.stem)
    if is_journal(rel) and dm:
        note.date = dm.group(1)

    out: list[str] = []
    headings: list[Heading] = []
    seen_heading = False
    blocks = {ln for ln, _l, inb in iter_lines(raw) if inb}
    i = 0
    while i < len(raw):
        line = raw[i]
        lineno = i + 1

        # Inside #+begin_…/#+end_… everything is literal content.
        if lineno in blocks:
            out.append(line)
            i += 1
            continue

        # org-inlinetask: 15+ stars, terminated by its own END line.
        im2 = HEADING_RE.match(line)
        if im2 and len(im2.group(1)) >= INLINETASK_MIN_LEVEL:
            text = im2.group(2)
            if text.strip() == "END":
                i += 1
                continue
            todo = re.match(r"^(?:TODO|DONE)\s+(.*)$", text)
            done = text.startswith("DONE")
            out.append(f"- [{'x' if done else ' '}] {todo.group(1) if todo else text}")
            i += 1
            continue

        mal_title = re.match(r"^#\+title\s+(\S.*)$", line, re.I)
        if mal_title:
            note.title = mal_title.group(1).strip()
            report.append(
                f"{rel}:{lineno}: '#+Title' is missing its colon, so org never "
                f"showed it as a title; used {note.title!r}"
            )
            i += 1
            continue

        km = KEYWORD_RE.match(line)
        if km:
            kw, val = km.group(1).lower(), km.group(2).strip()
            if kw == "title":
                note.title = val
            elif kw == "filetags":
                note.filetags = [t for t in val.split(":") if t]
            # STARTUP / OPTIONS / others carry no meaning in markdown
            i += 1
            continue

        # Drawers: :PROPERTIES: harvests the ID then vanishes; :BACKLINKS:
        # becomes an HTML-comment block that survives pandoc as a token.
        dm2 = DRAWER_START_RE.match(line)
        if dm2 and dm2.group(1) not in ("END",):
            name = dm2.group(1)
            body, i = _read_drawer(raw, i, rel, lineno)
            if name == "PROPERTIES":
                kept: list[str] = []
                for b in body:
                    im = ID_RE.match(b)
                    if im and not seen_heading:
                        note.file_id = note_id(im.group(1), rel, report)
                        continue
                    if (
                        im
                        and note.file_id is None
                        and headings
                        and len(headings) == 1
                        and headings[0].level == 1
                    ):
                        # A journal hangs its file ID on the date wrapper. Only
                        # treat a first level-1 heading that way when no drawer
                        # above it already supplied one -- a page's first
                        # section is a section, not the file.
                        note.file_id = note_id(im.group(1), rel, report)
                        continue
                    if im and headings:
                        headings[-1].anchor = slugify(headings[-1].text)
                        continue
                    pm = re.match(r"^\s*:([A-Za-z][\w-]*):\s*(.+?)\s*$", b)
                    if pm:
                        kept.append(f"**{pm.group(1)}:** {pm.group(2)}")
                    elif b.strip():
                        raise ConversionError(
                            f"{rel}:{lineno}: unparsed property line {b.strip()!r}"
                        )
                if kept:
                    token = f"@@PKMBLOCK{len(note.blocks)}@@"
                    note.blocks.append("\n".join(kept))
                    out.extend(["", token, ""])
            elif name == "BACKLINKS":
                token = f"@@PKMBLOCK{len(note.blocks)}@@"
                note.blocks.append(_backlinks_block(body, rel, index))
                out.extend(["", token, ""])
            else:
                raise ConversionError(f"{rel}:{lineno}: unknown drawer :{name}:")
            continue

        hm = HEADING_RE.match(line)
        if hm:
            seen_heading = True
            text = hm.group(2)
            tags: list[str] = []
            tm = TAGS_RE.search(text)
            if tm:
                tags = [t for t in tm.group(1).split(":") if t]
                text = text[: tm.start()].rstrip()
            headings.append(Heading(len(hm.group(1)), text, tags))
            out.append(f"{'*' * len(hm.group(1))} {text}")
            i += 1
            continue

        # Org comments -- every one is an enumerated exception
        if line.startswith("#") and not line.startswith("#+"):
            if lineno not in comment_actions:
                raise ConversionError(
                    f"{rel}:{lineno}: unhandled org comment: {line[:60]!r}"
                )
            action, level = comment_actions[lineno]
            stripped = line.lstrip("#").strip()
            if action == "heading":
                assert level is not None
                headings.append(Heading(level, stripped))
                out.append(f"{'*' * level} {stripped}")
            elif action == "listitem":
                out.append(f"- {stripped}")
            elif action == "htmlcomment":
                token = f"@@PKMBLOCK{len(note.blocks)}@@"
                note.blocks.append(f"<!-- {stripped} -->")
                out.extend(["", token, ""])
            else:
                raise ConversionError(f"{rel}:{lineno}: bad action {action!r}")
            i += 1
            continue

        # Org checkboxes are uppercase; a lowercase [x] is not a checkbox to
        # pandoc and comes out as literal \[x\].  The intent is plainly
        # "checked", and [x] *is* the markdown spelling, so normalise it.
        out.append(re.sub(r"^(\s*[-+*]\s*)\[x\]", r"\1[X]", line))
        i += 1

    note.headings = headings
    note.body = out
    return note


def _read_drawer(raw: list[str], i: int, rel: Path, lineno: int) -> tuple[list[str], int]:
    body: list[str] = []
    j = i + 1
    while j < len(raw):
        if raw[j].strip() == ":END:":
            return body, j + 1
        body.append(raw[j])
        j += 1
    raise ConversionError(f"{rel}:{lineno}: unterminated drawer")


BACKLINK_LINE_RE = re.compile(
    r"^\[([^\]]+)\]\s*<-\s*\[\[id:([^\]]+)\]\[<?([^\]>]+)>?\]\]$"
)


def _backlinks_block(
    body: list[str], rel: Path, index: dict[str, tuple[Path, str | None]]
) -> str:
    """Render a :BACKLINKS: drawer as a delimited, rewritable HTML block.

    The drawer's format is fixed -- `[stamp] <- [[id:UUID][<date>]]` -- so it
    is parsed exactly.  Running the generic timestamp/link rewriters over it
    would strip the brackets *inside* the link before the link is recognised.
    """
    items = []
    for line in body:
        line = line.strip()
        if not line:
            continue
        m = BACKLINK_LINE_RE.match(line)
        if not m:
            items.append("- " + line)
            continue
        stamp, uuid, label = m.groups()
        hit = index.get(uuid)
        if hit:
            path, anchor = hit
            href = _relpath(rel, path) + (f"#^{anchor}" if anchor else "")
            items.append(f"- {stamp} <- [{label}]({href})")
        else:
            items.append(f"- {stamp} <- {label}")
    return "<!-- backlinks -->\n" + "\n".join(items) + "\n<!-- /backlinks -->"


# --------------------------------------------------------------------------
# Link and timestamp rewriting (org space, before pandoc)
# --------------------------------------------------------------------------


def unbracket(text: str) -> str:
    """`<2026-02-13 Fri>` / `[2026-02-14 Sat 08:19]` -> plain text."""
    text = ACTIVE_TS_RE.sub(r"\1", text)
    return INACTIVE_TS_RE.sub(r"\1", text)


def rewrite_links(
    text: str, rel: Path, index: dict[str, tuple[Path, str | None]], report: list | None = None
) -> str:
    def repl(m: re.Match) -> str:
        target, desc = m.group(1), m.group(2)

        if target.startswith("id:"):
            uuid = target[3:]
            hit = index.get(uuid)
            if not hit:
                if report is not None:
                    report.append(f"{rel}: unresolved id: {uuid}")
                return desc or uuid
            path, anchor = hit
            href = _relpath(rel, path) + (f"#^{anchor}" if anchor else "")
            return f"[{desc or path.stem}]({href})"

        if target.startswith("file:"):
            href = target[5:]
            if href.endswith(".org"):
                href = href[:-4] + ".md"
            label = desc or Path(href).name
            if Path(href).suffix.lower() in IMAGE_EXTS:
                return f"![{label}]({href})"
            return f"[{label}]({href})"

        if target.startswith(("http://", "https://", "mailto:")):
            return f"[{desc or target}]({target})"

        # One note has [[https:www.…]] -- a real URL missing its slashes.
        mal = re.match(r"^(https?):(?!//)(.+)$", target)
        if mal:
            fixed = f"{mal.group(1)}://{mal.group(2)}"
            if report is not None:
                report.append(f"{rel}: repaired malformed URL {target} -> {fixed}")
            return f"[{desc or fixed}]({fixed})"

        # Bare [[target]] -- enumerated, never guessed at silently
        if target in BARE_LINK_TARGETS:
            return f"[{desc or target}]({BARE_LINK_TARGETS[target]})"
        if target.startswith(("/", "~")):
            return f"[{desc or Path(target).name}]({target})"
        if report is not None:
            report.append(f"{rel}: bare link left as text: [[{target}]]")
        return desc or target

    return ORG_LINK_RE.sub(repl, text)


def relax_escapes(md: str) -> str:
    """Undo pandoc escapes that CommonMark does not actually need.

    `\\#` is the important one: pandoc escapes every inline #tag, and 130 of
    this corpus's 135 tags come back that way -- which would break tag search
    in both Emacs and the web PKM.  Only a line-leading `#` is structural, so
    mid-line hashes are safe to restore.  `$` is never structural in GFM.
    """
    out = []
    for line in md.split("\n"):
        m = re.match(r"^(\s*\\#)", line)
        lead, rest = (m.group(1), line[m.end() :]) if m else ("", line)
        out.append(lead + rest.replace("\\#", "#").replace("\\$", "$"))
    return "\n".join(out)


SPURIOUS_RE = re.compile(
    r'<span class="spurious-link" target="([^"]*)">\*?([^<*]*)\*?</span>'
)


def unwrap_spurious_links(md: str) -> str:
    """Turn pandoc's `spurious-link` spans back into plain markdown.

    Pandoc emits these for bare org links like `[[~/.config/…]]` that it
    cannot classify.  Raw HTML in the note body helps nobody.
    """

    def repl(m: re.Match) -> str:
        target, label = m.group(1), m.group(2) or m.group(1)
        if " " in target:
            return label  # a page name like [[Nadia Dixson]], not a location
        return f"[{label}]({target})"

    return SPURIOUS_RE.sub(repl, md)


_SRC_ROOT: Path | None = None


def _root_relative_fix(rel: Path, href: str) -> str | None:
    """If a relative link is broken but resolves from the corpus root, say so.

    Two links in the corpus were already broken in org -- `[[file:README.org]]`
    written from journals/, and `[[file:journals/…]]` written from pages/ --
    where the intent is plainly root-relative.
    """
    if _SRC_ROOT is None:
        return None
    target, _, frag = href.partition("#")
    if (_SRC_ROOT / rel.parent / target).exists():
        return None  # already fine
    org_equiv = target[:-3] + ".org" if target.endswith(".md") else target
    if not (_SRC_ROOT / org_equiv).exists():
        return None
    import os

    fixed = os.path.relpath(Path(target), rel.parent).replace("\\", "/")
    return fixed + (f"#{frag}" if frag else "")


MD_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def rewrite_md_links(md: str, rel: Path, index, report: list) -> str:
    """Resolve pandoc's `[desc](id:UUID)` / `[desc](file.org)` into real paths."""

    def repl(m: re.Match) -> str:
        bang, desc, href = m.group(1), m.group(2), m.group(3)
        inner = href[1:-1] if href.startswith("<") and href.endswith(">") else href

        if inner.startswith("id:"):
            uuid = inner[3:]
            hit = index.get(uuid)
            if not hit:
                report.append(f"{rel}: unresolved id: {uuid}")
                return desc or uuid
            path, a = hit
            target = _relpath(rel, path) + (f"#^{a}" if a else "")
            return f"[{desc or path.stem}]({target})"

        if inner.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)

        if inner.startswith("help:"):
            return f"`{desc or inner[5:]}`"

        # One note has [[https:www.…]] -- a real URL missing its slashes.
        mal = re.match(r"^(https?):(?!//)(.+)$", inner)
        if mal:
            fixed = f"{mal.group(1)}://{mal.group(2)}"
            report.append(f"{rel}: repaired malformed URL {inner} -> {fixed}")
            return f"[{desc or fixed}]({fixed})"

        if desc.startswith("file:"):
            desc = Path(desc[5:]).stem
        if inner.endswith(".org"):
            inner = inner[:-4] + ".md"
        if not inner.startswith(("/", "~", "#")):
            fixed = _root_relative_fix(rel, inner)
            if fixed:
                report.append(f"{rel}: repaired broken link {inner} -> {fixed}")
                inner = fixed

        # A bare target with no scheme, no path, and no file behind it is not
        # a link at all -- [[Nadia Dixson]], [[resource:N]].  Naming it as one
        # would only manufacture dead links.
        if not inner.startswith(("/", "~", ".")) and "/" not in inner:
            on_disk = _SRC_ROOT is not None and (
                (_SRC_ROOT / rel.parent / inner).exists()
                or (_SRC_ROOT / rel.parent / (inner[:-3] + ".org")).exists()
            )
            if not on_disk:
                if inner in BARE_LINK_TARGETS:
                    return f"[{desc or inner}]({BARE_LINK_TARGETS[inner]})"
                report.append(f"{rel}: bare link left as text: [[{inner}]]")
                return desc or inner
        if Path(inner).suffix.lower() in IMAGE_EXTS and not bang:
            bang = "!"
        return f"{bang}[{desc}]({inner})"

    out, fence = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        # protect inline code spans, rewrite around them
        spans: list[str] = []

        def hide(m: re.Match) -> str:
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        line = re.sub(r"`[^`]*`", hide, line)
        line = MD_LINK_RE.sub(repl, line)
        line = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], line)
        out.append(line)
    return "\n".join(out)


def _relpath(src: Path, dst: Path) -> str:
    import os

    return os.path.relpath(dst, src.parent).replace("\\", "/")


# --------------------------------------------------------------------------
# Heading levels
# --------------------------------------------------------------------------


def adjust_levels(note: Note, report: list) -> list[str]:
    """Apply the per-kind heading rule, in org space, before pandoc.

    Also prunes the dropped date wrapper from note.headings, so the heading
    list stays aligned with what pandoc will actually emit.
    """
    lines = note.body
    if not is_journal(note.rel):
        return lines  # pages and top-level files are level-preserving

    wrapper = _find_wrapper(note, report)
    out: list[str] = []
    survivors: list[Heading] = []
    promoting = False
    found = False
    hi = 0
    for line in lines:
        m = HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue
        level, text = len(m.group(1)), m.group(2)
        heading = note.headings[hi]
        hi += 1
        if wrapper is not None and not found and level == 1 and text == wrapper:
            found = True
            promoting = True
            continue  # drop the date wrapper itself, and its Heading entry
        if promoting and level == 1:
            promoting = False  # a sibling of the wrapper: leave it alone
        if promoting or wrapper is None:
            level -= 1
            if level < 1:
                raise ConversionError(f"{note.rel}: promotion would zero heading {text!r}")
        heading.level = level
        survivors.append(heading)
        out.append(f"{'*' * level} {text}")
    if wrapper is not None and not found:
        raise ConversionError(f"{note.rel}: date wrapper {wrapper!r} vanished")
    note.headings = survivors
    return out


WRAPPER_TEXT_RE = re.compile(r"^<?(\d{4}-\d{2}-\d{2})|(\d{4}-\d{2}-\d{2})>?$")


def _find_wrapper(note: Note, report: list) -> str | None:
    """The date-wrapper heading text, or None when the journal has no wrapper.

    A wrapper is the first level-1 heading carrying a date -- which covers
    `<2026-04-16 Wed>` and `Wednesday 2026-04-16` alike.  When that date
    disagrees with the filename the file itself is mislabelled; convert it
    anyway and report, rather than guessing which one is right.
    """
    firsts = [h for h in note.headings if h.level == 1]
    if not firsts:
        return None
    first = firsts[0]
    found = DATE_IN_NAME_RE.search(first.text)
    if not found:
        raise ConversionError(
            f"{note.rel}: first level-1 heading {first.text!r} carries no date; "
            "handle explicitly"
        )
    if note.date and found.group(1) != note.date:
        report.append(
            f"{note.rel}: wrapper is dated {found.group(1)} but the file is named "
            f"{note.date} -- mislabelled journal, converted as-is"
        )
    return first.text


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------


def run_pandoc(org_text: str) -> str:
    # H:6 is essential.  Org's export default is H:3 and pandoc's reader
    # honours it, silently turning every heading below level 3 into an
    # ordered list -- 102 headings in this corpus.  ^:{} mirrors the
    # org-use-sub-superscripts setting in init-org.el, without which
    # identifiers like ORG_DIR come out as ORG<sub>DIR</sub>.
    proc = subprocess.run(
        ["pandoc", "-f", "org", "-t", "gfm", "--wrap=none"],
        input="#+OPTIONS: H:6 ^:{}\n" + org_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ConversionError(f"pandoc failed: {proc.stderr.strip()}")
    return proc.stdout


# A backslash before punctuation -- or at end of line, as in a pasted shell
# command's line continuation -- makes pandoc's org reader look for a LaTeX
# delimiter and abort the parse.  Every instance in this corpus is literal
# text -- `C\*`, the `¯\_(ツ)_/¯` shrug, an ASCII arrow, a password holding
# `\]` -- so hide them from pandoc and restore the exact bytes afterwards.
BACKSLASH_ESCAPE_RE = re.compile(r"\\[^A-Za-z0-9\s]|\\$", re.M)


def protect_escapes(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def hide(m: re.Match) -> str:
        saved.append(m.group(0))
        return f"@@PKMESC{len(saved) - 1}@@"

    return BACKSLASH_ESCAPE_RE.sub(hide, text), saved


def restore_escapes(text: str, saved: list[str], rel: Path) -> str:
    for i, seq in enumerate(saved):
        token = f"@@PKMESC{i}@@"
        if token not in text:
            raise ConversionError(f"{rel}: escape token {token} ({seq!r}) lost in pandoc")
        text = text.replace(token, seq)
    return text


ORG_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s")
MD_OL_RE = re.compile(r"^(\s*)(\d+)\.\s")


def restore_list_numbers(md: str, org_lines: list[str], rel: Path, report: list) -> str:
    """Put back ordered-list numbers that pandoc restarted at 1.

    A bullet list between two numbered items splits them into separate lists,
    so pandoc renumbers each from 1 -- which silently flattens a ranked list
    ("Best additions, in order: 1..6") into six 1s.
    """
    want = [int(m.group(2)) for line in org_lines if (m := ORG_OL_RE.match(line))]
    if not want:
        return md
    out, seen = [], 0
    for line in md.split("\n"):
        m = MD_OL_RE.match(line)
        if m and seen < len(want):
            out.append(f"{m.group(1)}{want[seen]}. {line[m.end():]}")
            seen += 1
            continue
        out.append(line)
    if seen != len(want):
        report.append(
            f"{rel}: ordered-list items drifted ({seen} in markdown, "
            f"{len(want)} in org); numbering left as pandoc emitted it"
        )
        return md
    return "\n".join(out)


def frontmatter(note: Note) -> str:
    def esc(v: str) -> str:
        return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = ["---", f"title: {esc(note.title or note.rel.stem)}"]
    if note.file_id:
        lines.append(f"id: {note.file_id}")
    if note.date:
        lines.append(f"date: {note.date}")
    if note.filetags:
        lines.append("tags: [" + ", ".join(note.filetags) + "]")
    lines.append("---")
    return "\n".join(lines)


def apply_anchors_and_tags(md: str, note: Note) -> str:
    """Walk markdown headings in order and re-attach anchors and tags.

    Pandoc preserves heading order, so index alignment is safe and avoids
    round-tripping heading text through pandoc's escaping.
    """
    carry = [h for h in note.headings]
    out: list[str] = []
    n = 0
    for line in md.splitlines():
        m = re.match(r"^(#+)\s+(.*?)\s*$", line)
        if m and n < len(carry):
            h = carry[n]
            n += 1
            suffix = "".join(f" #{t}" for t in h.tags)
            if h.anchor:
                suffix += f" ^{h.anchor}"
            out.append(line.rstrip() + suffix)
            continue
        out.append(line)
    if n != len(carry):
        raise ConversionError(
            f"{note.rel}: heading count drifted ({n} in markdown, {len(carry)} in org)"
        )
    return "\n".join(out)


def convert(abs_path: Path, rel: Path, index, report: list) -> str:
    note = parse(abs_path, rel, index, report)
    if note.file_id is None:
        # Without an ID a note cannot be renamed safely or linked reliably,
        # which is the whole reason file-level IDs survive the conversion.
        note.file_id = note_id("", rel)
        report.append(f"{rel}: had no :ID:; minted {note.file_id}")
    lines = adjust_levels(note, report)

    body = "\n".join(lines)
    body = unbracket(body)
    body, escapes = protect_escapes(body)

    # Links are rewritten *after* pandoc.  Emitting markdown links into the
    # org source would only feed them back through the org reader, where
    # `~/path` is code markup and `=[[file:x]]=` is verbatim -- both of which
    # pandoc is right to mangle, and neither of which we want touched.
    md = run_pandoc(body)
    md = relax_escapes(md)
    md = restore_escapes(md, escapes, rel)
    md = unwrap_spurious_links(md)
    md = rewrite_md_links(md, rel, index, report)
    md = restore_list_numbers(md, lines, rel, report)
    md = apply_anchors_and_tags(md, note)

    for i, block in enumerate(note.blocks):
        token = f"@@PKMBLOCK{i}@@"
        if token not in md:
            raise ConversionError(f"{rel}: block token {token} lost in pandoc")
        md = md.replace(token, block)

    # pandoc emits `1.  item`; one space is conventional
    md = re.sub(r"^(\s*\d+\.)\s\s+", r"\1 ", md, flags=re.M)

    for line in md.splitlines():
        m = re.match(r"^(#+)\s", line)
        if m and len(m.group(1)) > MAX_HEADING_LEVEL:
            raise ConversionError(f"{rel}: heading deeper than h{MAX_HEADING_LEVEL}: {line[:50]}")

    return frontmatter(note) + "\n\n" + md.strip() + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    global _SRC_ROOT
    src, dst = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    _SRC_ROOT = src
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    files = []
    for p in sorted(src.rglob("*.org")):
        if "sync-conflict" in p.name or ".backup-" in p.name:
            # Artifacts, not notes -- and a backup shares its original's :ID:,
            # which would put a duplicate ID in the index.
            print(f"  skipping artifact: {p.relative_to(src)}")
            continue
        files.append((p, p.relative_to(src)))

    index = build_id_index(files)
    print(f"indexed {len(index)} IDs across {len(files)} files")

    report: list[str] = []
    errors: list[str] = []
    for abs_path, rel in files:
        try:
            md = convert(abs_path, rel, index, report)
        except ConversionError as e:
            errors.append(str(e))
            continue
        out = dst / rel.with_suffix(".md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

    if (src / "assets").is_dir():
        shutil.copytree(src / "assets", dst / "assets")

    print(f"\nconverted {len(files) - len(errors)}/{len(files)}")
    if report:
        print(f"\nnotes ({len(report)}):")
        for r in report:
            print(f"  {r}")
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
