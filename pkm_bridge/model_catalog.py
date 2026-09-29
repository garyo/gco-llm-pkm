"""Model catalog: the models offered in the UI, discovered live where possible.

Anthropic and OpenAI models come from each provider's models endpoint, so new
releases appear without code changes. Other providers use the curated
STATIC_MODELS entries, which also stand in for a provider whose listing fails.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
import litellm

from .http_session import HttpSession

logger = logging.getLogger(__name__)

STATIC_MODELS: list[dict[str, Any]] = [
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

# Provider → required env var (None = always available). Order is display order.
PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": None,  # always available if Ollama is running
}

REFRESH_SECONDS = 12 * 3600
RETRY_SECONDS = 10 * 60
DISCOVERY_TIMEOUT = 10.0

_TIER_ORDER = {"fast": 0, "balanced": 1, "best": 2, "reasoning": 3}
_VERSION = re.compile(r"[\d.]+")


def model_family(model_id: str) -> str:
    """A model ID without its version numbers: claude-opus-5-5 → claude-opus."""
    return "-".join(part for part in _VERSION.sub("", model_id).split("-") if part)


def _newest_per_family(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    newest: dict[str, dict[str, Any]] = {}
    for model in models:
        family = model_family(model["id"])
        if family not in newest or model["created"] > newest[family]["created"]:
            newest[family] = model
    return list(newest.values())


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _anthropic_tier(model_id: str) -> str:
    if "haiku" in model_id:
        return "fast"
    if "sonnet" in model_id:
        return "balanced"
    return "best"


def _discover_anthropic() -> list[dict[str, Any]]:
    client = anthropic.Anthropic(timeout=DISCOVERY_TIMEOUT, max_retries=1)
    models = [
        {
            "id": m.id,
            "name": m.display_name.removeprefix("Claude "),
            "provider": "anthropic",
            "tier": _anthropic_tier(m.id),
            "created": m.created_at.timestamp(),
        }
        for m in client.models.list(limit=100)
    ]
    return _newest_per_family(models)


# ---------------------------------------------------------------------------
# OpenAI — its listing includes every model it has ever served, of every kind
# ---------------------------------------------------------------------------

_OPENAI_SNAPSHOT = re.compile(r"-\d{4}(-\d{2}-\d{2})?$")  # gpt-4o-2024-08-06, gpt-4-0613
_OPENAI_EXCLUDED = re.compile(r"audio|realtime|search|transcribe|tts|latest")
# A family with no release in this long is a legacy line (gpt-4o, o1, ...).
OPENAI_STALE_AFTER = timedelta(days=548)


def _is_openai_chat_model(model_id: str) -> bool:
    """A tool-calling Chat Completions model, per LiteLLM's model data."""
    info = litellm.model_cost.get(model_id, {})
    endpoints = info.get("supported_endpoints", ["/v1/chat/completions"])
    return (
        info.get("mode") == "chat"
        and bool(info.get("supports_function_calling"))
        and "/v1/chat/completions" in endpoints
        and not _OPENAI_SNAPSHOT.search(model_id)
        and not _OPENAI_EXCLUDED.search(model_id)
    )


def _openai_name(model_id: str) -> str:
    """gpt-5.4-mini → GPT-5.4 Mini; o4-mini → o4 Mini."""
    parts = model_id.split("-")
    if parts[0] == "gpt" and len(parts) > 1:
        parts = [f"GPT-{parts[1]}"] + parts[2:]
    return " ".join([parts[0]] + [p.capitalize() for p in parts[1:]])


def _openai_tier(model_id: str) -> str:
    if "mini" in model_id or "nano" in model_id:
        return "fast"
    if re.match(r"o\d", model_id):
        return "reasoning"
    return "balanced"


def _discover_openai() -> list[dict[str, Any]]:
    response = HttpSession(timeout=DISCOVERY_TIMEOUT).get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
    )
    response.raise_for_status()
    models = [
        {
            "id": m["id"],
            "name": _openai_name(m["id"]),
            "provider": "openai",
            "tier": _openai_tier(m["id"]),
            "created": float(m["created"]),
        }
        for m in response.json()["data"]
        if _is_openai_chat_model(m["id"])
    ]
    cutoff = (datetime.now(timezone.utc) - OPENAI_STALE_AFTER).timestamp()
    return [m for m in _newest_per_family(models) if m["created"] >= cutoff]


# ---------------------------------------------------------------------------
# Cached catalog
# ---------------------------------------------------------------------------

_DISCOVERERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "anthropic": _discover_anthropic,
    "openai": _discover_openai,
}
_discovered: dict[str, list[dict[str, Any]]] = {}
_next_refresh = 0.0
_lock = threading.Lock()


def _refresh() -> None:
    global _next_refresh
    succeeded = True
    for provider, discover in _DISCOVERERS.items():
        env_var = PROVIDER_KEYS[provider]
        if env_var and not os.getenv(env_var):
            continue
        try:
            models = discover()
        except Exception as e:
            succeeded = False
            fallback = "last listed" if provider in _discovered else "built-in"
            logger.warning(f"Could not list {provider} models, using {fallback} ones: {e}")
            continue
        if models:
            _discovered[provider] = models
    _next_refresh = time.monotonic() + (REFRESH_SECONDS if succeeded else RETRY_SECONDS)


def get_catalog() -> list[dict[str, Any]]:
    """Every known model, grouped by provider and ordered by tier, then age."""
    with _lock:
        if time.monotonic() >= _next_refresh:
            _refresh()
        discovered = dict(_discovered)
    catalog: list[dict[str, Any]] = []
    for provider in PROVIDER_KEYS:
        models = discovered.get(provider) or [m for m in STATIC_MODELS if m["provider"] == provider]
        catalog += sorted(models, key=lambda m: (_TIER_ORDER[m["tier"]], m.get("created", 0)))
    return catalog


def get_available_models() -> list[dict[str, Any]]:
    """The catalog, filtered to providers that have keys configured (or are local)."""
    return [
        m
        for m in get_catalog()
        if (env_var := PROVIDER_KEYS.get(m["provider"])) is None or os.getenv(env_var)
    ]


def resolve_model(model: str) -> str:
    """Resolve a Claude family alias ("haiku", "sonnet", "opus", "fable") to that
    family's newest model. Full model IDs pass through unchanged."""
    if "-" in model or "/" in model:
        return model
    family = [m["id"] for m in get_catalog() if model_family(m["id"]) == f"claude-{model}"]
    return family[-1] if family else model
