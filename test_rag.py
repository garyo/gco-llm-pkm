#!/usr/bin/env python3
"""Quick test of RAG retrieval."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment
env_local = Path('.env.local')
if env_local.exists():
    load_dotenv(env_local)
else:
    load_dotenv()

from pkm_bridge.embeddings.voyage_client import VoyageClient
from pkm_bridge.context_retriever import ContextRetriever

# Initialize clients
voyage_api_key = os.getenv('VOYAGE_API_KEY')
if not voyage_api_key:
    print("Error: VOYAGE_API_KEY not set")
    exit(1)

voyage_client = VoyageClient(api_key=voyage_api_key)
retriever = ContextRetriever(voyage_client)

# Test query
query = "What presents should I get?"
print(f"Query: {query}\n")

# Retrieve context
chunks = retriever.retrieve_context(query, limit=5, min_similarity=0.5)

print(f"Found {len(chunks)} relevant chunks:\n")
for i, chunk in enumerate(chunks, 1):
    print(f"Chunk {i}:")
    print(f"  File: {chunk['filename']}")
    print(f"  Similarity: {chunk['similarity']}")
    print(f"  Content preview: {chunk['content'][:100]}...")
    print()

# Test formatting
if chunks:
    context_block = retriever.format_as_context_block(chunks)
    print("=" * 60)
    print("FORMATTED CONTEXT BLOCK:")
    print("=" * 60)
    print(context_block[:500])
    print("...")
