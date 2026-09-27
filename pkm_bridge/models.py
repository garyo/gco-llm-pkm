"""Model configuration and catalog for multi-LLM support.

Provides role-based model defaults (configurable via env vars),
an available models catalog for the frontend, and capability detection.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role-based model defaults
# Each "role" in the system can use a different model, configured via env var.
# ---------------------------------------------------------------------------

MODEL_ROLES: dict[str, str] = {
    "chat": os.getenv("MODEL_CHAT", os.getenv("MODEL", "claude-haiku-4-5")),
    "voice": os.getenv("MODEL_VOICE", "claude-haiku-4-5"),
    "retrospective": os.getenv("MODEL_RETROSPECTIVE", "claude-sonnet-5"),
    "scheduler": os.getenv("MODEL_SCHEDULER", "claude-sonnet-5"),
    "self_improvement": os.getenv("MODEL_SELF_IMPROVEMENT", "claude-sonnet-5"),
    "curation": os.getenv("MODEL_CURATION", "claude-sonnet-5"),
}


def get_role_model(role: str) -> str:
    """Get the configured model for a given role."""
    return MODEL_ROLES.get(role, MODEL_ROLES["chat"])


# ---------------------------------------------------------------------------
# Available models catalog — drives the frontend dropdown and /api/models
# ---------------------------------------------------------------------------

AVAILABLE_MODELS: list[dict[str, Any]] = [
    # Anthropic (direct)
    {"id": "claude-haiku-4-5", "name": "Haiku 4.5", "provider": "anthropic", "tier": "fast"},
    {"id": "claude-sonnet-5", "name": "Sonnet 5", "provider": "anthropic", "tier": "balanced"},
    {"id": "claude-opus-5", "name": "Opus 5", "provider": "anthropic", "tier": "best"},
    {"id": "claude-opus-5-5", "name": "Opus 5.5", "provider": "anthropic", "tier": "best"},
    {"id": "claude-fable-5-1", "name": "Fable 5.1", "provider": "anthropic", "tier": "best"},
    # OpenAI (direct)
    {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai", "tier": "balanced"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "tier": "fast"},
    # Google (direct)
    {
        "id": "gemini/gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "google",
        "tier": "fast",
    },
    {
        "id": "gemini/gemini-3.5-flash",
        "name": "Gemini 3.5 Flash",
        "provider": "google",
        "tier": "fast",
    },
    {
        "id": "gemini/gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "google",
        "tier": "balanced",
    },
    # OpenRouter (many models behind one key)
    {
        "id": "openrouter/deepseek/deepseek-r1",
        "name": "DeepSeek R1",
        "provider": "openrouter",
        "tier": "reasoning",
    },
    {
        "id": "openrouter/deepseek/deepseek-chat-v3",
        "name": "DeepSeek V3",
        "provider": "openrouter",
        "tier": "fast",
    },
    {
        "id": "openrouter/deepseek/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "provider": "openrouter",
        "tier": "reasoning",
    },
    {
        "id": "openrouter/deepseek/deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "provider": "openrouter",
        "tier": "fast",
    },
    {
        "id": "openrouter/qwen/qwen3.6-max-preview",
        "name": "Qwen3.6 Max (preview)",
        "provider": "openrouter",
        "tier": "best",
    },
    {
        "id": "openrouter/qwen/qwen3.6-plus",
        "name": "Qwen3.6 Plus",
        "provider": "openrouter",
        "tier": "balanced",
    },
    {
        "id": "openrouter/z-ai/glm-5.1",
        "name": "GLM 5.1",
        "provider": "openrouter",
        "tier": "balanced",
    },
]


def get_available_models() -> list[dict[str, Any]]:
    """Return models filtered to providers that have keys configured (or are local)."""
    # Provider → required env var (None = always available)
    provider_keys: dict[str, str | None] = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "ollama": None,  # always available if Ollama is running
    }
    available = []
    for model in AVAILABLE_MODELS:
        env_var = provider_keys.get(model["provider"])
        if env_var is None or os.getenv(env_var):
            available.append(model)
    return available


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


def is_anthropic(model: str) -> bool:
    """Check if a model ID routes to the Anthropic API."""
    return model.startswith("claude-")


def supports_tools(model: str) -> bool:
    """Check if a model is known to support tool/function calling well."""
    # Most major models support tools; small local models may not
    no_tool_prefixes = ("ollama/mistral:7b", "ollama/phi")
    return not model.startswith(no_tool_prefixes)


def supports_thinking(model: str) -> bool:
    """Only Anthropic models support extended thinking."""
    return is_anthropic(model)


# Claude models from before adaptive thinking (Claude 4.6). They take a fixed
# thinking budget; every later model rejects `budget_tokens` or deprecates it.
_BUDGET_THINKING_PREFIXES = (
    "claude-3",
    "claude-haiku-4-5",
    "claude-sonnet-4-0",
    "claude-sonnet-4-2",  # dated IDs like claude-sonnet-4-20250514
    "claude-sonnet-4-5",
    "claude-opus-4-0",
    "claude-opus-4-1",
    "claude-opus-4-2",
    "claude-opus-4-5",
)
THINKING_BUDGET_TOKENS = 10_000
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"


def thinking_params(model: str) -> dict[str, Any]:
    """Request parameters that turn on "deep thinking" for `model`.

    Current models use adaptive thinking; "summarized" display because Sonnet 5
    and Opus 4.7+ otherwise stream empty thinking text. Older models need a
    fixed budget, plus a beta header to think between tool calls (adaptive
    thinking interleaves on its own). Returns {} for non-Anthropic models.
    """
    if not supports_thinking(model):
        return {}
    if not model.startswith(_BUDGET_THINKING_PREFIXES):
        return {"thinking": {"type": "adaptive", "display": "summarized"}}
    return {
        "thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
        "extra_headers": {"anthropic-beta": INTERLEAVED_THINKING_BETA},
    }


# Anthropic models that support dynamic filtering (web_search_20260209 runs
# searches through code execution, filtering results before they hit context).
# Older Claude models (Haiku 4.5, Sonnet 4.5, ...) get the basic tool version.
_WEB_SEARCH_FILTERING_PREFIXES = (
    "claude-fable",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
)

# Anthropic charges $10 per 1,000 web searches, on top of token costs.
WEB_SEARCH_COST_PER_SEARCH = 0.01


def web_search_tool(model: str) -> dict[str, Any] | None:
    """Return the Anthropic server-side web search tool definition for `model`.

    Returns None for non-Anthropic models (the server tool only exists on
    Anthropic's API) or when disabled via WEB_SEARCH_ENABLED=0.
    """
    if not is_anthropic(model) or os.getenv("WEB_SEARCH_ENABLED", "1") == "0":
        return None
    if model.startswith(_WEB_SEARCH_FILTERING_PREFIXES):
        tool_type = "web_search_20260209"
    else:
        tool_type = "web_search_20250305"
    return {
        "type": tool_type,
        "name": "web_search",
        "max_uses": int(os.getenv("WEB_SEARCH_MAX_USES", "5")),
    }


def supports_caching(model: str) -> bool:
    """Models that support prompt caching via cache_control hints.

    Anthropic uses transparent ephemeral caching — always on, always safe.

    Gemini supports explicit context caching, but Google's free tier disallows
    cached-content storage entirely (limit=0). To avoid breaking free-tier
    users, explicit caching for Gemini is opt-in via GEMINI_EXPLICIT_CACHING=1.
    Implicit caching on Gemini 2.5+ happens automatically and needs no hint.
    """
    if is_anthropic(model):
        return True
    if model.startswith("gemini/") and os.getenv("GEMINI_EXPLICIT_CACHING") == "1":
        return True
    return False


# ---------------------------------------------------------------------------
# Cost rates for Anthropic models (per million tokens, 5-minute cache writes)
# Non-Anthropic models use litellm.completion_cost() instead.
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# ---------------------------------------------------------------------------


def _rates(input_: float, cache_write: float, cache_read: float, output: float) -> dict[str, float]:
    return {"input": input_, "cache_write": cache_write, "cache_read": cache_read, "output": output}


ANTHROPIC_COST_RATES: dict[str, dict[str, float]] = {
    # _rates(input, cache write, cache read, output)
    "claude-haiku-4-5": _rates(1.00, 1.25, 0.10, 5.00),
    "claude-sonnet-4-5": _rates(3.00, 3.75, 0.30, 15.00),
    "claude-sonnet-4-6": _rates(3.00, 3.75, 0.30, 15.00),
    "claude-sonnet-5": _rates(2.00, 2.50, 0.20, 10.00),
    "claude-opus-4-5": _rates(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-6": _rates(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-7": _rates(5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-8": _rates(5.00, 6.25, 0.50, 25.00),
    "claude-opus-5": _rates(5.00, 6.25, 0.50, 25.00),
    "claude-opus-5-5": _rates(4.00, 5.00, 0.20, 20.00),
    "claude-fable-5": _rates(10.00, 12.50, 1.00, 50.00),
    "claude-fable-5-1": _rates(10.00, 12.50, 0.25, 50.00),
}

# Unknown models are priced at the most expensive known tier, so a missing
# table entry over-reports cost rather than hiding it.
_FALLBACK_COST_RATES = max(ANTHROPIC_COST_RATES.values(), key=lambda r: r["output"])
_warned_unpriced_models: set[str] = set()


def get_cost_rates(model: str) -> dict[str, float]:
    """Resolve the per-million-token rates for a model.

    Matches the model ID exactly or, for dated snapshots such as
    ``claude-haiku-4-5-20251001``, by its longest known prefix.
    """
    if model in ANTHROPIC_COST_RATES:
        return ANTHROPIC_COST_RATES[model]
    prefixes = [known for known in ANTHROPIC_COST_RATES if model.startswith(known + "-")]
    if prefixes:
        return ANTHROPIC_COST_RATES[max(prefixes, key=len)]
    if model not in _warned_unpriced_models:
        _warned_unpriced_models.add(model)
        logger.warning(
            f"No pricing for model {model!r}; costing it at the most expensive known "
            f"rates. Add it to ANTHROPIC_COST_RATES."
        )
    return _FALLBACK_COST_RATES


def get_anthropic_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    web_search_requests: int = 0,
) -> float:
    """Calculate cost for an Anthropic model call. Returns cost in dollars."""
    rates = get_cost_rates(model)
    return (
        (input_tokens * rates["input"])
        + (cache_write_tokens * rates["cache_write"])
        + (cache_read_tokens * rates["cache_read"])
        + (output_tokens * rates["output"])
    ) / 1_000_000 + (web_search_requests * WEB_SEARCH_COST_PER_SEARCH)


def _count(value: Any) -> int:
    """A token count from an SDK usage field, which may be None or absent."""
    return value if isinstance(value, int) else 0


@dataclass
class TokenUsage:
    """Token counts in Anthropic's terms: `input_tokens` excludes cached input."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    web_search_requests: int = 0

    @classmethod
    def from_response(cls, response: Any) -> TokenUsage:
        """Read the usage of an LLMClient response (Anthropic or normalized LiteLLM)."""
        usage = getattr(response, "usage", None)
        server_use = getattr(usage, "server_tool_use", None)
        return cls(
            input_tokens=_count(getattr(usage, "input_tokens", 0)),
            output_tokens=_count(getattr(usage, "output_tokens", 0)),
            cache_write_tokens=_count(getattr(usage, "cache_creation_input_tokens", 0)),
            cache_read_tokens=_count(getattr(usage, "cache_read_input_tokens", 0)),
            web_search_requests=_count(getattr(server_use, "web_search_requests", 0)),
        )

    def __iadd__(self, other: TokenUsage) -> TokenUsage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.web_search_requests += other.web_search_requests
        return self

    def anthropic_cost(self, model: str) -> float:
        return get_anthropic_cost(
            model,
            self.input_tokens,
            self.output_tokens,
            self.cache_write_tokens,
            self.cache_read_tokens,
            web_search_requests=self.web_search_requests,
        )

    def billable_input_tokens(self, model: str) -> float:
        """All input, with cache writes and reads weighted by their price relative to
        uncached input, so N billable tokens cost the same as N uncached ones.

        Non-Anthropic models count every input token in full.
        """
        cached = self.cache_write_tokens + self.cache_read_tokens
        if not is_anthropic(model):
            return self.input_tokens + cached
        rates = get_cost_rates(model)
        weighted = (
            self.cache_write_tokens * rates["cache_write"]
            + self.cache_read_tokens * rates["cache_read"]
        )
        return self.input_tokens + weighted / rates["input"]
