"""Org-mode link resolution: attachment paths, org-id lookup, link rewriting."""

import re
import subprocess
from pathlib import Path




def resolve_attachment_path(
    org_dir: Path, org_id: str, filename: str
) -> Path | None:
    """Resolve an org-attach attachment to its filesystem path.

    Emacs org-attach stores files at:
        <org-dir>/data/<ID[0:2]>/<ID[2:]>/<filename>

    Args:
        org_dir: Root org-mode directory
        org_id: The :ID: property of the enclosing heading (UUID)
        filename: The attachment filename

    Returns:
        Resolved Path if found, else None
    """
    # Validate ID format: hex digits and hyphens only
    if not re.fullmatch(r"[A-Fa-f0-9-]+", org_id):
        return None

    if len(org_id) < 3:
        return None

    # Emacs org-attach splits ID at position 2 (keeping hyphens intact)
    rel = Path("data") / org_id[:2] / org_id[2:] / filename

    # Search in org_dir root and common subdirectories
    search_roots = [org_dir]
    for subdir in ("journals", "pages"):
        candidate = org_dir / subdir
        if candidate.is_dir():
            search_roots.append(candidate)

    for root in search_roots:
        candidate = root / rel
        if candidate.is_file():
            return candidate

    # Fallback: glob for the file anywhere under org_dir/data/
    pattern = f"data/{org_id[:2]}/{org_id[2:]}/{filename}"
    matches = list(org_dir.rglob(pattern))
    if matches:
        return matches[0]

    return None


def extract_heading_id(lines: list[str], target_line: int) -> str | None:
    """Walk backward from target_line to find the enclosing heading's :ID: property.

    Args:
        lines: All lines of the org file (no trailing newlines)
        target_line: 0-indexed line number to start searching from

    Returns:
        The UUID string if found, else None
    """
    # Walk backward to find the nearest heading
    heading_line = None
    for i in range(min(target_line, len(lines) - 1), -1, -1):
        if lines[i].startswith("*"):
            heading_line = i
            break

    if heading_line is None:
        # No heading found — check for file-level :ID: in properties at top
        heading_line = -1

    # Now look for :PROPERTIES: drawer after the heading
    start = heading_line + 1
    if start >= len(lines):
        return None

    # Skip blank lines between heading and properties
    idx = start
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    if idx >= len(lines) or lines[idx].strip() != ":PROPERTIES:":
        return None

    # Read properties until :END:
    idx += 1
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == ":END:":
            break
        match = re.match(r":ID:\s+(.+)", stripped)
        if match:
            return match.group(1).strip()
        idx += 1

    return None


def resolve_org_id_to_file(
    org_dir: Path,
    uuid: str,
    logseq_dir: Path | None = None,
) -> tuple[str, int] | None:
    """Find the org file containing :ID: <uuid> using ripgrep.

    Returns:
        Tuple of (org:relative/path.org, line_number) or None
    """
    if not re.fullmatch(r"[A-Fa-f0-9-]+", uuid):
        return None

    search_dirs = [str(org_dir)]
    if logseq_dir and logseq_dir.exists():
        search_dirs.append(str(logseq_dir))

    cmd = [
        "rg",
        "--json",
        "-i",
        "--type-add", "org:*.org",
        "--type", "org",
        "--max-count", "1",
        f":ID:\\s+{re.escape(uuid)}",
    ] + search_dirs

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            import json
            data = json.loads(line)
            if data.get("type") == "match":
                match_data = data["data"]
                file_path = Path(match_data["path"]["text"])
                line_num = match_data["line_number"]

                # Build org: prefixed relative path
                try:
                    rel = file_path.relative_to(org_dir)
                    return (f"org:{rel}", line_num)
                except ValueError:
                    pass
                if logseq_dir:
                    try:
                        rel = file_path.relative_to(logseq_dir)
                        return (f"logseq:{rel}", line_num)
                    except ValueError:
                        pass
                # Fallback: return absolute
                return (str(file_path), line_num)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def rewrite_org_links_to_markdown(
    text: str,
    lines: list[str],
    section_start: int,
    org_dir: Path,  # noqa: ARG001 — kept for future use
) -> str:
    """Rewrite org-mode links in text to markdown equivalents.

    - [[attachment:file][desc]] -> ![desc](/api/org-attachment/ID/file) (images)
    - [[attachment:file][desc]] -> [desc](/api/org-attachment/ID/file) (non-images)
    - [[id:UUID][desc]] -> [desc](org-id:UUID)

    Args:
        text: The text containing org links
        lines: All lines of the source org file
        section_start: 0-indexed line where this section starts
        org_dir: Root org directory (reserved for future use)

    Returns:
        Text with rewritten links
    """
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}

    # Find the heading ID for attachment resolution
    heading_id = extract_heading_id(lines, section_start)

    def replace_link(m: re.Match) -> str:
        target = m.group(1)
        desc = m.group(2) if m.group(2) else target

        # Handle attachment: links
        if target.startswith("attachment:"):
            filename = target[len("attachment:"):]
            if not heading_id:
                # Can't resolve without heading ID, leave as-is
                return m.group(0)
            ext = Path(filename).suffix.lower()
            url = f"/api/org-attachment/{heading_id}/{filename}"
            if ext in IMAGE_EXTS:
                return f"![{desc}]({url})"
            else:
                return f"[{desc}]({url})"

        # Handle id: links
        if target.startswith("id:"):
            uuid = target[3:]
            return f"[{desc}](org-id:{uuid})"

        # Leave other links as-is
        return m.group(0)

    # Match org links: [[target][description]] or [[target]]
    pattern = r"\[\[([^\]]+)\](?:\[([^\]]+)\])?\]"
    return re.sub(pattern, replace_link, text)
