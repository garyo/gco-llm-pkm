#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pyyaml>=6.0.2",
# ]
# ///
"""Find notes matching a regex with full context extraction."""

import re
import sys
import yaml
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Allow running as script or as module
if __name__ == "__main__":
    # When running as script, add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pkm_bridge.tools.base import BaseTool
    from pkm_bridge.logging_config import setup_logging
    from pkm_bridge.org_links import rewrite_org_links_to_markdown
else:
    from .base import BaseTool
    from ..org_links import rewrite_org_links_to_markdown


class FindContextTool(BaseTool):
    """Find all notes containing a regex pattern, with full context.

    For each match, returns:
    - The entire note section containing the match
    - Parent headlines/structure up to the root
    - File metadata (name, type, line number)
    """

    def __init__(self, logger, org_dir: Path, logseq_dir: Path|None = None):
        """Initialize find_context tool.

        Args:
            logger: Logger instance
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.org_dir = Path(org_dir)
        self.logseq_dir = Path(logseq_dir) if logseq_dir else None

    @property
    def name(self) -> str:
        return "find_context"

    @property
    def description(self) -> str:
        dirs_info = f"PRIMARY (org-mode): {self.org_dir}"
        if self.logseq_dir:
            dirs_info += f"\nSECONDARY (Logseq): {self.logseq_dir}"

        return f"""Find all notes matching a regex pattern with full hierarchical context.

Uses ripgrep for fast searching. Automatically respects .gitignore files to exclude:
- Backup directories (bak/, .recycle/)
- Internal config directories (.logseq/)
- Sync/temp files (.syncthing*, *.tmp, etc.)

