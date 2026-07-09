"""Context retriever for auto-injection RAG.

Automatically retrieves semantically relevant note chunks for user queries
and formats them for injection into Claude's system prompt.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func, null, or_

from pkm_bridge.database import Document, DocumentChunk, get_db
from pkm_bridge.embeddings.voyage_client import VoyageClient

logger = logging.getLogger(__name__)

# pgvector's cosine_distance = 1 - cosine_similarity, so true similarity lives
# in [0, 1] (not the [0, 2] distance range). A near-duplicate chunk typically
# scores ~0.7-0.9; unrelated content is often ~0.1-0.2, so 0.3-0.4 is a
# reasonable "somewhat relevant" default cutoff on this scale.
DEFAULT_MIN_SIMILARITY = 0.35

# Hybrid retrieval: dense (pgvector cosine) and keyword (Postgres full-text)
# candidate lists are merged with weighted Reciprocal Rank Fusion. Keyword
# matching rescues exact-token queries (names, codes, filenames) that cosine
# similarity ranks poorly; RRF sidesteps normalizing the two incomparable
# score scales by combining ranks instead.
VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3
RRF_K = 60  # standard damping constant; higher = flatter rank contribution


def rrf_fuse(
    vector_ids: List[Any],
    keyword_ids: List[Any],
    vector_weight: float = VECTOR_WEIGHT,
    keyword_weight: float = KEYWORD_WEIGHT,
    k: int = RRF_K,
) -> List[Any]:
    """Fuse two ranked id lists with weighted Reciprocal Rank Fusion.

    Each list contributes weight / (k + rank) per id (rank is 1-based).
    Returns ids ordered by fused score, best first; ties break on id for
    determinism.
    """
    scores: Dict[Any, float] = defaultdict(float)
    for rank, cid in enumerate(vector_ids, 1):
        scores[cid] += vector_weight / (k + rank)
    for rank, cid in enumerate(keyword_ids, 1):
        scores[cid] += keyword_weight / (k + rank)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))]


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
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        newer: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant chunks via hybrid semantic + keyword search.

        Dense candidates (pgvector cosine, thresholded by min_similarity) and
        keyword candidates (Postgres websearch full-text match, which requires
        all query terms and so bypasses the similarity threshold) are merged
        with weighted RRF. Exact-token queries — names, codes, filenames —
        surface through the keyword side even when embeddings rank them low.

        Args:
            query: User's query text
            limit: Maximum number of chunks to retrieve
            min_similarity: Minimum cosine similarity threshold (0-1; see
                DEFAULT_MIN_SIMILARITY for the cosine-scale rationale).
                Applies to dense candidates only.
            newer: Optional YYYY-MM-DD filter; only chunks whose document date
                is on/after this (or has no known date) are considered. Applied
                in SQL before the LIMIT so it can't hide matches ranked below
                the top N.

        Returns:
            List of dicts with keys: content, heading_path, filename, date,
            similarity (0.0 for keyword-only hits with no embedding),
            start_line, chunk_type
        """
        # Embed query. On failure fall back to keyword-only retrieval rather
        # than returning nothing.
        query_embedding = None
        try:
            query_embedding = self.voyage_client.embed_single(
                query,
                input_type="query"
            )
        except Exception as e:
            logger.error(f"Failed to embed query (keyword-only fallback): {e}")

        db = get_db()
        try:
            date_filters = []
            if newer:
                # Keep undated chunks too -- we can't verify they're too old.
                date_filters.append(
                    or_(Document.date_extracted.is_(None), Document.date_extracted >= newer)
                )

            # Candidate pool per modality; fusion narrows to `limit`.
            pool = max(limit * 3, 30)

            # Dense candidates (cosine_distance = 1 - cosine_similarity)
            vector_rows = []
            if query_embedding is not None:
                vector_rows = db.query(
                    DocumentChunk,
                    Document,
                    DocumentChunk.embedding.cosine_distance(query_embedding).label('distance')
                ).join(
                    Document,
                    DocumentChunk.document_id == Document.id
                ).filter(
                    DocumentChunk.embedding.isnot(None),
                    *date_filters
                ).order_by(
                    'distance'
                ).limit(pool).all()

            # Keyword candidates. websearch_to_tsquery is built for raw user
            # input (ANDs terms, tolerates quotes/operators); the expression
            # must match idx_chunks_content_fts exactly to use the GIN index.
            tsvector = func.to_tsvector('english', DocumentChunk.content)
            tsquery = func.websearch_to_tsquery('english', query)
            distance_col = (
                DocumentChunk.embedding.cosine_distance(query_embedding)
                if query_embedding is not None else null()
            ).label('distance')
            keyword_rows = db.query(
                DocumentChunk,
                Document,
                distance_col
            ).join(
                Document,
                DocumentChunk.document_id == Document.id
            ).filter(
                tsvector.op('@@')(tsquery),
                *date_filters
            ).order_by(
                func.ts_rank_cd(tsvector, tsquery).desc()
            ).limit(pool).all()

            # Collect candidates; rank order within each list feeds RRF.
            candidates: Dict[int, Dict[str, Any]] = {}
            vector_ids = []
            for chunk, doc, distance in vector_rows:
                similarity = 1 - distance
                if similarity < min_similarity:
                    continue
                vector_ids.append(chunk.id)
                candidates[chunk.id] = self._chunk_dict(chunk, doc, similarity)

            keyword_ids = []
            for chunk, doc, distance in keyword_rows:
                keyword_ids.append(chunk.id)
                if chunk.id not in candidates:
                    similarity = (1 - distance) if distance is not None else 0.0
                    candidates[chunk.id] = self._chunk_dict(chunk, doc, similarity)

            fused = rrf_fuse(vector_ids, keyword_ids)
            chunks = [candidates[cid] for cid in fused[:limit]]

            logger.info(
                f"Retrieved {len(chunks)} chunks "
                f"(dense: {len(vector_ids)} >= {min_similarity}, keyword: {len(keyword_ids)})"
            )
            return chunks

        except Exception as e:
            logger.error(f"Failed to retrieve context: {e}")
            return []
        finally:
            db.close()

    @staticmethod
    def _chunk_dict(chunk: DocumentChunk, doc: Document, similarity: float) -> Dict[str, Any]:
        """Result dict for one retrieved chunk."""
        return {
            'content': chunk.content,
            'heading_path': chunk.heading_path,
            'filename': doc.file_path,
            'date': doc.date_extracted,
            'similarity': round(similarity, 3),
            'start_line': chunk.start_line,
            'chunk_type': chunk.chunk_type
        }

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
        min_similarity: float = DEFAULT_MIN_SIMILARITY
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
        import os
        from datetime import datetime, timedelta
        from pathlib import Path

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
