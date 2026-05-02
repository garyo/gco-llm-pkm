"""Model configuration and catalog for multi-LLM support.

Provides role-based model defaults (configurable via env vars),
an available models catalog for the frontend, and capability detection.
"""

import os
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


def supports_caching(model: str) -> bool:
    """Only Anthropic models support prompt caching."""
    return is_anthropic(model)


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
}


def get_anthropic_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calculate cost for an Anthropic model call. Returns cost in dollars."""
    rates = ANTHROPIC_COST_RATES.get(model, ANTHROPIC_COST_RATES["claude-haiku-4-5"])
    return (
        (input_tokens * rates["input"])
        + (cache_write_tokens * rates["cache_write"])
        + (cache_read_tokens * rates["cache_read"])
        + (output_tokens * rates["output"])
    ) / 1_000_000
