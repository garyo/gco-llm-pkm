"""Semantic search tool for RAG.

Provides Claude with the ability to explicitly search notes using
semantic similarity when auto-retrieved context is insufficient.
"""

from typing import Dict, Any
import yaml

from pkm_bridge.tools.base import BaseTool
from pkm_bridge.context_retriever import ContextRetriever


class SemanticSearchTool(BaseTool):
    """Semantic search using vector embeddings."""

    def __init__(self, logger, context_retriever: ContextRetriever):
        """Initialize semantic search tool.

        Args:
            logger: Logger instance
            context_retriever: ContextRetriever for querying
        """
        super().__init__(logger)
        self.context_retriever = context_retriever

    @property
    def name(self) -> str:
        return "semantic_search"

    @property
    def description(self) -> str:
        return """Search notes using semantic similarity (understands meaning, not just keywords).

Use this tool when auto-retrieved context is insufficient and you need MORE or DIFFERENT information.

IMPORTANT: You already have auto-retrieved context in your system prompt. Only use this tool if:
- The auto-retrieved excerpts don't contain what the user is asking about
- You need broader or different results
- The user explicitly asks to search for something specific

Arguments:
- query: natural language search query (describe what you're looking for)
- limit: maximum results to return (default: 10)
- min_similarity: minimum similarity threshold 0-1 (default: 0.6, higher = more strict)
- newer: optional YYYY-MM-DD date filter (only return notes >= this date)

Returns YAML with:
- filename: path to source file
- file_type: 'org' or 'md'
- similarity: cosine similarity score 0-1 (1 = perfect match)
- date: note date (if available)
- heading_path: hierarchical context (heading structure)
- content: matched chunk text
- start_line: line number for jump-to-source

Results are sorted by similarity (most similar first).
"""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query"
                },
                "limit": {
                    "type": "number",
                    "default": 10,
                    "description": "Maximum number of results"
                },
                "min_similarity": {
                    "type": "number",
                    "default": 0.6,
                    "description": "Minimum similarity threshold (0-1)"
                },
                "newer": {
                    "type": "string",
                    "description": "Optional YYYY-MM-DD date filter (only notes >= this date)"
                }
            },
            "required": ["query"]
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
        limit = params.get("limit", 10)
        min_similarity = params.get("min_similarity", 0.6)
        newer_date = params.get("newer")

        self.logger.info(f"Semantic search: '{query[:50]}...' (limit={limit}, min_sim={min_similarity})")

        # Retrieve relevant chunks
        try:
            chunks = self.context_retriever.retrieve_context(
                query=query,
                limit=limit,
                min_similarity=min_similarity
            )
        except Exception as e:
            self.logger.error(f"Semantic search failed: {e}")
            return yaml.dump({
                'error': str(e),
                'query': query
            })

        # Filter by date if requested
        # Include chunks without dates (can't verify they're too old)
        if newer_date:
            chunks = [
                c for c in chunks
                if not c.get('date') or c['date'] >= newer_date
            ]

        # Format results
        results = []
        for chunk in chunks:
            result = {
                'filename': chunk['filename'],
                'file_type': 'org' if chunk['filename'].endswith('.org') else 'md',
                'similarity': chunk['similarity'],
                'date': chunk.get('date'),
                'heading_path': chunk.get('heading_path'),
                'content': chunk['content'],
                'start_line': chunk.get('start_line')
            }
            results.append(result)

        output = {
            'query': query,
            'total_results': len(results),
            'results': results
        }

        return yaml.dump(output, default_flow_style=False, allow_unicode=True, sort_keys=False)
