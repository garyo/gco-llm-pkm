#!/usr/bin/env python3
"""Create an org-mode journal file for a given date with proper structure.

Usage: create-org-journal.py <ORG_DIR> [YYYY-MM-DD]
If no date given, defaults to today.
Outputs the full path of the created (or existing) file.
Exit code 0 if created, 1 on error, 2 if file already exists.
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: create-org-journal.py <ORG_DIR> [YYYY-MM-DD]", file=sys.stderr)
        return 1

    org_dir = Path(sys.argv[1])
    if not org_dir.is_dir():
        print(f"Error: ORG_DIR does not exist: {org_dir}", file=sys.stderr)
        return 1

    args = sys.argv[2:]
    fmt = None
    if "--format" in args:
        i = args.index("--format")
        if i + 1 >= len(args) or args[i + 1] not in ("org", "md"):
            print("Error: --format takes 'org' or 'md'.", file=sys.stderr)
            return 1
        fmt = args[i + 1]
        del args[i : i + 2]

    date_str = args[0] if args else datetime.now().strftime("%Y-%m-%d")

    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.", file=sys.stderr)
        return 1

    journal_dir = org_dir / "journals"
    if fmt is None:
        fmt = "md" if any(journal_dir.glob("*.md")) else "org"
    filepath = journal_dir / f"{date_str}.{fmt}"

    if filepath.exists():
        print(filepath)
        return 2

    journal_dir.mkdir(exist_ok=True)

    dow = date.strftime("%a")
    note_id = str(uuid.uuid4()).upper()

    if fmt == "md":
        # The org date wrapper collapses into frontmatter; sections start at #.
        filepath.write_text(
            f"---\n"
            f'title: "{date_str}"\n'
            f"id: {note_id}\n"
            f"date: {date_str}\n"
            f"---\n\n"
        )
    else:
        filepath.write_text(
            f"#+title: {date_str}\n"
            f"\n"
            f"* <{date_str} {dow}>\n"
            f":PROPERTIES:\n"
            f":ID:       {note_id}\n"
            f":END:\n\n"
        )

    print(filepath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
