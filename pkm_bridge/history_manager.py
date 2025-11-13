"""Conversation history management with token budget control.

Manages conversation history to keep costs predictable by:
- Estimating token counts for messages
- Smart truncation of large tool results (line-based)
- Removing oldest turns when over budget
- Preserving recent context

Configuration:
- Tool results are filtered when they are:
  * OLD: More than MIN_AGE_FOR_FILTERING turns old
  * LARGE: More than MIN_TOKENS_FOR_FILTERING tokens
- Filtering uses a smart line-based approach that preserves:
  * Recent content (first 2/3 of target lines)
  * Oldest content (last 1/3 of target lines)
  * This preserves both recent and historical context

Future Enhancement:
- LLM-based filtering can be enabled by setting use_llm_filtering=True
- See test-multiple-queries-review.py for the LLM filtering implementation
- LLM filtering reduces tool results by ~80% while preserving context
- Trade-off: ~90s processing time and $0.06 cost per large tool result
"""

from typing import List, Dict, Any, Optional
import json

# Configuration constants for tool result filtering
MIN_AGE_FOR_FILTERING = 5  # Filter tool results older than this many turns
MIN_TOKENS_FOR_FILTERING = 10000  # Only filter tool results larger than this
TARGET_TOKENS_AFTER_FILTERING = 2000  # Target size after filtering (for line-based)

# LLM-based filtering (disabled by default - see docstring for details)
USE_LLM_FILTERING = False  # Set to True to enable LLM-based filtering instead


