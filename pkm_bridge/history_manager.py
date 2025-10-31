"""Conversation history management with token budget control.

Manages conversation history to keep costs predictable by:
- Estimating token counts for messages
- Truncating old tool results
- Removing oldest turns when over budget
- Preserving recent context
"""

from typing import List, Dict, Any
import json


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
    def truncate_tool_result(content: List[Dict[str, Any]], max_tokens: int = 1000) -> List[Dict[str, Any]]:
        """Truncate large tool results in message content.

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
                        # Truncate and add marker
                        chars_to_keep = max_tokens * 4
                        truncated = tool_content[:chars_to_keep]
                        item = item.copy()
                        item['content'] = (
                            f"{truncated}\n\n[... truncated {tokens - max_tokens} tokens "
                            f"from tool result to save costs ...]"
                        )
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

        # If under budget, return as-is
        if total_tokens <= self.max_tokens:
            return history

        # Separate into recent and old
        # Count conversation turns (pairs of user + assistant messages)
        user_assistant_indices = [
            i for i, msg in enumerate(history)
            if msg.get('role') in ['user', 'assistant']
        ]

        # Keep at least keep_recent_turns conversation turns
        if len(user_assistant_indices) > self.keep_recent_turns * 2:
            # Find the index where we should start keeping everything
            keep_from_index = user_assistant_indices[-(self.keep_recent_turns * 2)]
        else:
            keep_from_index = 0

        recent = history[keep_from_index:]
        old = history[:keep_from_index]

        # First, try truncating large tool results in old messages
        truncated_old = []
        for msg in old:
            if msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                # Truncate tool results in this message
                msg = msg.copy()
                msg['content'] = self.truncate_tool_result(msg['content'], max_tokens=1000)
            truncated_old.append(msg)

        # Recalculate tokens
        new_history = truncated_old + recent
        total_tokens = sum(self.estimate_message_tokens(msg) for msg in new_history)

        # If still over budget, remove oldest messages
        while total_tokens > self.max_tokens and len(truncated_old) > 0:
            removed = truncated_old.pop(0)
            total_tokens -= self.estimate_message_tokens(removed)

        new_history = truncated_old + recent

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
