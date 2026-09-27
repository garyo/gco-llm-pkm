"""Semantic search tool for RAG.

Lets the model search notes by meaning (plus exact keywords) via the
embedding index.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import yaml

from pkm_bridge.context_retriever import DEFAULT_MIN_SIMILARITY, ContextRetriever
from pkm_bridge.note_paths import display_path
from pkm_bridge.tools.base import BaseTool

# Output budget: excerpts, not whole chunks, so a search costs a few
# thousand tokens; read_note fetches the full note when one matters.
MAX_EXCERPT_CHARS = 600
MAX_OUTPUT_CHARS = 10_000
MAX_CHUNKS_PER_DOC = 2


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    """Trim text to about `limit` chars at a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip() + " …"


class SemanticSearchTool(BaseTool):
    """Semantic search using vector embeddings."""

    def __init__(
        self,
        logger,
        context_retriever: ContextRetriever,
        org_dir: Path | None = None,
        logseq_dir: Path | None = None,
    ):
        """Initialize semantic search tool.

        Args:
            logger: Logger instance
            context_retriever: ContextRetriever for querying
            org_dir: Primary notes directory, for org: result paths
            logseq_dir: Logseq directory, for logseq: result paths
        """
        super().__init__(logger)
        self.context_retriever = context_retriever
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir

    @property
    def name(self) -> str:
        return "semantic_search"

    @property
    def description(self) -> str:
        return f"""Search notes by meaning (vector similarity) blended with exact keyword
matching, which rescues names, codes and filenames. Notes are not loaded into your
context automatically, so use this first for questions about the user's life, people,
projects or past notes, especially vague or conceptual ones. Then read_note a result's
filename for the full note, or use search_notes for every literal match of a term.

Returns YAML, best first: filename (org:/logseq: path for read_note), date, similarity
(0-1; keyword-only hits may score below min_similarity), heading_path, start_line and
content, an excerpt of up to ~{MAX_EXCERPT_CHARS} chars. At most {MAX_CHUNKS_PER_DOC} excerpts
per note and ~{MAX_OUTPUT_CHARS // 1000}KB in all.
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "limit": {
                    "type": "number",
                    "default": 10,
                    "description": "Maximum number of results",
                },
                "min_similarity": {
                    "type": "number",
                    "default": DEFAULT_MIN_SIMILARITY,
                    "description": "Minimum cosine similarity threshold (0-1)",
                },
                "newer": {
                    "type": "string",
                    "description": "Optional YYYY-MM-DD date filter (only notes >= this date)",
                },
            },
            "required": ["query"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute semantic search.

        Args:
            params: Tool parameters (query, limit, min_similarity, newer)
            context: Additional context (unused)

        Returns:
            YAML-formatted search results
        """
        query = params["query"]
        limit = int(params.get("limit", 10))
        min_similarity = params.get("min_similarity", DEFAULT_MIN_SIMILARITY)
        newer_date = params.get("newer")

        self.logger.info(
            f"Semantic search: '{query[:50]}...' (limit={limit}, min_sim={min_similarity})"
        )

        # Retrieve relevant chunks. The date filter is applied inside the SQL
        # query (before the LIMIT) so recent matches ranked below the top-N
        # by similarity are still reachable. Over-fetch so the per-note cap
        # still leaves `limit` results.
        try:
            chunks = self.context_retriever.retrieve_context(
                query=query, limit=limit * 2, min_similarity=min_similarity, newer=newer_date
            )
        except Exception as e:
            self.logger.error(f"Semantic search failed: {e}")
            return yaml.dump({"error": str(e), "query": query})

        results = self._format_results(chunks, limit)
        output: Dict[str, Any] = {"query": query, "total_results": len(results)}
        output["results"] = results
        text = self._dump(output)

        # Enforce the overall budget by dropping the lowest-ranked results
        omitted = 0
        while len(text) > MAX_OUTPUT_CHARS and len(results) > 1:
            results.pop()
            omitted += 1
            output["total_results"] = len(results)
            output["omitted_for_length"] = omitted
            text = self._dump(output)
        return text

    def _format_results(self, chunks: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        """Compact result dicts, best first, at most MAX_CHUNKS_PER_DOC per note."""
        per_doc: Counter[str] = Counter()
        results = []
        for chunk in chunks:
            if len(results) >= limit:
                break
            filename = chunk["filename"]
            if per_doc[filename] >= MAX_CHUNKS_PER_DOC:
                continue
            per_doc[filename] += 1
            result = {
                "filename": display_path(filename, self.org_dir, self.logseq_dir),
                "date": chunk.get("date"),
                "similarity": chunk["similarity"],
                "heading_path": chunk.get("heading_path"),
                "start_line": chunk.get("start_line"),
                "content": _excerpt(chunk["content"]),
            }
            results.append({k: v for k, v in result.items() if v is not None})
        return results

    @staticmethod
    def _dump(output: Dict[str, Any]) -> str:
        return yaml.dump(output, default_flow_style=False, allow_unicode=True, sort_keys=False)
