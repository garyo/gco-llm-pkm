"""Vocabulary-based query expansion using learned rules.

Zero-cost query expansion: loads vocabulary-type LearnedRules into an
in-memory cache and expands user queries with mapped terms before
embedding similarity search.
"""

import time
from typing import Dict, List, Optional, Tuple

from .database import get_db
from .db_repository import LearnedRuleRepository


class QueryEnhancer:
    """Expands queries using vocabulary rules from the learned rules database."""

    def __init__(self, logger, cache_ttl_seconds: int = 3600):
        self.logger = logger
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: List[Tuple[str, List[str]]] = []  # (user_term, note_terms) pairs
        self._cache_loaded_at: float = 0

    def _refresh_cache(self) -> None:
        """Reload vocabulary rules from database if cache is stale."""
        now = time.time()
        if self._cache and (now - self._cache_loaded_at) < self.cache_ttl_seconds:
            return

        try:
            db = get_db()
            try:
                rules = LearnedRuleRepository.get_vocabulary_rules(db)
                new_cache = []
                for rule in rules:
                    data = rule.rule_data or {}
                    user_term = data.get('user_term', '').lower()
                    note_terms = data.get('note_terms', [])
                    if user_term and note_terms:
                        if isinstance(note_terms, str):
                            note_terms = [note_terms]
                        new_cache.append((user_term, note_terms))

                self._cache = new_cache
                self._cache_loaded_at = now
                if new_cache:
                    self.logger.debug(f"QueryEnhancer: loaded {len(new_cache)} vocabulary mappings")
            finally:
                db.close()
        except Exception as e:
            self.logger.warning(f"QueryEnhancer: failed to refresh cache: {e}")

    def expand_query(self, query: str) -> str:
        """Expand a query using vocabulary rules.

        If the query contains any mapped user terms, appends the corresponding
        note terms as a parenthetical expansion. The expanded query is used only
        for embedding similarity search, not shown to the user.

        Args:
            query: The original user query.

        Returns:
            The expanded query string, or the original if no expansions apply.
        """
        self._refresh_cache()

        if not self._cache:
            return query

        query_lower = query.lower()
        expansions = []

        for user_term, note_terms in self._cache:
            if user_term in query_lower:
                expansions.extend(note_terms)

        if not expansions:
            return query

        # Deduplicate while preserving order
        seen = set()
        unique_expansions = []
        for term in expansions:
            if term.lower() not in seen:
                seen.add(term.lower())
                unique_expansions.append(term)

        expanded = f"{query} (related: {', '.join(unique_expansions)})"
        self.logger.info(f"QueryEnhancer: expanded query with {len(unique_expansions)} terms")
        return expanded
