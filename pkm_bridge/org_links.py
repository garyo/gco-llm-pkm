"""Org-mode link resolution: org-id lookup and link rewriting."""

import re
import subprocess
from pathlib import Path

# [[file:../assets/NAME]] -- assets live in one flat dir, so the bare
# filename locates them regardless of how deep the linking file sits.
ASSET_LINK_RE = re.compile(r"file:(?:\.\./)*assets/([^/]+)$")


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
        "--type-add",
        "org:*.org",
        "--type",
        "org",
        "--max-count",
        "1",
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


def rewrite_org_links_to_markdown(text: str) -> str:
    """Rewrite org-mode links in text to markdown equivalents.

    - [[file:../assets/name][desc]] -> ![desc](/assets/name) (images)
    - [[file:../assets/name][desc]] -> [desc](/assets/name) (non-images)
    - [[id:UUID][desc]] -> [desc](org-id:UUID)

    Args:
        text: The text containing org links

    Returns:
        Text with rewritten links
    """
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}

    def replace_link(m: re.Match) -> str:
        target = m.group(1)
        desc = m.group(2) if m.group(2) else target

        asset = ASSET_LINK_RE.match(target)
        if asset:
            filename = asset.group(1)
            url = f"/assets/{filename}"
            # A bare link has no description; the filename reads better
            # as alt text than the whole org target does.
            alt = m.group(2) if m.group(2) else filename
            if Path(filename).suffix.lower() in IMAGE_EXTS:
                return f"![{alt}]({url})"
            return f"[{alt}]({url})"

        # Handle id: links
        if target.startswith("id:"):
            uuid = target[3:]
            return f"[{desc}](org-id:{uuid})"

        # Leave other links as-is
        return m.group(0)

    # Match org links: [[target][description]] or [[target]]
    pattern = r"\[\[([^\]]+)\](?:\[([^\]]+)\])?\]"
    return re.sub(pattern, replace_link, text)
