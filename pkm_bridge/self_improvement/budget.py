"""Token/action budget tracking for the self-improvement agent.

Prevents runaway costs by enforcing limits on turns, write actions,
and token usage per agent invocation.
"""

from dataclasses import dataclass, field

from ..models import TokenUsage


@dataclass
class Budget:
    """Tracks resource usage for a single agent run.

    Attributes:
        max_turns: Maximum API round-trips allowed.
        max_actions: Maximum write operations (skills + rules + amendments).
        max_input_tokens: Maximum billable input tokens across all turns. Cache
            writes and reads count at their price relative to uncached input
            (see TokenUsage.billable_input_tokens), so the cap bounds input cost
            whether or not the prompt cache hits.
        max_output_tokens: Maximum output tokens across all turns.
        model: The model being run, which sets the cache-token weights.
    """

    max_turns: int = 15
    max_actions: int = 10
    max_input_tokens: int = 50_000
    max_output_tokens: int = 20_000
    model: str = ""

    turns_used: int = field(default=0, init=False)
    actions_used: int = field(default=0, init=False)
    usage: TokenUsage = field(default_factory=TokenUsage, init=False)
    billable_input_tokens: float = field(default=0.0, init=False)
    cost_usd: float = field(default=0.0, init=False)

    def record_turn(self, usage: TokenUsage, cost_usd: float = 0.0) -> None:
        """Record an API round-trip, its token usage and its dollar cost."""
        self.turns_used += 1
        self.usage += usage
        self.billable_input_tokens += usage.billable_input_tokens(self.model)
        self.cost_usd += cost_usd

    def record_action(self) -> None:
        """Record a write operation (skill, rule, amendment, etc.)."""
        self.actions_used += 1

    @property
    def output_tokens_used(self) -> int:
        return self.usage.output_tokens

    @property
    def turns_remaining(self) -> int:
        return max(0, self.max_turns - self.turns_used)

    @property
    def actions_remaining(self) -> int:
        return max(0, self.max_actions - self.actions_used)

    @property
    def can_continue(self) -> bool:
        """Whether the agent has budget left for another turn."""
        return self.stop_reason is None

    @property
    def can_act(self) -> bool:
        """Whether the agent has budget left for a write action."""
        return self.actions_used < self.max_actions

    @property
    def stop_reason(self) -> str | None:
        """Return the reason the budget is exhausted, or None if still good."""
        if self.turns_used >= self.max_turns:
            return f"max turns ({self.max_turns})"
        if self.billable_input_tokens >= self.max_input_tokens:
            return f"input token cap ({self.max_input_tokens})"
        if self.output_tokens_used >= self.max_output_tokens:
            return f"output token cap ({self.max_output_tokens})"
        return None

    def summary(self) -> dict:
        """Return a summary dict for logging and the run log."""
        return {
            "turns": f"{self.turns_used}/{self.max_turns}",
            "actions": f"{self.actions_used}/{self.max_actions}",
            "input_tokens": f"{round(self.billable_input_tokens)}/{self.max_input_tokens}",
            "output_tokens": f"{self.output_tokens_used}/{self.max_output_tokens}",
            "cache_write_tokens": self.usage.cache_write_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 4),
        }