class HistoryManager:
    """Manages conversation history with token budget constraints."""

    def __init__(self, max_tokens: int = 100000, keep_recent_turns: int = 10):
        """Initialize history manager.

        Args:
            max_tokens: Maximum tokens allowed in history (default ~$0.10 per request)
            keep_recent_turns: Number of recent conversation turns to always keep
        """
        self.max_tokens = max_tokens
        self.keep_recent_turns = keep_recent_turns

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count for text.

        Uses rough heuristic: 1 token ≈ 4 characters for English text.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        return len(text) // 4

    @staticmethod
    def estimate_message_tokens(message: Dict[str, Any]) -> int:
        """Estimate tokens for a message.

        Args:
            message: Message dict with 'role' and 'content'

        Returns:
            Estimated token count
        """
        content = message.get('content', '')

        # Handle string content
        if isinstance(content, str):
            return HistoryManager.estimate_tokens(content)

        # Handle list content (tool uses, tool results, etc.)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    # Serialize dict to JSON for token estimation
                    total += HistoryManager.estimate_tokens(json.dumps(item))
                elif isinstance(item, str):
                    total += HistoryManager.estimate_tokens(item)
                else:
                    # Fallback: convert to string
                    total += HistoryManager.estimate_tokens(str(item))
            return total

        # Fallback
        return HistoryManager.estimate_tokens(str(content))

    @staticmethod
    def smart_truncate_lines(text: str, target_tokens: int) -> str:
        """Smart truncation that preserves both recent and oldest content.

        Given text with N lines, where we want only M lines (M < N),
        keeps the first M*2/3 lines (recent) and last M*1/3 lines (oldest).

        This assumes the text is sorted most-recent-first, which is typical
        for tool results like search_notes.

        Args:
            text: Text to truncate (newline-separated)
            target_tokens: Target token count

        Returns:
            Truncated text with marker showing what was removed
        """
        lines = text.split('\n')
        total_lines = len(lines)

        # Calculate target lines from target tokens
        # Rough estimate: average line is ~40 chars = ~10 tokens
        target_lines = (target_tokens * 4) // 40  # Conservative estimate

        if total_lines <= target_lines:
            # Already small enough
            return text

        # Keep first 2/3 (most recent) and last 1/3 (oldest)
        recent_count = (target_lines * 2) // 3
        oldest_count = target_lines - recent_count

        recent_lines = lines[:recent_count]
        oldest_lines = lines[-oldest_count:] if oldest_count > 0 else []

        removed_count = total_lines - len(recent_lines) - len(oldest_lines)

        result = '\n'.join(recent_lines)
        if removed_count > 0:
            result += f"\n\n[... removed {removed_count} lines (~{removed_count * 10} tokens) ...]\n\n"
        if oldest_lines:
            result += '\n'.join(oldest_lines)

        return result

    @staticmethod
    def truncate_tool_result(content: List[Dict[str, Any]], max_tokens: int = 1000) -> List[Dict[str, Any]]:
        """Truncate large tool results in message content.

        Uses smart line-based truncation that preserves both recent and
        oldest content for better context preservation.

        Args:
            content: List of content blocks (may include tool_result blocks)
            max_tokens: Maximum tokens to keep per tool result

        Returns:
            Content list with truncated tool results
        """
        result = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'tool_result':
                tool_content = item.get('content', '')
                if isinstance(tool_content, str):
                    tokens = HistoryManager.estimate_tokens(tool_content)
                    if tokens > max_tokens:
                        # Use smart truncation
                        truncated = HistoryManager.smart_truncate_lines(tool_content, max_tokens)
                        item = item.copy()
                        item['content'] = truncated
                result.append(item)
            else:
                result.append(item)
        return result

    def truncate_history(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truncate conversation history to fit within token budget.

        Strategy:
        1. Count total tokens in history
        2. If over budget:
           a. Keep last N conversation turns untouched
           b. Truncate large tool results in older turns
           c. Remove oldest turns if still over budget
        3. Always preserve at least the most recent turn

        Args:
            history: List of conversation messages

        Returns:
            Truncated history that fits within budget
        """
        if not history:
            return history

        # Estimate current size
        total_tokens = sum(self.estimate_message_tokens(msg) for msg in history)

        # If under budget, return as-is (no filtering needed)
        if total_tokens <= self.max_tokens:
            return history

        # Filter tool results based on age and size
        # Process all messages and filter those that are:
        # - OLD: More than MIN_AGE_FOR_FILTERING turns old (from the end)
        # - LARGE: More than MIN_TOKENS_FOR_FILTERING tokens

        # Count conversation turns (pairs of user + assistant messages)
        user_assistant_indices = [
            i for i, msg in enumerate(history)
            if msg.get('role') in ['user', 'assistant']
        ]

        # Calculate the cutoff index for "old" messages (those eligible for filtering)
        # Messages beyond the last MIN_AGE_FOR_FILTERING turns are considered "old"
        total_turns = len(user_assistant_indices) // 2
        if total_turns > MIN_AGE_FOR_FILTERING:
            # Find the index of the message that's MIN_AGE_FOR_FILTERING turns from the end
            turns_to_keep = MIN_AGE_FOR_FILTERING * 2  # Each turn is 2 messages (user + assistant)
            filter_cutoff_index = user_assistant_indices[-turns_to_keep] if turns_to_keep < len(user_assistant_indices) else 0
        else:
            # Not enough turns - don't filter anything
            filter_cutoff_index = len(history)

        # Process messages and filter old, large tool results
        filtered_history = []
        for i, msg in enumerate(history):
            # Check if this message is old enough to be filtered
            is_old_enough = i < filter_cutoff_index

            if is_old_enough and msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                # Check if any tool results are large enough to filter
                has_large_tool_results = False
                for item in msg.get('content', []):
                    if isinstance(item, dict) and item.get('type') == 'tool_result':
                        tokens = self.estimate_tokens(str(item.get('content', '')))
                        if tokens > MIN_TOKENS_FOR_FILTERING:
                            has_large_tool_results = True
                            break

                if has_large_tool_results:
                    # Filter this message's tool results
                    msg = msg.copy()
                    if USE_LLM_FILTERING:
                        # TODO: Implement LLM-based filtering
                        # See test-multiple-queries-review.py for implementation
                        msg['content'] = self.truncate_tool_result(
                            msg['content'],
                            max_tokens=TARGET_TOKENS_AFTER_FILTERING
                        )
                    else:
                        # Use fast line-based truncation
                        msg['content'] = self.truncate_tool_result(
                            msg['content'],
                            max_tokens=TARGET_TOKENS_AFTER_FILTERING
                        )

            filtered_history.append(msg)

        # Recalculate tokens after filtering
        total_tokens = sum(self.estimate_message_tokens(msg) for msg in filtered_history)

        # If still over budget after filtering, remove oldest messages
        # But always keep at least keep_recent_turns conversation turns
        # Calculate minimum messages to keep
        min_messages_to_keep = self.keep_recent_turns * 2  # Each turn is user + assistant

        while total_tokens > self.max_tokens and len(filtered_history) > min_messages_to_keep:
            # Remove oldest message (but maintain minimum)
            removed = filtered_history.pop(0)
            total_tokens -= self.estimate_message_tokens(removed)

        new_history = filtered_history

        # Log truncation
        if len(new_history) < len(history):
            removed_count = len(history) - len(new_history)
            original_tokens = sum(self.estimate_message_tokens(msg) for msg in history)
            new_tokens = sum(self.estimate_message_tokens(msg) for msg in new_history)
            print(f"[HISTORY] Truncated {removed_count} messages: {original_tokens} → {new_tokens} tokens")

        return new_history

    def get_history_stats(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get statistics about conversation history.

        Args:
            history: List of conversation messages

        Returns:
            Dict with stats: total_tokens, message_count, turn_count, etc.
        """
        total_tokens = sum(self.estimate_message_tokens(msg) for msg in history)
        message_count = len(history)

        # Count conversation turns (user/assistant pairs)
        turn_count = len([m for m in history if m.get('role') in ['user', 'assistant']]) // 2

        # Find largest messages
        messages_by_size = sorted(
            [(i, self.estimate_message_tokens(msg), msg.get('role', 'unknown'))
             for i, msg in enumerate(history)],
            key=lambda x: x[1],
            reverse=True
        )

        largest_messages = [
            {"index": i, "tokens": tokens, "role": role}
            for i, tokens, role in messages_by_size[:5]
        ]

        return {
            "total_tokens": total_tokens,
            "message_count": message_count,
            "turn_count": turn_count,
            "largest_messages": largest_messages,
            "over_budget": total_tokens > self.max_tokens,
            "budget_usage": f"{total_tokens}/{self.max_tokens} ({100*total_tokens//self.max_tokens}%)"
        }

    @staticmethod
    def filter_tool_result_with_llm(content: str, tool_name: str, query_params: dict) -> str:
        """Filter tool results using LLM (placeholder for future implementation).

        This method would use Claude Haiku 4.5 to intelligently filter large tool
        results while preserving contextually valuable information.

        Performance characteristics (based on testing):
        - Reduction: ~80% (23k → 6k tokens typical)
        - Processing time: ~90 seconds for 23k tokens
        - Cost: ~$0.06 per filter operation
        - Quality: Preserves format, removes low-value entries

        Implementation notes:
        - Should run in background thread to avoid blocking
        - Should only be called for old (>5 turns) and large (>10k tokens) results
        - See test-multiple-queries-review.py for working implementation
        - System prompt: "You filter file search results. Output ONLY filtered entries."
        - User prompt: "Keep the N most valuable entries..." with specific criteria

        Args:
            content: Tool result content to filter
            tool_name: Name of the tool (e.g., 'search_notes')
            query_params: Parameters passed to the tool

        Returns:
            Filtered content (or original if LLM filtering fails)
        """
        # TODO: Implement LLM-based filtering
        # For now, this is just a placeholder
        # When implementing:
        # 1. Use claude-haiku-4-5 model
        # 2. Run in background thread (asyncio or threading)
        # 3. Calculate target_entries = max(100, int(total_entries * 0.30))
        # 4. Use the prompt from test-multiple-queries-review.py
        # 5. Handle errors gracefully (fall back to line-based truncation)
        raise NotImplementedError("LLM-based filtering not yet implemented")