Returns YAML with the following fields for each match:
- filename: full path to the file
- file_type: 'org' or 'md'
- date: note date in YYYY-MM-DD format (extracted from #+title for org files, filename for journals, or file mtime)
- match_line: line number of the match (1-indexed)
- matched_text: the actual matched line
- context: hierarchical context (optional - only included if there's additional context beyond the matched line)

Context structure:
- For org files: includes parent headings up to root, current heading, and direct content under that heading (stops at child headings)
- For markdown files: includes parent bullets (less indented), matched line, and child content (more indented)

Returns only the first match per file to avoid duplication.
Results are sorted by date (most recent first), then limited to max_results.

Arguments:
- pattern: regex pattern to search for (case-insensitive, required)
- paths: optional list of files/directories to search (if not provided, searches default directories)
- newer: optional date filter in YYYY-MM-DD format (only returns notes with dates >= this date)
- max_results: maximum number of results to return (default: 50)

Default directories searched (if paths not provided):
{dirs_info}
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for"
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of files or directories to search. Directories are searched recursively."
                },
                "newer": {
                    "type": "string",
                    "description": "Optional date filter in YYYY-MM-DD format. Only returns notes with dates >= this date."
                },
                "max_results": {
                    "type": "number",
                    "default": 50,
                    "description": "Maximum number of results to return"
                }
            },
            "required": ["pattern"]
        }

    def _parse_org_structure(self, lines: List[str], match_line: int) -> Dict[str, Any]:
        """Parse org-mode file structure and extract context for a match.

        Returns:
            Dict with parent_headings, current_heading, and section_content
        """
        # Find the heading that contains this line
        parent_headings = []
        current_section_start = 0
        current_section_level = 0
        current_heading = ""

        # Stack to track heading hierarchy: [(level, line_num, heading_text)]
        heading_stack = []

        # First pass: find which heading contains the match line
        for i, line in enumerate(lines):
            if i > match_line:
                break

            # Check for org heading
            if line.startswith('*'):
                # Count the number of stars
                match = re.match(r'^(\*+)\s+(.+)$', line)
                if match:
                    level = len(match.group(1))
                    heading_text = match.group(2).strip()

                    # Pop headings of equal or greater level from stack
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()

                    # Add this heading to stack
                    heading_stack.append((level, i, heading_text))

                    # This heading contains or is at the match line
                    current_section_start = i
                    current_section_level = level
                    parent_headings = [(lvl, txt) for lvl, _, txt in heading_stack[:-1]]
                    current_heading = heading_text

        # Find the end of the current section
        # Stop at the next heading of any level (to exclude child sections)
        section_end = len(lines)
        for i in range(current_section_start + 1, len(lines)):
            line = lines[i]
            if line.startswith('*'):
                match = re.match(r'^(\*+)\s', line)
                if match:
                    # Stop at ANY heading (child or sibling)
                    section_end = i
                    break

        # Extract section content (skip the heading line itself and property drawers)
        section_content = []
        in_properties = False

        for i in range(current_section_start + 1, section_end):
            line = lines[i]

            # Track property drawer state
            if line.strip() == ':PROPERTIES:':
                in_properties = True
                continue
            elif line.strip() == ':END:':
                in_properties = False
                continue

            # Skip lines inside property drawers
            if in_properties:
                continue

            section_content.append(line)

        return {
            'parent_headings': parent_headings,
            'current_heading': current_heading,
            'current_heading_level': current_section_level,
            'section_content': '\n'.join(section_content).strip(),
            'section_start': current_section_start,
        }

    def _parse_markdown_structure(self, lines: List[str], match_line: int) -> Dict[str, Any]:
        """Parse markdown/Logseq file structure and extract context for a match.

        Returns:
            Dict with parent_bullets and section_content
        """
        # Calculate indentation of the matched line
        matched_line = lines[match_line]
        match_indent = len(matched_line) - len(matched_line.lstrip())

        # Find parent bullets (lines with less indentation before this line)
        parent_bullets = []
        for i in range(match_line - 1, -1, -1):
            line = lines[i]
            if not line.strip():
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent < match_indent and line.strip().startswith('-'):
                parent_bullets.insert(0, (line_indent, line.strip()))
                match_indent = line_indent

        # Find all child content (lines with greater indentation after this line)
        section_content = [matched_line.strip()]
        for i in range(match_line + 1, len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            line_indent = len(line) - len(line.lstrip())
            # If we hit a line with equal or less indentation, stop
            if line_indent <= (len(matched_line) - len(matched_line.lstrip())):
                break
            section_content.append(line.strip())

        return {
            'parent_bullets': parent_bullets,
            'section_content': '\n'.join(section_content).strip()
        }

    def _extract_date(self, file_path: Path, lines: List[str], file_type: str) -> Optional[str]:
        """Extract date from note file.

        Priority order:
        1. For org files: #+title: line (if contains date in YYYY-MM-DD format)
        2. Filename (journal files like YYYY-MM-DD.org or YYYY_MM_DD.md)
        3. File modification time (fallback)

        Returns:
            Date string in YYYY-MM-DD format, or None if no date found
        """
        # 1. Try #+title for org files
        if file_type == 'org':
            for line in lines[:20]:  # Check first 20 lines
                if line.lower().startswith('#+title:'):
                    # Look for YYYY-MM-DD pattern in the title
                    title_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if title_match:
                        return title_match.group(1)

        # 2. Try filename pattern (YYYY-MM-DD or YYYY_MM_DD)
        filename = file_path.stem  # Get filename without extension
        date_match = re.match(r'^(\d{4})[-_](\d{2})[-_](\d{2})', filename)
        if date_match:
            year, month, day = date_match.groups()
            return f"{year}-{month}-{day}"

        # 3. Fall back to file modification time
        try:
            mtime = file_path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime)
            return dt.strftime('%Y-%m-%d')
        except Exception as e:
            self.logger.warning(f"Could not get mtime for {file_path}: {e}")
            return None



    def _run_ripgrep(self, pattern: str, paths: List[str]) -> List[Dict[str, Any]]:
        """Use ripgrep to find all matches efficiently.

        Args:
            pattern: Regex pattern to search for
            paths: List of directory paths to search

        Returns:
            List of match info dicts with file, line number, and matched text
        """
        if not paths:
            return []

        # Build ripgrep command
        # Note: ripgrep respects .gitignore by default, which already excludes
        # internal directories like bak/, .recycle/, .logseq/, etc.
        cmd = [
            'rg',
            '--json',  # JSON output for easy parsing
            '-i',  # Case insensitive
            '--type-add', 'notes:*.{org,md}',  # Define custom type
            '--type', 'notes',  # Only search note files
            '--max-count', '1',  # Only first match per file
            pattern
        ]
        cmd.extend(paths)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            # Parse JSON output from ripgrep
            matches = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    data = yaml.safe_load(line)  # JSON is valid YAML
                    if data.get('type') == 'match':
                        match_data = data['data']
                        matches.append({
                            'file': Path(match_data['path']['text']),
                            'line_num': match_data['line_number'],
                            'matched_text': match_data['lines']['text'].strip()
                        })
                except Exception as e:
                    self.logger.debug(f"Could not parse ripgrep line: {e}")
                    continue

            return matches

        except subprocess.TimeoutExpired:
            self.logger.error(f"Ripgrep search timed out after 30s")
            return []
        except FileNotFoundError:
            self.logger.error("ripgrep (rg) not found. Please install ripgrep.")
            return []
        except Exception as e:
            self.logger.error(f"Error running ripgrep: {e}")
            return []

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute context search.

        Args:
            params: Dict with pattern, optional paths, and optional max_results

        Returns:
            YAML-formatted results or error message
        """
        pattern = params["pattern"]
        paths = params.get("paths", [])
        newer = params.get("newer")
        max_results = params.get("max_results", 50)

        self.logger.info(f"Finding context for pattern: {pattern}")
        if newer:
            self.logger.info(f"Filtering for dates >= {newer}")

        # Determine which directories to search
        search_dirs = []
        if paths:
            # Use provided paths
            for path_str in paths:
                path = Path(path_str).expanduser()
                if not path.exists():
                    self.logger.warning(f"Path does not exist: {path}")
                    continue
                search_dirs.append(str(path))
        else:
            # Use default directories
            if self.org_dir.exists():
                search_dirs.append(str(self.org_dir))
            if self.logseq_dir and self.logseq_dir.exists():
                search_dirs.append(str(self.logseq_dir))

        if not search_dirs:
            return "No valid directories to search"

        # Use ripgrep to find all matches (fast!)
        self.logger.info(f"Searching {len(search_dirs)} directories with ripgrep")
        matches = self._run_ripgrep(pattern, search_dirs)
        self.logger.info(f"Found {len(matches)} matches")

        if not matches:
            return f"No matches found for pattern: {pattern}"

        # Now extract context for each match
        all_results = []
        for match in matches:
            file_path = match['file']
            line_num = match['line_num'] - 1  # Convert to 0-indexed

            # Determine file type
            if file_path.suffix == '.org':
                file_type = 'org'
            elif file_path.suffix == '.md':
                file_type = 'md'
            else:
                continue

            try:
                # Read file and extract context
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = [line.rstrip('\n\r') for line in f.readlines()]

                # Extract date from file
                file_date = self._extract_date(file_path, lines, file_type)

                # Extract context based on file type
                if file_type == 'org':
                    context = self._parse_org_structure(lines, line_num)

                    # Build full context with parent headings
                    full_context = []
                    for level, heading in context['parent_headings']:
                        full_context.append('*' * level + ' ' + heading)
                    if context.get('current_heading'):
                        level = context['current_heading_level']
                        full_context.append('*' * level + ' ' + context['current_heading'])
                    full_context.append('')
                    full_context.append(context['section_content'])

                    context_str = '\n'.join(full_context).strip()
                    matched_text = match['matched_text']

                    # Rewrite org links to markdown for image/id rendering
                    section_start = context.get('section_start', 0)
                    context_str = rewrite_org_links_to_markdown(
                        context_str, lines, section_start, self.org_dir
                    )
                    matched_text = rewrite_org_links_to_markdown(
                        matched_text, lines, section_start, self.org_dir
                    )

                    result = {
                        'filename': str(file_path),
                        'file_type': file_type,
                        'match_line': match['line_num'],
                        'matched_text': matched_text,
                    }

                    # Add date if available
                    if file_date:
                        result['date'] = file_date

                    # Only include context if it adds information beyond the matched line
                    if context_str != matched_text:
                        result['context'] = context_str

                    all_results.append(result)

                else:  # markdown
                    context = self._parse_markdown_structure(lines, line_num)

                    # Build full context with parent bullets
                    full_context = []
                    for indent, bullet in context['parent_bullets']:
                        full_context.append(' ' * indent + bullet)
                    full_context.append(context['section_content'])

                    context_str = '\n'.join(full_context).strip()
                    matched_text = match['matched_text']

                    result = {
                        'filename': str(file_path),
                        'file_type': file_type,
                        'match_line': match['line_num'],
                        'matched_text': matched_text,
                    }

                    # Add date if available
                    if file_date:
                        result['date'] = file_date

                    # Only include context if it adds information beyond the matched line
                    if context_str != matched_text:
                        result['context'] = context_str

                    all_results.append(result)

            except Exception as e:
                self.logger.error(f"Error processing {file_path}: {e}")
                continue

        # Sort results by date (most recent first)
        # Results without dates will be at the end
        all_results.sort(key=lambda x: x.get('date', '0000-00-00'), reverse=True)

        # Filter by date if newer is specified
        if newer:
            all_results = [r for r in all_results if r.get('date', '0000-00-00') >= newer]

        # Limit results AFTER sorting and filtering
        all_results = all_results[:max_results]

        # Format as YAML
        if not all_results:
            return f"No matches found for pattern: {pattern}"

        output = {
            'pattern': pattern,
            'total_matches': len(all_results),
            'results': all_results
        }

        # Custom representer for multiline strings - use literal block style (|)
        def str_representer(dumper, data):
            if '\n' in data:
                return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
            return dumper.represent_scalar('tag:yaml.org,2002:str', data)

        yaml.add_representer(str, str_representer)

        return yaml.dump(output,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False)


def main():
    """Command-line interface for find_context tool."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description='Find notes matching a regex pattern with full context',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "Emacs.*PKM"
  %(prog)s "LLM|AI" --max-results 10
  %(prog)s "#music" --org-only
  %(prog)s "TODO" --paths ~/Documents/org-agenda/journals/2025-11-*.org
  %(prog)s "music" --newer 2025-11-01
        """
    )
    parser.add_argument('pattern', help='Regex pattern to search for')
    parser.add_argument('--paths', nargs='+',
                        help='Files or directories to search (default: use default org/logseq dirs)')
    parser.add_argument('--newer', type=str,
                        help='Only return notes with dates >= this date (YYYY-MM-DD format)')
    parser.add_argument('--max-results', type=int, default=50,
                        help='Maximum number of results (default: 50)')
    parser.add_argument('--org-only', action='store_true',
                        help='Search only org files (skip Logseq)')
    parser.add_argument('--logseq-only', action='store_true',
                        help='Search only Logseq files (skip org)')
    parser.add_argument('--org-dir', type=str,
                        default=os.path.expanduser('~/Documents/org-agenda'),
                        help='Org-mode directory (default: ~/Documents/org-agenda)')
    parser.add_argument('--logseq-dir', type=str,
                        default=os.path.expanduser('~/Logseq Notes'),
                        help='Logseq directory (default: ~/Logseq Notes)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    # Setup logging
    log_level = 'DEBUG' if args.debug else 'INFO'
    logger = setup_logging(log_level)

    # Determine directories
    org_dir = None if args.logseq_only else Path(args.org_dir)
    logseq_dir = None if args.org_only else Path(args.logseq_dir)

    if not org_dir and not logseq_dir:
        print("Error: Cannot use both --org-only and --logseq-only", file=sys.stderr)
        sys.exit(1)

    # Create tool and execute
    tool = FindContextTool(logger, org_dir or Path('/tmp'), logseq_dir)
    params = {
        'pattern': args.pattern,
        'max_results': args.max_results
    }
    if args.paths:
        params['paths'] = args.paths
    if args.newer:
        params['newer'] = args.newer

    result = tool.execute(params)

    print(result)


if __name__ == "__main__":
    main()
