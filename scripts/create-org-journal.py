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

    date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")

    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.", file=sys.stderr)
        return 1

    journal_dir = org_dir / "journals"
    filepath = journal_dir / f"{date_str}.org"

    if filepath.exists():
        print(filepath)
        return 2

    journal_dir.mkdir(exist_ok=True)

    dow = date.strftime("%a")
    org_uuid = str(uuid.uuid4()).upper()

    filepath.write_text(
        f"#+title: {date_str}\n"
        f"\n"
        f"* <{date_str} {dow}>\n"
        f":PROPERTIES:\n"
        f":ID:       {org_uuid}\n"
        f":END:\n\n"
    )

    print(filepath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
