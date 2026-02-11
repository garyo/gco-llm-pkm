"""Token/action budget tracking for the self-improvement agent.

Prevents runaway costs by enforcing limits on turns, write actions,
and token usage per agent invocation.
"""

from dataclasses import dataclass, field


@dataclass
class Budget:
    """Tracks resource usage for a single agent run.

    Attributes:
        max_turns: Maximum API round-trips allowed.
        max_actions: Maximum write operations (skills + rules + amendments).
        max_input_tokens: Maximum input tokens across all turns.
        max_output_tokens: Maximum output tokens across all turns.
    """

    max_turns: int = 15
    max_actions: int = 10
    max_input_tokens: int = 50_000
    max_output_tokens: int = 20_000

    turns_used: int = field(default=0, init=False)
    actions_used: int = field(default=0, init=False)
    input_tokens_used: int = field(default=0, init=False)
    output_tokens_used: int = field(default=0, init=False)

    def record_turn(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Record an API round-trip and its token usage."""
        self.turns_used += 1
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens

    def record_action(self) -> None:
        """Record a write operation (skill, rule, amendment, etc.)."""
        self.actions_used += 1

    @property
    def turns_remaining(self) -> int:
        return max(0, self.max_turns - self.turns_used)

    @property
    def actions_remaining(self) -> int:
        return max(0, self.max_actions - self.actions_used)

    @property
    def can_continue(self) -> bool:
        """Whether the agent has budget left for another turn."""
        if self.turns_used >= self.max_turns:
            return False
        if self.input_tokens_used >= self.max_input_tokens:
            return False
        if self.output_tokens_used >= self.max_output_tokens:
            return False
        return True

    @property
    def can_act(self) -> bool:
        """Whether the agent has budget left for a write action."""
        return self.actions_used < self.max_actions

    @property
    def stop_reason(self) -> str | None:
        """Return the reason the budget is exhausted, or None if still good."""
        if self.turns_used >= self.max_turns:
            return f"max turns ({self.max_turns})"
        if self.input_tokens_used >= self.max_input_tokens:
            return f"input token cap ({self.max_input_tokens})"
        if self.output_tokens_used >= self.max_output_tokens:
            return f"output token cap ({self.max_output_tokens})"
        return None

    def summary(self) -> dict:
        """Return a summary dict for logging and the run log."""
        return {
            "turns": f"{self.turns_used}/{self.max_turns}",
            "actions": f"{self.actions_used}/{self.max_actions}",
            "input_tokens": f"{self.input_tokens_used}/{self.max_input_tokens}",
            "output_tokens": f"{self.output_tokens_used}/{self.max_output_tokens}",
        }
