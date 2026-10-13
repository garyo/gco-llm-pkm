"""Command line for query blocks; Emacs refreshes a buffer with `refresh-text --json`.

Run from the repository root (or with it on PYTHONPATH):

    python -m pkm_bridge.queries refresh-text --root ~/Documents/org-notes --path pages/x.md < x.md
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pkm_bridge.queries import Corpus, preview, refresh_text


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, required=True, help="the notes directory")
    common.add_argument("--today", type=date.fromisoformat, default=None, help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="python -m pkm_bridge.queries")
    commands = parser.add_subparsers(dest="command", required=True)
    text_cmd = commands.add_parser(
        "refresh-text", parents=[common], help="refresh the note on stdin, print it"
    )
    text_cmd.add_argument("--path", required=True, help="the note's path under --root")
    text_cmd.add_argument("--json", action="store_true", help="print a JSON report with the text")
    preview_cmd = commands.add_parser(
        "preview", parents=[common], help="print the results a query would get"
    )
    preview_cmd.add_argument("--path", required=True, help="the note the query would be in")
    preview_cmd.add_argument("query")
    files_cmd = commands.add_parser(
        "refresh", parents=[common], help="rewrite notes in place (all with queries if none given)"
    )
    files_cmd.add_argument("paths", nargs="*", help="notes, relative to --root")
    args = parser.parse_args(argv)

    root = args.root.expanduser()
    corpus = Corpus(root)
    today = args.today or date.today()
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    if args.command == "refresh-text":
        result = refresh_text(sys.stdin.read(), _relative(root, args.path), corpus, today)
        if args.json:
            report = {**asdict(result), "blocks": [asdict(b) for b in result.blocks]}
            json.dump(report, sys.stdout, ensure_ascii=False)
        else:
            sys.stdout.write(result.text)
        return 0

    if args.command == "preview":
        lines, report = preview(args.query, _relative(root, args.path), corpus, today)
        print("\n".join(lines).strip("\n"))
        return 1 if report.error else 0

    failed = False
    for rel in [_relative(root, p) for p in args.paths] or corpus.pages_with_queries():
        path = root / rel
        try:
            result = refresh_text(path.read_text(encoding="utf-8"), rel, corpus, today)
            if result.changed:
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_text(result.text, encoding="utf-8")
                os.replace(tmp, path)
        except (OSError, RuntimeError) as e:
            print(f"{rel}: {e}", file=sys.stderr)
            failed = True
            continue
        counts = ", ".join(str(b.error or b.entries) for b in result.blocks)
        print(f"{rel}: {'written' if result.changed else 'unchanged'} ({counts})")
    return 1 if failed else 0


def _relative(root: Path, path: str) -> str:
    p = Path(path).expanduser()
    return p.resolve().relative_to(root.resolve()).as_posix() if p.is_absolute() else p.as_posix()


if __name__ == "__main__":
    sys.exit(main())
