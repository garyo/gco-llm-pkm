# Vector Database & Semantic Search

## Overview

The PKM system uses **Retrieval-Augmented Generation (RAG)** to automatically provide Claude with relevant context from your notes. This enables Claude to answer questions using your personal knowledge base without requiring explicit searches for most queries.

### Key Features

- **Auto-injection**: Relevant note excerpts are automatically retrieved and injected into Claude's context for every query
- **Semantic search**: AI-powered search that understands meaning, not just keywords
- **Incremental updates**: Only changed files are re-embedded (efficient daily updates)
- **Background processing**: Embeddings run automatically at 3am daily via APScheduler

## Architecture

### Components

1. **PostgreSQL with pgvector**: Stores document chunks and their vector embeddings
2. **Voyage AI (voyage-3)**: Generates 1024-dimensional embeddings for text chunks
3. **Semantic Chunker**: Splits notes into coherent chunks (500-800 tokens)
4. **Context Retriever**: Automatically retrieves relevant chunks for each query
5. **APScheduler**: Runs incremental embedding updates daily

### How It Works

```
User Query → Embed Query → Vector Search → Top 12 Chunks → Inject into Prompt → Claude Response
                ↓
           (Voyage AI)
                ↓
          pgvector DB
```

1. **User sends a query** (e.g., "What camera gear do I have?")
2. **Query is embedded** using Voyage AI (creates a 1024-dim vector)
3. **Vector similarity search** finds the 12 most semantically similar note chunks
4. **Auto-injection**: Chunks are added to Claude's system prompt as background knowledge
5. **Claude responds** using the retrieved context + can call search tools if needed

## Setup

### Prerequisites

