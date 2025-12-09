"""Note chunker for semantic embeddings.

Splits org-mode and markdown files into semantically coherent chunks
suitable for vector embeddings. Reuses parsing logic from find_context.py
but creates multiple overlapping chunks per file.
"""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class Chunk:
    """Represents a semantically coherent chunk of text."""
    content: str
    chunk_type: str  # 'heading', 'content', 'bullet'
    heading_path: Optional[str]  # Hierarchical context
    start_line: int
    token_count: int


class NoteChunker:
    """Chunk notes into semantically coherent pieces for embedding."""

    def __init__(self, max_tokens: int = 800, min_tokens: int = 20):
        """Initialize chunker.

        Args:
            max_tokens: Maximum tokens per chunk (split if exceeded)
            min_tokens: Minimum tokens per chunk (merge if possible)
        """
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation (chars / 4)."""
        return len(text) // 4

    def chunk_file(self, file_path: Path) -> List[Chunk]:
        """Chunk a file based on its type.

        Args:
            file_path: Path to the note file

        Returns:
            List of Chunk objects
        """
        if file_path.suffix == '.org':
            return self.chunk_org_file(file_path)
        elif file_path.suffix == '.md':
            return self.chunk_markdown_file(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")

    def chunk_org_file(self, file_path: Path) -> List[Chunk]:
        """Parse org file and create chunks at heading boundaries.

        Strategy:
        1. Parse file into heading sections
        2. For each section, create a chunk with:
           - Heading path (ancestors)
           - Content under that heading (excluding child headings)
        3. If section > max_tokens, split on paragraph boundaries

        Args:
            file_path: Path to .org file

        Returns:
            List of Chunk objects
        """
        chunks = []

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Parse heading structure
        heading_stack: List[Dict[str, Any]] = []
        current_section_start = 0
        current_content: List[str] = []

        for line_num, line in enumerate(lines, 1):
            # Match org heading (*, **, ***, etc.)
            heading_match = re.match(r'^(\*+)\s+(.+)$', line)

            if heading_match:
                # Save previous section if exists
                if current_content:
                    chunk = self._create_org_chunk(
                        heading_stack,
                        current_content,
                        current_section_start
                    )
                    if chunk:
                        chunks.append(chunk)

                # Update heading stack
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()

                # Pop headings at same or deeper level
                while heading_stack and heading_stack[-1]['level'] >= level:
                    heading_stack.pop()

                # Push new heading
                heading_stack.append({
                    'level': level,
                    'text': heading_text,
                    'line': line_num
                })

                # Reset content for new section
                current_content = [line]
                current_section_start = line_num
            else:
                # Add to current section content
                current_content.append(line)

        # Don't forget last section
        if current_content:
            chunk = self._create_org_chunk(
                heading_stack,
                current_content,
                current_section_start
            )
            if chunk:
                chunks.append(chunk)

        return chunks

    def _create_org_chunk(
        self,
        heading_stack: List[Dict[str, Any]],
        content_lines: List[str],
        start_line: int
    ) -> Optional[Chunk]:
        """Create a chunk from an org section.

        Args:
            heading_stack: Stack of parent headings
            content_lines: Lines of content for this section
            start_line: Starting line number

        Returns:
            Chunk object or None if content too small
        """
        # Filter out property drawers and empty lines
        filtered_content = []
        in_drawer = False

        for line in content_lines:
            if line.strip().startswith(':PROPERTIES:'):
                in_drawer = True
                continue
            if line.strip().startswith(':END:'):
                in_drawer = False
                continue
            if not in_drawer:
                filtered_content.append(line)

        # Join content
        content_text = ''.join(filtered_content).strip()

        # Skip if too small
        token_count = self.estimate_tokens(content_text)
        if token_count < self.min_tokens:
            return None

        # Build heading path
        heading_path = None
        if heading_stack:
            heading_path = '\n'.join([
                ('*' * h['level']) + ' ' + h['text']
                for h in heading_stack
            ])

        # If too large, split on paragraphs
        if token_count > self.max_tokens:
            # TODO: Implement paragraph splitting if needed
            # For now, just truncate
            pass

        return Chunk(
            content=content_text,
            chunk_type='heading',
            heading_path=heading_path,
            start_line=start_line,
            token_count=token_count
        )

    def chunk_markdown_file(self, file_path: Path) -> List[Chunk]:
        """Parse markdown file and create chunks at bullet boundaries.

        Strategy:
        1. Parse file into heading/bullet structure
        2. Create chunks at top-level bullets or headings
        3. Include nested bullets up to a reasonable depth

        Args:
            file_path: Path to .md file

        Returns:
            List of Chunk objects
        """
        chunks = []

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Parse heading and bullet structure
        current_chunk_lines: List[str] = []
        current_start_line = 1
        current_heading_stack: List[str] = []

        for line_num, line in enumerate(lines, 1):
            # Match markdown heading (# ## ### etc.)
            heading_match = re.match(r'^(#+)\s+(.+)$', line)

            # Match bullet (-, *, or numbered)
            bullet_match = re.match(r'^(\s*)([-*]|\d+\.)\s+', line)

            if heading_match:
                # Save previous chunk
                if current_chunk_lines:
                    chunk = self._create_markdown_chunk(
                        current_heading_stack,
                        current_chunk_lines,
                        current_start_line
                    )
                    if chunk:
                        chunks.append(chunk)

                # Update heading stack
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()

                # Reset for new section
                current_heading_stack = [heading_text]
                current_chunk_lines = [line]
                current_start_line = line_num

            elif bullet_match and not current_chunk_lines:
                # Start of new top-level bullet
                current_chunk_lines = [line]
                current_start_line = line_num

            else:
                # Continue current chunk
                current_chunk_lines.append(line)

                # Split if chunk gets too large
                chunk_text = ''.join(current_chunk_lines)
                if self.estimate_tokens(chunk_text) > self.max_tokens:
                    chunk = self._create_markdown_chunk(
                        current_heading_stack,
                        current_chunk_lines,
                        current_start_line
                    )
                    if chunk:
                        chunks.append(chunk)

                    # Reset for next chunk
                    current_chunk_lines = []
                    current_start_line = line_num + 1

        # Don't forget last chunk
        if current_chunk_lines:
            chunk = self._create_markdown_chunk(
                current_heading_stack,
                current_chunk_lines,
                current_start_line
            )
            if chunk:
                chunks.append(chunk)

        return chunks

    def _create_markdown_chunk(
        self,
        heading_stack: List[str],
        content_lines: List[str],
        start_line: int
    ) -> Optional[Chunk]:
        """Create a chunk from a markdown section.

        Args:
            heading_stack: Stack of parent headings
            content_lines: Lines of content
            start_line: Starting line number

        Returns:
            Chunk object or None if content too small
        """
        content_text = ''.join(content_lines).strip()

        # Skip if too small
        token_count = self.estimate_tokens(content_text)
        if token_count < self.min_tokens:
            return None

        # Build heading path
        heading_path = None
        if heading_stack:
            heading_path = ' > '.join(heading_stack)

        return Chunk(
            content=content_text,
            chunk_type='bullet',
            heading_path=heading_path,
            start_line=start_line,
            token_count=token_count
        )
