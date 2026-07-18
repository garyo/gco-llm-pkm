"""Model configuration and catalog for multi-LLM support.

Provides role-based model defaults (configurable via env vars),
an available models catalog for the frontend, and capability detection.
"""

import os
from datetime import date, datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Role-based model defaults
# Each "role" in the system can use a different model, configured via env var.
# ---------------------------------------------------------------------------

MODEL_ROLES: dict[str, str] = {
    "chat": os.getenv("MODEL_CHAT", os.getenv("MODEL", "claude-haiku-4-5")),
    "voice": os.getenv("MODEL_VOICE", "claude-haiku-4-5"),
    "retrospective": os.getenv("MODEL_RETROSPECTIVE", "claude-sonnet-4-6"),
    "scheduler": os.getenv("MODEL_SCHEDULER", "claude-sonnet-4-6"),
    "self_improvement": os.getenv("MODEL_SELF_IMPROVEMENT", "claude-sonnet-4-6"),
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
    {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6", "provider": "anthropic", "tier": "balanced"},
    {"id": "claude-sonnet-5", "name": "Sonnet 5", "provider": "anthropic", "tier": "balanced"},
    {"id": "claude-opus-4-7", "name": "Opus 4.7", "provider": "anthropic", "tier": "best"},
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


# Anthropic models that support dynamic filtering (web_search_20260209 runs
# searches through code execution, filtering results before they hit context).
# Older Claude models (Haiku 4.5, Sonnet 4.5, ...) get the basic tool version.
_WEB_SEARCH_FILTERING_PREFIXES = (
    "claude-fable",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
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
# Cost rates for Anthropic models (per million tokens)
# Non-Anthropic models use litellm.completion_cost() instead.
# ---------------------------------------------------------------------------

ANTHROPIC_COST_RATES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input": 0.80,
        "cache_write": 1.00,
        "cache_read": 0.08,
        "output": 4.00,
    },
    "claude-sonnet-4-5": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
    "claude-opus-4-5": {
        "input": 5.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
        "output": 25.00,
    },
    "claude-opus-4-6": {
        "input": 5.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
        "output": 25.00,
    },
    "claude-opus-4-7": {
        "input": 5.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
        "output": 25.00,
    },
    # Standard rates; Sonnet 5 also has introductory pricing (see below).
    "claude-sonnet-5": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
}

# Time-limited introductory rates that revert to the standard table above the
# day after ``through`` (inclusive). Applied automatically by the current date.
INTRO_COST_RATES: dict[str, dict[str, Any]] = {
    "claude-sonnet-5": {
        "through": date(2026, 8, 31),
        "rates": {
            "input": 2.00,
            "cache_write": 2.50,
            "cache_read": 0.20,
            "output": 10.00,
        },
    },
}


def get_cost_rates(model: str, on_date: date | None = None) -> dict[str, float]:
    """Resolve the effective per-million-token rates for a model on a given date.

    Uses introductory pricing while in effect, otherwise the standard rates
    (falling back to Haiku rates for unknown models). ``on_date`` defaults to
    the current UTC date.
    """
    intro = INTRO_COST_RATES.get(model)
    if intro is not None:
        today = on_date or datetime.now(timezone.utc).date()
        if today <= intro["through"]:
            return intro["rates"]
    return ANTHROPIC_COST_RATES.get(model, ANTHROPIC_COST_RATES["claude-haiku-4-5"])


def get_anthropic_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    on_date: date | None = None,
    web_search_requests: int = 0,
) -> float:
    """Calculate cost for an Anthropic model call. Returns cost in dollars."""
    rates = get_cost_rates(model, on_date)
    return (
        (input_tokens * rates["input"])
        + (cache_write_tokens * rates["cache_write"])
        + (cache_read_tokens * rates["cache_read"])
        + (output_tokens * rates["output"])
    ) / 1_000_000 + (web_search_requests * WEB_SEARCH_COST_PER_SEARCH)
