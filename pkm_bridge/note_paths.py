"""Shared helpers for locating, naming and ordering note files.

Tools report note paths in the prefixed form ('org:journals/2026-09-27.md',
'logseq:Personal/pages/Foo.md') that read_note, write_file and the editor
accept, rather than absolute server paths.
"""

import re
from datetime import datetime
from pathlib import Path

# ripgrep arguments selecting note files. Together with rg's defaults (skip
# hidden dirs such as .pkm/ and .git/, honor .gitignore/.ignore) this is the
# set of files the embedding scanner indexes and the search tools search.
RG_NOTE_FILTER = ["--type-add", "notes:*.{org,md}", "--type", "notes"]

_DATE_IN_NAME = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")


def note_roots(org_dir: Path | str | None, logseq_dir: Path | str | None) -> list[tuple[str, Path]]:
    """(prefix, resolved dir) for each configured note directory, org first."""
    roots = []
    for prefix, d in (("org", org_dir), ("logseq", logseq_dir)):
        if d:
            roots.append((prefix, Path(d).expanduser().resolve()))
    return roots


def display_path(
    path: Path | str, org_dir: Path | str | None, logseq_dir: Path | str | None
) -> str:
    """Prefixed form of an absolute note path; unchanged if outside the note dirs."""
    p = Path(path)
    try:
        resolved = p.expanduser().resolve()
    except (OSError, RuntimeError):
        return str(path)
    for prefix, root in note_roots(org_dir, logseq_dir):
        if resolved.is_relative_to(root):
            return f"{prefix}:{resolved.relative_to(root)}"
    return str(path)


def resolve_note_path(spec: str, org_dir: Path | str | None, logseq_dir: Path | str | None) -> Path:
    """Resolve a user-supplied path to an absolute path inside a note directory.

    Accepts 'org:rel', 'logseq:rel', absolute paths, and relative paths
    (tried against the org dir, then Logseq). Raises ValueError if the path
    lies outside every note directory.
    """
    roots = note_roots(org_dir, logseq_dir)
    prefix, sep, rest = spec.partition(":")
    if sep and prefix in ("org", "logseq"):
        root = dict(roots).get(prefix)
        if root is None:
            raise ValueError(f"'{prefix}:' directory is not configured")
        candidates = [(root / rest).resolve()]
    else:
        p = Path(spec).expanduser()
        if p.is_absolute():
            candidates = [p.resolve()]
        else:
            candidates = [(root / p).resolve() for _, root in roots]

    contained = [c for c in candidates if any(c.is_relative_to(r) for _, r in roots)]
    if not contained:
        raise ValueError(f"Path '{spec}' is outside the note directories")
    # Prefer a candidate that exists; otherwise report the first
    return next((c for c in contained if c.exists()), contained[0])


def filename_date(path: Path | str) -> str | None:
    """YYYY-MM-DD from a journal-style filename (2026-09-27.md, 2026_09_27.md)."""
    m = _DATE_IN_NAME.search(Path(path).name)
    return "-".join(m.groups()) if m else None


def is_journal(path: Path | str) -> bool:
    return "journals" in Path(path).parts


def recency(path: Path) -> float:
    """Sort key, larger is newer: the date in the filename if any, else mtime."""
    date = filename_date(path)
    if date:
        try:
            return datetime.strptime(date, "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
