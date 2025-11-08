#!/usr/bin/env -S uv run --script
"""
find_references - Find all references to a term across org-mode and logseq PKM
Usage: find_references <search_term> [--context=N] [--files-only]
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# Configuration
ORG_DIR = Path("/data/org-agenda")
LOGSEQ_DIR = Path("/data/logseq")

if os.path.exists("/Users/garyo/Documents/org-agenda"):
    ORG_DIR="/Users/garyo/Documents/org-agenda"
if os.path.exists("/Users/garyo/Logseq Notes"):
    LOGSEQ_DIR="/Users/garyo/Logseq Notes"

def search_files(search_term, directory, file_pattern):
    """Search for term in files, return results with context"""
    results = defaultdict(list)

    # Use ripgrep for fast searching
    import subprocess

    try:
        # Search with context?
        cmd = [
            "rg",
            "--glob=*.md",
            "--glob=*.org",
            "-i",  # case insensitive
            "--with-filename",
            "--line-number",
            # "-C", "2",  # 2 lines of context
            "--max-count=10",  # max 10 matches per file
            search_term,
            str(directory)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.stdout:
            # Parse ripgrep output
            current_file = None
            for line in result.stdout.split('\n'):
                if not line:
                    continue

                # Line format: path:line_number:content or path:line_number--context
                parts = line.split(':', 3)
                if len(parts) >= 3:
                    file_path = parts[0]
                    try:
                        line_num = int(parts[1])
                        content = parts[3] if len(parts) > 3 else parts[2]

                        results[file_path].append({
                            'line': line_num,
                            'content': content.strip()
                        })
                    except ValueError:
                        pass

        return results
    except Exception as e:
        print(f"Error searching: {e}", file=sys.stderr)
        return results

def get_file_info(file_path):
    """Get metadata about a file"""
    try:
        p = Path(file_path)
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime)

        if "DSS" in file_path:
            prefix="DSS"
        elif "Personal" in file_path:
            prefix="PERSONAL"
        else:
            prefix=""
        # Determine file type
        if "journals" in file_path:
            file_type = "JOURNAL"
            # Extract date from filename
            name = p.stem
            date_str = name.replace("_", "-") if "_" in name else name
            return prefix, file_type, date_str, mtime
        elif "pages" in file_path:
            file_type = "PAGE"
            name = p.stem.replace("___", "/")
            return prefix, file_type, name, mtime
        else:
            file_type = "FILE"
            return prefix, file_type, p.stem, mtime
    except:
        return "", "FILE", p.stem, None

def format_output(search_term, org_results, logseq_results):
    """Format results nicely"""
    print(f"\n{'='*80}")
    print(f"References to '{search_term}' across your PKM")
    print(f"{'='*80}\n")

    # Combine and sort by date (most recent first)
    all_results = []

    for file_path, matches in org_results.items():
        prefix, file_type, name, mtime = get_file_info(file_path)
        all_results.append((mtime, "/".join({prefix, file_type}), name, file_path, matches, "ORG"))

    for file_path, matches in logseq_results.items():
        prefix, file_type, name, mtime = get_file_info(file_path)
        all_results.append((mtime, "/".join({prefix, file_type}), name, file_path, matches, "LOGSEQ"))

    # Sort by mtime (most recent first), then by type
    all_results.sort(key=lambda x: (x[0] or datetime.min, -ord(x[1][0])), reverse=True)

    if not all_results:
        print(f"No references found for '{search_term}'")
        return

    for mtime, file_type, name, file_path, matches, source in all_results:
        date_str = mtime.strftime("%Y-%m-%d") if mtime else "unknown"

        # Color coding (if terminal supports it)
        type_color = {
            "JOURNAL": "📅",
            "PAGE": "📄",
            "FILE": "📋"
        }.get(file_type, "📋")

        print(f"{type_color}  {source:6} | {file_type:8} | mtime: {date_str}")
        print(f"   → {name}")
        print(f"     at \"{file_path}\"")

        if matches:
            max_matches = 4
            max_len = 80
            print(f"   Matches: {len(matches)}")
            for match in matches[:max_matches]:  # Show first N matches
                content = match['content'][:max_len]
                if len(match['content']) > max_len:
                    content += "..."
                print(f"      L{match['line']}: {content}")
            if len(matches) > max_matches:
                print(f"      ... and {len(matches) - 3} more matches")
        print()

def main():
    if len(sys.argv) < 2:
        print("Usage: find_references <search_term> [--context=N]")
        print("Example: find_references revisionfx")
        sys.exit(1)

    search_term = sys.argv[1]

    print(f"Searching for '{search_term}' in ORG and LOGSEQ directories...")

    # Search both directories
    org_results = search_files(search_term, ORG_DIR, "*.org")
    logseq_results = search_files(search_term, LOGSEQ_DIR, "*.md")

    # Format and display results
    format_output(search_term, org_results, logseq_results)

    # Summary
    total_files = len(org_results) + len(logseq_results)
    total_matches = sum(len(m) for m in org_results.values()) + sum(len(m) for m in logseq_results.values())
    print(f"\nSummary: Found {total_matches} references in {total_files} files")

if __name__ == "__main__":
    main()
