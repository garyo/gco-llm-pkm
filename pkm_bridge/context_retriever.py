"""Context retriever for auto-injection RAG.

Automatically retrieves semantically relevant note chunks for user queries
and formats them for injection into Claude's system prompt.
"""

from typing import List, Dict, Any
import logging

from pkm_bridge.database import get_db, Document, DocumentChunk
from pkm_bridge.embeddings.voyage_client import VoyageClient


logger = logging.getLogger(__name__)


class ContextRetriever:
    """Automatically retrieve relevant note chunks for queries."""

    def __init__(self, voyage_client: VoyageClient):
        """Initialize context retriever.

        Args:
            voyage_client: Voyage AI client for query embedding
        """
        self.voyage_client = voyage_client

    def retrieve_context(
        self,
        query: str,
        limit: int = 12,
        min_similarity: float = 0.65
    ) -> List[Dict[str, Any]]:
        """Retrieve semantically relevant chunks for a query.

        Args:
            query: User's query text
            limit: Maximum number of chunks to retrieve
            min_similarity: Minimum cosine similarity threshold (0-1)

        Returns:
            List of dicts with keys: content, heading_path, filename, date, similarity
        """
        # Embed query
        try:
            query_embedding = self.voyage_client.embed_single(
                query,
                input_type="query"
            )
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            return []

        # Vector similarity search
        db = get_db()
        try:
            # Cosine similarity search with pgvector
            # Note: cosine_distance returns 0-2, where 0 is most similar
            results = db.query(
                DocumentChunk,
                Document,
                DocumentChunk.embedding.cosine_distance(query_embedding).label('distance')
            ).join(
                Document,
                DocumentChunk.document_id == Document.id
            ).filter(
                DocumentChunk.embedding.isnot(None)
            ).order_by(
                'distance'
            ).limit(limit).all()

            # Format and filter by similarity threshold
            chunks = []
            for chunk, doc, distance in results:
                # Convert distance to similarity (1 - distance/2 for cosine)
                # pgvector cosine_distance returns 0-2, where 0 is most similar
                similarity = 1 - (distance / 2)

                if similarity >= min_similarity:
                    chunks.append({
                        'content': chunk.content,
                        'heading_path': chunk.heading_path,
                        'filename': doc.file_path,
                        'date': doc.date_extracted,
                        'similarity': round(similarity, 3),
                        'start_line': chunk.start_line,
                        'chunk_type': chunk.chunk_type
                    })

            logger.info(f"Retrieved {len(chunks)} relevant chunks (similarity >= {min_similarity})")
            return chunks

        except Exception as e:
            logger.error(f"Failed to retrieve context: {e}")
            return []
        finally:
            db.close()

    def format_as_context_block(self, chunks: List[Dict[str, Any]]) -> str:
        """Format retrieved chunks as a context block for system prompt.

        Args:
            chunks: List of retrieved chunks

        Returns:
            Formatted markdown string for system prompt
        """
        if not chunks:
            return ""

        lines = [
            "# RETRIEVED NOTE CONTEXT",
            "",
            "The following note excerpts are semantically relevant to the user's query.",
            "These have been automatically retrieved from the user's PKM system based on semantic similarity.",
            ""
        ]

        for i, chunk in enumerate(chunks, 1):
            # Header with similarity score
            lines.append(f"## Excerpt {i} (similarity: {chunk['similarity']:.2f})")

            # Metadata
            if chunk.get('date'):
                lines.append(f"**Date:** {chunk['date']}")

            # Filename (make it more readable)
            from pathlib import Path
            filename = Path(chunk['filename']).name
            lines.append(f"**File:** {filename}")

            if chunk.get('heading_path'):
                lines.append(f"**Context:** {chunk['heading_path']}")

            lines.append("")

            # Content
            lines.append(chunk['content'])
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def retrieve_and_format(
        self,
        query: str,
        limit: int = 12,
        min_similarity: float = 0.65
    ) -> str:
        """Convenience method: retrieve and format in one call.

        Args:
            query: User's query text
            limit: Maximum number of chunks
            min_similarity: Minimum similarity threshold

        Returns:
            Formatted context block (empty string if no results)
        """
        chunks = self.retrieve_context(query, limit, min_similarity)
        return self.format_as_context_block(chunks)

    def retrieve_recent_journals(self, days: int = 3) -> List[Dict[str, Any]]:
        """Retrieve recent journal entries (last N days) from files directly.

        Uses the same file discovery as embedding service (ripgrep with .gitignore).

        Args:
            days: Number of recent days to retrieve

        Returns:
            List of documents with their content, sorted by date (newest first)
        """
        from datetime import datetime, timedelta
        from pathlib import Path
        import os
        import re

        # Reuse find_note_files from embedding service
        from pkm_bridge.embeddings.embedding_service import find_note_files

        # Get directory paths from environment
        org_dir = Path(os.getenv('ORG_DIR', ''))
        logseq_dir = Path(os.getenv('LOGSEQ_DIR', ''))

        directories = [d for d in [org_dir, logseq_dir] if d.exists()]
        if not directories:
            logger.warning("Neither ORG_DIR nor LOGSEQ_DIR found")
            return []

        # Find all note files (respects .gitignore via ripgrep)
        all_files = find_note_files(directories, logger=logger)

        # Calculate cutoff date
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_timestamp = cutoff_date.timestamp()

        journals = []

        # Filter for recent journal files only
        for file_path in all_files:
            # Check if this is a journal file (contains /journals/ in path)
            if '/journals/' not in str(file_path):
                continue

            # Check if file is recent enough (by mtime)
            if file_path.stat().st_mtime < cutoff_timestamp:
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract date from filename
                # Org: YYYY-MM-DD.org
                # Logseq: YYYY_MM_DD.md
                if file_path.suffix == '.org':
                    date_str = file_path.stem  # Already in YYYY-MM-DD format
                    file_type = 'org'
                else:  # .md
                    date_str = file_path.stem.replace('_', '-')  # Convert YYYY_MM_DD to YYYY-MM-DD
                    file_type = 'md'

                journals.append({
                    'date': date_str,
                    'file_path': str(file_path),
                    'content': content,
                    'file_type': file_type
                })
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")

        # Sort by date, newest first
        journals.sort(key=lambda x: x['date'], reverse=True)

        logger.info(f"Retrieved {len(journals)} journal entries from last {days} days (from files)")
        return journals

    def format_recent_journals(self, journals: List[Dict[str, Any]]) -> str:
        """Format recent journal entries as a context block.

        Args:
            journals: List of journal dictionaries

        Returns:
            Formatted markdown string
        """
        if not journals:
            return ""

        from pathlib import Path

        lines = [
            "# RECENT JOURNAL ENTRIES",
            "",
            "The following are your most recent daily journal entries.",
            "These provide temporal context for recent activities and thoughts.",
            ""
        ]

        for journal in journals:
            # Format date nicely
            date_str = journal['date']
            filename = Path(journal['file_path']).name

            lines.append(f"## {date_str}")
            lines.append(f"**File:** {filename}")
            lines.append("")
            lines.append(journal['content'])
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def retrieve_and_format_recent(self, days: int = 3) -> str:
        """Convenience method: retrieve and format recent journals.

        Args:
            days: Number of recent days

        Returns:
            Formatted context block (empty string if no results)
        """
        journals = self.retrieve_recent_journals(days)
        return self.format_recent_journals(journals)
