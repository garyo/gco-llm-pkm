#!/usr/bin/env python3
"""Create the Markdown journal file for a given date.

Usage: create-org-journal.py <ORG_DIR> [YYYY-MM-DD]
If no date given, defaults to today.
Outputs the full path of the created (or existing) file.
Exit code 0 if created, 1 on error, 2 if file already exists.

The assistant uses the journal_append tool instead; this is for manual use.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pkm_bridge.journal import journal_rel_path, journal_template  # noqa: E402


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
        filepath = org_dir / journal_rel_path(date_str)
    except ValueError:
        print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.", file=sys.stderr)
        return 1

    filepath.parent.mkdir(exist_ok=True)
    try:
        with open(filepath, "x", encoding="utf-8") as f:
            f.write(journal_template(date_str))
    except FileExistsError:
        print(filepath)
        return 2

    print(filepath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