- PostgreSQL with pgvector extension installed
- Voyage AI API key (get from https://voyageai.com)
- Python dependencies: `pgvector`, `voyageai`, `apscheduler`

### Installation

1. **Install pgvector extension** in PostgreSQL:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

2. **Add Voyage API key** to `.env`:
   ```bash
   VOYAGE_API_KEY=pa-your-key-here
   ```

3. **Database tables** are created automatically on first run via `init_db()`:
   - `documents`: Tracks which files have been embedded
   - `document_chunks`: Stores text chunks with embeddings

4. **Run initial embedding**:
   ```bash
   # Development
   uv run --script scripts/embed_notes.py

   # Production (Docker)
   docker exec pkm-bridge-server uv run --script /app/scripts/embed_notes.py
   ```

### Docker Setup

The `docker-compose.yml` includes:
- `pgvector/pgvector:pg16` image for PostgreSQL
- `postgres-init.sql` to enable the vector extension on first init
- Environment variable `VOYAGE_API_KEY` must be set in `.env`

## Database Schema

### `documents` Table

Tracks which files have been embedded and their metadata:

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `file_path` | String(1024) | Absolute path to file (unique) |
| `file_type` | String(10) | File type: 'org' or 'md' |
| `file_hash` | String(64) | SHA256 hash for change detection |
| `date_extracted` | String(20) | Date from file (YYYY-MM-DD) |
| `total_chunks` | Integer | Number of chunks created |
| `last_embedded_at` | DateTime | When embedding was last updated |
| `created_at` | DateTime | First embedded |
| `updated_at` | DateTime | Last modified |

### `document_chunks` Table

Stores individual text chunks with their embeddings:

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `document_id` | Integer | FK to documents table |
| `chunk_index` | Integer | Position within document |
| `chunk_type` | String(20) | 'heading', 'content', 'bullet' |
| `heading_path` | Text | Hierarchical context (e.g., "* Top\n** Section") |
| `content` | Text | Actual text content |
| `start_line` | Integer | Line number in source file |
| `token_count` | Integer | Estimated token count |
| `embedding` | Vector(1024) | Voyage AI embedding (pgvector type) |
| `created_at` | DateTime | When chunk was created |

**Index**: `idx_embedding_cosine` on `embedding` column using IVFFlat algorithm for fast similarity search.

## Chunking Strategy

### Org-mode Files

- **Split at**: Heading boundaries (`*`, `**`, `***`, etc.)
- **Include**: Parent heading hierarchy for context
- **Filter**: Property drawers (`:PROPERTIES:`, `:END:`)
- **Target size**: 500-800 tokens per chunk
- **Minimum size**: 20 tokens (skip smaller chunks)

**Example**:
```org
* Music Practice
** Guitar
- Practiced scales
- Worked on chord transitions

** Piano
- Started learning new piece
```

Creates 2 chunks:
1. Heading path: `* Music Practice\n** Guitar` | Content: Guitar notes
2. Heading path: `* Music Practice\n** Piano` | Content: Piano notes

### Markdown Files (Logseq)

- **Split at**: Top-level bullets or heading boundaries
- **Include**: Current heading as context
- **Target size**: 500-800 tokens per chunk
- **Minimum size**: 20 tokens

**Example**:
```markdown
## Daily Log
- #work Fixed authentication bug
  - Added better error handling
  - Tests passing now
- #music Rehearsal tonight
```

Creates 2 chunks:
1. Heading: "Daily Log" | Content: work-related bullet + sub-bullets
2. Heading: "Daily Log" | Content: music-related bullet

## Embedding Pipeline

### Script: `scripts/embed_notes.py`

Command-line tool for managing embeddings:

```bash
# Embed all notes (initial run)
./scripts/embed_notes.py

# Incremental update (only changed files)
./scripts/embed_notes.py --incremental

# Embed specific file
./scripts/embed_notes.py --file /path/to/note.org

# Clear all embeddings (requires confirmation)
./scripts/embed_notes.py --clear

# Force clear without confirmation
./scripts/embed_notes.py --clear --force
```

### Process Flow

1. **Discover files**: Use ripgrep to find all `.org` and `.md` files (respects `.gitignore`)
2. **Check for changes**: Compute SHA256 hash, compare with database
3. **Chunk files**: Split into semantically coherent chunks (500-800 tokens)
4. **Batch embed**: Send chunks to Voyage AI in batches
5. **Store in DB**: Save chunks and embeddings to PostgreSQL
6. **Update metadata**: Record file hash and timestamp

### Change Detection

Files are re-embedded only if:
- File hash (SHA256) has changed
- File is new (not in database)
- User forces re-embedding with `--file` flag

### Scheduled Updates

**APScheduler** runs incremental embeddings daily at 3am:
- Configured in `pkm-bridge-server.py` (lines 127-139)
- Uses `cron` trigger: `hour=3`
- Misfire grace time: 1 hour (runs even if server was down)
- Runs as background task (doesn't block server)

Manual trigger via admin endpoint:
```bash
curl -X POST http://localhost:8000/admin/trigger-embedding
```

## Auto-Retrieval (RAG)

### How Auto-Injection Works

On every user query, the system:

1. **Embeds the query** using Voyage AI
2. **Searches vector DB** for top 12 most similar chunks (cosine similarity)
3. **Filters by threshold**: Only includes chunks with similarity ≥ 0.60
4. **Formats as context block**: Creates markdown section with excerpts
5. **Injects into system prompt**: Adds before the query (uses prompt caching)

### Context Block Format

```markdown
# RETRIEVED NOTE CONTEXT

The following note excerpts are semantically relevant to the user's query.

## Excerpt 1 (similarity: 0.82)
**Date:** 2025-12-08
**File:** 2025-12-08.org
**Context:** * Photography\n** Equipment

My current camera: Sony A7IV with 24-70mm f/2.8 lens
...

## Excerpt 2 (similarity: 0.76)
...
```

### Configuration

Located in `pkm-bridge-server.py` (lines 460-470):

```python
context_block_text = context_retriever.retrieve_and_format(
    query=user_message,
    limit=12,              # Max chunks to retrieve
    min_similarity=0.60    # Minimum similarity threshold (0-1)
)
```

**Tuning**:
- `limit`: Higher = more context (but more tokens/cost)
- `min_similarity`: Lower = more results (but potentially less relevant)
  - `0.70`: Strict (very relevant only)
  - `0.60`: Balanced (recommended)
  - `0.50`: Permissive (may include tangentially related)

### Prompt Caching

Retrieved context is cached separately using Claude's ephemeral caching:

```python
system_blocks = [
    {"type": "text", "text": base_prompt, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": context_block, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": current_date}  # Not cached (changes daily)
]
```

Benefits:
- ~90% cost reduction for repeated queries
- Faster responses (cached content processed instantly)

## Semantic Search Tool

In addition to auto-retrieval, Claude can explicitly call the `semantic_search` tool for:
- Vague/conceptual queries ("creative hobbies", "workflow improvements")
- When auto-retrieved context is insufficient
- User explicitly asks to search semantically

### Tool Usage

**Arguments**:
- `query` (required): Natural language search query
- `limit` (optional): Max results (default: 10)
- `min_similarity` (optional): Threshold 0-1 (default: 0.6)
- `newer` (optional): YYYY-MM-DD date filter (only notes ≥ this date)

**Example**:
```python
semantic_search(
    query="creative projects and hobbies",
    limit=15,
    min_similarity=0.65,
    newer="2025-01-01"  # Only 2025 notes
)
```

**Returns**: YAML with ranked results:
```yaml
query: creative projects and hobbies
total_results: 8
results:
  - filename: /data/org-agenda/journals/2025-12-01.org
    file_type: org
    similarity: 0.78
    date: 2025-12-01
    heading_path: "* Projects\n** Creative"
    content: "Started learning watercolor painting..."
    start_line: 42
```

### System Prompt Guidance

Location: `config/system_prompt.txt` (lines 24-57)

Key points:
- Auto-retrieved context is always present (if RAG enabled)
- Use `semantic_search` for additional semantic results
- Use `find_context` for full context around specific terms
- Use `search_notes` for exhaustive keyword matching

## Performance & Cost

### Initial Embedding

- **Time**: 10-30 minutes for 10K notes
- **Cost**: ~$0.30 one-time (Voyage AI: $0.06 per 1M tokens)
- **Storage**: ~40MB for 10K notes (~4KB per chunk)

### Incremental Updates

- **Daily changes**: ~10 files
- **Time**: <1 minute
- **Cost**: ~$0.003 per day

### Query Cost (Per Query)

| Component | Tokens | Cost (at Claude Haiku rates) |
|-----------|--------|------------------------------|
| Query embedding | ~50 | ~$0.0001 (Voyage AI) |
| Retrieved chunks (12 × 200 tokens) | ~2,400 | ~$0.0003 (input, cached) |
| System prompt | ~2,000 | ~$0.0002 (cached) |
| User message | ~100 | ~$0.00001 |
| **Total overhead per query** | | **~$0.0006** |

With prompt caching: ~90% cheaper for repeated queries (cached content is nearly free).

**Monthly estimate** (100 queries/day):
- Auto-retrieval: ~$1.80/month
- Incremental embeddings: ~$0.09/month
- **Total: ~$1.90/month**

### Vector Search Performance

- **Query time**: 50-100ms (pgvector cosine similarity)
- **Total overhead**: ~200-400ms per query (embedding + search)
- **Index**: IVFFlat with 100 lists (good for 1K-100K vectors)

## Troubleshooting

### "type 'vector' does not exist"

**Cause**: pgvector extension not installed in PostgreSQL.

**Fix**:
```bash
# Connect to database and create extension
psql -U pkm -d pkm_db -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Or in Docker
docker exec pkm-db psql -U pkm -d pkm_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### "VOYAGE_API_KEY not set - RAG disabled"

**Cause**: Missing API key in environment.

**Fix**: Add to `.env`:
```bash
VOYAGE_API_KEY=pa-your-key-here
```

Then restart server.

### No results from semantic search

**Check**:
1. Are embeddings populated? `SELECT COUNT(*) FROM document_chunks;`
2. Is similarity threshold too high? Try lowering to 0.5
3. Is query too specific? Semantic search works best for conceptual queries

**Debug**:
```bash
# Check embedding status
docker exec pkm-bridge-server uv run --script /app/scripts/embed_notes.py --incremental
```

### Stale embeddings

**Cause**: Files changed but embeddings not updated.

**Fix**: Run incremental embedding manually:
```bash
# Development
./scripts/embed_notes.py --incremental

# Production
docker exec pkm-bridge-server uv run --script /app/scripts/embed_notes.py --incremental
```

Or wait until 3am when the scheduled job runs.

### High cost

**Causes**:
- Too many retrieved chunks (`limit` too high)
- Prompt caching not working (check cache hit rate in logs)
- Re-embedding unchanged files (should use `--incremental`)

**Solutions**:
- Reduce `limit` from 12 to 8 or 10
- Ensure prompt caching is enabled (`cache_control` in system blocks)
- Always use `--incremental` flag for daily updates

## Advanced Configuration

### Tuning Similarity Thresholds

Edit `pkm-bridge-server.py` and `pkm_bridge/tools/semantic_search.py`:

```python
# Auto-retrieval (pkm-bridge-server.py:464)
min_similarity=0.60  # Increase for precision, decrease for recall

# semantic_search tool (semantic_search.py:99)
min_similarity = params.get("min_similarity", 0.60)
```

### Adjusting Chunk Size

Edit `pkm_bridge/embeddings/chunker.py`:

```python
def __init__(self, max_tokens: int = 800, min_tokens: int = 20):
```

- `max_tokens`: Larger chunks = more context but fewer unique chunks
- `min_tokens`: Lower = include very small notes, higher = skip short content

### Changing Scheduled Time

Edit `pkm-bridge-server.py`:

```python
embedding_scheduler.add_job(
    func=run_incremental_embedding,
    trigger="cron",
    hour=3,           # Change to desired hour (0-23)
    minute=0,         # Optional: specify minute
    ...
)
```

## Files Reference

### Core Implementation

- `pkm_bridge/database.py`: SQLAlchemy models (Document, DocumentChunk)
- `pkm_bridge/embeddings/chunker.py`: Note chunking logic
- `pkm_bridge/embeddings/voyage_client.py`: Voyage AI API wrapper
- `pkm_bridge/embeddings/embedding_service.py`: Embedding pipeline
- `pkm_bridge/context_retriever.py`: Auto-retrieval logic
- `pkm_bridge/tools/semantic_search.py`: Semantic search tool

### Scripts

- `scripts/embed_notes.py`: CLI for managing embeddings
- `postgres-init.sql`: PostgreSQL initialization (enables pgvector)

### Configuration

- `config/system_prompt.txt`: Search strategy guidance for Claude
- `pyproject.toml`: Python dependencies (pgvector, voyageai, apscheduler)
- `docker-compose.yml`: PostgreSQL with pgvector configuration

### Server

- `pkm-bridge-server.py`:
  - Auto-retrieval injection (lines 460-485)
  - APScheduler setup (lines 127-139)
  - Semantic search tool registration (lines 174-177)

## Best Practices

1. **Initial setup**: Run full embedding once, then use `--incremental` daily
2. **Monitor costs**: Check Voyage AI dashboard monthly
3. **Tune thresholds**: Start with 0.60, adjust based on results
4. **Use scheduled jobs**: Let APScheduler handle daily updates
5. **Clear old embeddings**: If changing chunking strategy, use `--clear` and re-embed
6. **Test queries**: Use both auto-retrieval and explicit semantic_search to compare results
7. **Cache hits**: Monitor logs for cache hit rates (should be >80% for repeat queries)

## Future Enhancements

Potential improvements:
- Hybrid ranking (combine semantic + keyword + recency scores)
- LLM re-ranking of top results for better precision
- Temporal decay (weight recent notes higher)
- Note clustering visualization
- User feedback loop (mark helpful/unhelpful chunks)
- Multi-query retrieval (embed variations of user query)
- Cross-encoder re-ranking for top-k results
