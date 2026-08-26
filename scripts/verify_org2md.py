#!/usr/bin/env python3
"""Check an org->markdown conversion against its source.

Compares each file structurally (words, headings, links, images, checkboxes),
then checks every emitted link and image actually resolves.  Prints what
drifted; a clean run means the conversion preserved the corpus.

Usage:  verify_org2md.py SRC_DIR DST_DIR
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ORG_LINK_RE = re.compile(r"\[\[([^\]]+)\](?:\[([^\]]*)\])?\]")
MD_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)]+)\)")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}
BLOCK_RE = re.compile(r"^\s*#\+(begin|end)_(\w+)", re.I)
INLINETASK_MIN = 15
WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Org comments deliberately revealed as markdown headings by the converter.
sys.path.insert(0, str(Path(__file__).parent))
from org2md import COMMENT_ACTIONS  # noqa: E402

REVEALED_HEADINGS = {
    path: sum(1 for a, _ in actions.values() if a == "heading")
    for path, actions in COMMENT_ACTIONS.items()
}

# Markup that legitimately disappears: it is structure, not prose.
DROPPED_ORG = re.compile(
    r"^\s*(:[A-Z][A-Z0-9_]*:|#\+\w+:.*|\*{%d,}.*)\s*$" % INLINETASK_MIN
)


FENCE_RE = re.compile(r"^\s*(```|~~~)")


def strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans.

    Neither is prose, and both hold things that merely look like links --
    e.g. ffprobe output containing `[0x1](und)`.
    """
    out, fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fence = not fence
            out.append("")
            continue
        out.append("" if fence else re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def strip_org_code(text: str) -> str:
    """Same for org: #+begin_…/#+end_… blocks and =verbatim=/~code~ spans."""
    out, depth = [], 0
    for line in text.splitlines():
        m = BLOCK_RE.match(line)
        if m:
            depth += 1 if m.group(1).lower() == "begin" else -1
            depth = max(0, depth)
            out.append("")
            continue
        if depth:
            out.append("")
            continue
        line = re.sub(r"=[^=\s][^=]*=", "", line)
        out.append(re.sub(r"~[^~\s][^~]*~", "", line))
    return "\n".join(out)


DATE_WRAPPER_RE = re.compile(r"^\*\s+<?\d{4}-\d{2}-\d{2}[^>]*>?\s*(:[\w:@#%]+:)?\s*$")


def org_words(text: str, journal: bool) -> list[str]:
    out = []
    dropped_wrapper = False
    for line in text.splitlines():
        if DROPPED_ORG.match(line) or re.match(r"^\s*:(ID|END):", line):
            continue
        # A journal's date wrapper moves into frontmatter, which md_words skips.
        if journal and not dropped_wrapper and DATE_WRAPPER_RE.match(line):
            dropped_wrapper = True
            continue
        line = ORG_LINK_RE.sub(lambda m: m.group(2) or "", line)
        line = re.sub(r"(?:https?|mailto):\S+", "", line)
        line = re.sub(r"^(\s*[-+*]\s*)\[[ xX-]\]", r"\1", line)
        line = re.sub(r"^\*+\s+", "", line)
        line = re.sub(r"\s+:[A-Za-z0-9_@#%:]+:$", "", line)
        out += WORD_RE.findall(line)
    return out


def md_words(text: str) -> list[str]:
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    out = []
    for line in body.splitlines():
        if line.strip().startswith("<!--"):
            continue
        line = re.sub(r"^(\s*[-+*]\s*)\[[ xX-]\]", r"\1", line)
        line = MD_LINK_RE.sub(lambda m: m.group(2), line)
        line = re.sub(r"<(?:https?|mailto):[^>]*>", "", line)  # bare autolinks
        line = re.sub(r"^#+\s+", "", line)
        line = re.sub(r"\s+\^[\w-]+$", "", line)
        out += WORD_RE.findall(line)
    return out


def count_org_headings(text: str) -> int:
    n, depth = 0, 0
    for line in text.splitlines():
        m = BLOCK_RE.match(line)
        if m:
            depth += 1 if m.group(1).lower() == "begin" else -1
            depth = max(0, depth)
            continue
        if depth:
            continue
        h = re.match(r"^(\*+)\s+\S", line)
        if h and len(h.group(1)) < INLINETASK_MIN:
            n += 1
    return n


def count_md_headings(text: str) -> int:
    n, fence = 0, False
    for line in re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence and re.match(r"^#+\s+\S", line):
            n += 1
    return n


def main() -> int:
    src, dst = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    problems: list[str] = []
    stats = {"files": 0, "words_org": 0, "words_md": 0, "links": 0, "images": 0}

    for org in sorted(src.rglob("*.org")):
        if "sync-conflict" in org.name:
            continue
        rel = org.relative_to(src)
        md_path = dst / rel.with_suffix(".md")
        if not md_path.exists():
            problems.append(f"MISSING OUTPUT  {rel}")
            continue
        stats["files"] += 1
        o, m = org.read_text(), md_path.read_text()
        o_prose, m_prose = strip_org_code(o), strip_code(m)

        ow, mw = org_words(o_prose, rel.parts[0] == "journals"), md_words(m_prose)
        stats["words_org"] += len(ow)
        stats["words_md"] += len(mw)
        # Only *loss* matters.  Markdown legitimately gains words (link text
        # pandoc unfolds, code the org side over-strips), so comparing totals
        # is noise; comparing multisets in the lossy direction is the question
        # actually worth asking -- did any prose fail to make it across?
        missing = Counter(ow) - Counter(mw)
        if sum(missing.values()) > max(2, 0.01 * len(ow)):
            top = ", ".join(f"{w}x{c}" for w, c in missing.most_common(6))
            problems.append(
                f"WORDS LOST  {rel}: {sum(missing.values())} of {len(ow)} -> {top}"
            )

        oh, mh = count_org_headings(o), count_md_headings(m)
        # Journals legitimately lose exactly one heading (the date wrapper),
        # and any org comment revealed as a heading legitimately adds one.
        expected = oh - 1 if rel.parts[0] == "journals" and oh else oh
        expected += REVEALED_HEADINGS.get(rel.as_posix(), 0)
        if mh != expected and mh != oh:
            problems.append(f"HEADINGS  {rel}: org={oh} md={mh} (expected {expected})")

        if not m.startswith("---\n"):
            problems.append(f"NO FRONTMATTER  {rel}")

        ocb = len(re.findall(r"^\s*[-+*]\s*\[[ xX]\]", o, re.M))
        mcb = len(re.findall(r"^\s*[-+*]\s*\[[ xX]\]", m, re.M))
        if mcb < ocb:
            problems.append(f"CHECKBOXES  {rel}: org={ocb} md={mcb}")

        for line in m.splitlines():
            if re.match(r"^#{7,}\s", line):
                problems.append(f"H7+  {rel}: {line[:50]}")

        for bang, _label, href in MD_LINK_RE.findall(m_prose):
            if href.startswith("<") and href.endswith(">"):
                href = href[1:-1]  # pandoc's autolink form
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            stats["images" if bang else "links"] += 1
            target, _, anchor = href.partition("#")
            if target.startswith(("/", "~")):
                continue  # absolute local paths, intentionally left alone
            resolved = (md_path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"DEAD LINK  {rel} -> {href}")
                continue
            if anchor.startswith("^"):
                if f" {anchor}" not in resolved.read_text():
                    problems.append(f"DEAD ANCHOR  {rel} -> {href}")

    print(f"files            {stats['files']}")
    print(f"words org/md     {stats['words_org']} / {stats['words_md']}")
    print(f"links checked    {stats['links']}")
    print(f"images checked   {stats['images']}")
    print(f"\nproblems: {len(problems)}")
    for p in problems[:60]:
        print(f"  {p}")
    if len(problems) > 60:
        print(f"  ... and {len(problems) - 60} more")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
