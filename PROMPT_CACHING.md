# Prompt Caching Optimization

This document explains the prompt caching optimizations implemented to reduce API costs.

## Problem

Initial implementation was sending ~16k input tokens per request with **0% cache hit rate**, resulting in high API costs.

## Root Causes

1. **Missing beta header**: Not including `"prompt-caching-2024-07-31"` header
2. **Uncached tools**: Tool definitions (~2-3k tokens) sent every request
3. **Poor cache structure**: Daily-changing date embedded in middle of system prompt

## Solution

### 1. Enable Prompt Caching Beta (`pkm-bridge-server.py:352`)

```python
beta_features = ["prompt-caching-2024-07-31"]  # Was missing!
```

### 2. Cache Tool Definitions (`pkm-bridge-server.py:360-361`)

```python
tools = tool_registry.get_anthropic_tools()
if tools:
    tools[-1]["cache_control"] = {"type": "ephemeral"}
```

### 3. Restructure System Prompt

**Removed date from template** (`config/system_prompt.txt`)
- Removed `Today's date is {TODAY}.` line from template
- Date is now appended as separate uncached block

**Created block structure** (`config/settings.py:124-183`)
Created `get_system_prompt_blocks()` method that returns structured blocks:

**Block 1: Static Instructions (~8.2k chars) - CACHED**
- Base system prompt
- Tool descriptions
- Static paths (ORG_DIR, LOGSEQ_DIR)
- ✅ Cached: rarely changes

**Block 2: User Context (~1.7k chars) - CACHED**
- Personal information about user
- Work details, interests, preferences
- ✅ Cached: changes occasionally
- Separate block so editing context doesn't invalidate base instructions

**Block 3: Today's Date (~29 chars) - NOT CACHED**
- Current date appended dynamically
- ❌ Not cached: changes daily
- But doesn't break cache for blocks 1 & 2!

### 4. Add Cache Performance Logging (`pkm-bridge-server.py:436-442`)

```python
usage = response.usage
cache_write = getattr(usage, 'cache_creation_input_tokens', 0)
cache_read = getattr(usage, 'cache_read_input_tokens', 0)
logger.info(f"Token usage: {input_tokens} input, {cache_write} cache write, {cache_read} cache read")
```

## Expected Results

### First Request of the Day
- **Input tokens**: ~16k (full prompt)
- **Cache writes**: ~10k tokens (blocks 1 & 2)
- **Cost**: Standard input rate + cache write surcharge

### Subsequent Requests Same Day
- **Input tokens**: ~30 chars (just the date)
- **Cache reads**: ~10k tokens (blocks 1 & 2)
- **Cost**: 90% discount on cached tokens!

### Daily Cache Refresh
- When date changes, blocks 1 & 2 remain cached
- Only block 3 (29 chars) is new
- Cache hits continue until static content or user context changes

## Cost Savings

**Before**: 16k input tokens × $3/M = $0.048 per request

**After (cache hits)**:
- 30 input tokens × $3/M = $0.00009
- 10k cached tokens × $0.30/M = $0.003
- **Total**: $0.00309 per request

**Savings**: ~94% reduction in input costs after first request!

## Monitoring

Check server logs for cache performance:
```
Token usage: 45 input, 0 cache write, 10234 cache read
```

- High `cache_read`: ✅ Good! Caching working
- High `input`, low `cache_read`: ❌ Cache not hitting (check logs)
- High `cache_write` every request: ❌ Cache breaking (content changing)

## Cache Invalidation

Cache will refresh when:
1. **User context changes**: User edits context in Settings
2. **Tools change**: Code update adds/removes/modifies tools
3. **System prompt changes**: Update to base instructions
4. **Every 5 minutes**: Anthropic's ephemeral cache TTL

Cache will NOT refresh when:
1. **Date changes**: Block 3 isn't cached, so blocks 1 & 2 remain valid
2. **User asks different questions**: Cache is per-prompt structure, not content
3. **Conversation history grows**: History is separate from system prompt

## History Management (Cost Cap)

To prevent unbounded cost growth in long conversations, the server includes automatic history truncation:

**Implementation:** `pkm_bridge/history_manager.py`

**Strategy:**
1. **Token budget**: 100,000 tokens max (~$0.10 per request with Haiku)
2. **Keep recent context**: Last 10 conversation turns always preserved
3. **Truncate tool results**: Large tool results (>1000 tokens) in old messages get truncated
4. **Remove oldest**: If still over budget, remove oldest messages

**Example:**
```
Before: 120k tokens (50 turns with large search results)
After:  95k tokens (last 10 turns + truncated older tool results)
Savings: 25k tokens, costs stay under $0.10
```

**Logging:**
```
History before truncation: 120545/100000 (120%)
✂️  Truncated history: saved 25234 tokens (95311/100000 (95%))
```

**Why this matters:**
- Without truncation: Long conversation could hit 200k+ tokens = $0.30+ per request
- With truncation: Capped at ~$0.10 per request while maintaining recent context
- Tool results (search, file listings) can be huge but aren't needed after discussion

## References

- [Anthropic Prompt Caching Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- Implementation: `config/settings.py`, `pkm-bridge-server.py`, `pkm_bridge/history_manager.py`
