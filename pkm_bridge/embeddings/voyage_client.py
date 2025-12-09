"""Voyage AI client wrapper for embeddings.

Provides a simple interface for batch embedding with error handling,
retries, and cost tracking.
"""

import time
from typing import List, Optional
from dataclasses import dataclass
import logging


logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    """Result of an embedding operation."""
    embeddings: List[List[float]]
    total_tokens: int
    cost: float  # USD


class VoyageClient:
    """Wrapper for Voyage AI API with batching and error handling."""

    # Voyage pricing (as of Dec 2024)
    PRICE_PER_MILLION_TOKENS = 0.06  # $0.06 per 1M tokens for voyage-3

    def __init__(self, api_key: str, model: str = "voyage-3"):
        """Initialize Voyage client.

        Args:
            api_key: Voyage AI API key
            model: Model to use (default: voyage-3)
        """
        self.api_key = api_key
        self.model = model

        # Lazy import voyageai to avoid import errors if not installed
        try:
            import voyageai
            self.client = voyageai.Client(api_key=api_key)
        except ImportError:
            raise ImportError(
                "voyageai package not installed. "
                "Install with: pip install voyageai"
            )

    def embed(
        self,
        texts: List[str],
        input_type: str = "document",
        batch_size: int = 128,
        max_retries: int = 3
    ) -> EmbeddingResult:
        """Embed a list of texts.

        Args:
            texts: List of texts to embed
            input_type: "document" or "query" (optimizes embedding)
            batch_size: Max texts per API call (Voyage supports up to 128)
            max_retries: Number of retries on failure

        Returns:
            EmbeddingResult with embeddings, token count, and cost
        """
        all_embeddings: List[List[float]] = []
        total_tokens = 0

        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            # Retry logic
            for attempt in range(max_retries):
                try:
                    logger.debug(f"Embedding batch {i//batch_size + 1} ({len(batch)} texts)")

                    result = self.client.embed(
                        texts=batch,
                        model=self.model,
                        input_type=input_type
                    )

                    # Extract embeddings and token count
                    all_embeddings.extend(result.embeddings)
                    total_tokens += result.total_tokens

                    break  # Success

                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff
                        logger.warning(
                            f"Embedding failed (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {wait_time}s..."
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Embedding failed after {max_retries} attempts: {e}")
                        raise

        # Calculate cost
        cost = (total_tokens / 1_000_000) * self.PRICE_PER_MILLION_TOKENS

        logger.info(
            f"Embedded {len(texts)} texts. "
            f"Tokens: {total_tokens:,}, Cost: ${cost:.4f}"
        )

        return EmbeddingResult(
            embeddings=all_embeddings,
            total_tokens=total_tokens,
            cost=cost
        )

    def embed_single(
        self,
        text: str,
        input_type: str = "document"
    ) -> List[float]:
        """Embed a single text (convenience method).

        Args:
            text: Text to embed
            input_type: "document" or "query"

        Returns:
            Embedding vector
        """
        result = self.embed([text], input_type=input_type)
        return result.embeddings[0]
