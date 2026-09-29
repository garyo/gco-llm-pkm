"""Tests for live model discovery and family aliases."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pkm_bridge import model_catalog
from pkm_bridge.model_catalog import (
    get_available_models,
    get_catalog,
    model_family,
    resolve_model,
)


def _anthropic_model(model_id: str, name: str, year: int, month: int) -> SimpleNamespace:
    created = datetime(year, month, 1, tzinfo=timezone.utc)
    return SimpleNamespace(id=model_id, display_name=name, created_at=created)


ANTHROPIC_LISTING = [
    _anthropic_model("claude-sonnet-5-5", "Claude Sonnet 5.5", 2026, 9),
    _anthropic_model("claude-opus-5-5", "Claude Opus 5.5", 2026, 8),
    _anthropic_model("claude-sonnet-5", "Claude Sonnet 5", 2026, 5),
    _anthropic_model("claude-opus-5", "Claude Opus 5", 2026, 4),
    _anthropic_model("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 2025, 10),
    _anthropic_model("claude-3-5-haiku-20241022", "Claude Haiku 3.5", 2024, 10),
]


@pytest.fixture
def anthropic_listing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        model_catalog, "_DISCOVERERS", {"anthropic": model_catalog._discover_anthropic}
    )
    client = MagicMock()
    client.models.list.return_value = ANTHROPIC_LISTING
    with patch.object(model_catalog.anthropic, "Anthropic", return_value=client):
        yield client


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("claude-opus-5-5", "claude-opus"),
        ("claude-haiku-4-5-20251001", "claude-haiku"),
        ("claude-3-5-haiku-20241022", "claude-haiku"),
        ("gpt-5.4-mini", "gpt-mini"),
        ("gpt-4o", "gpt-o"),
        ("o4-mini", "o-mini"),
    ],
)
def test_model_family_drops_version_numbers(model_id, family):
    assert model_family(model_id) == family


def test_discovered_anthropic_models_replace_the_built_in_ones(anthropic_listing):
    anthropic = [m for m in get_catalog() if m["provider"] == "anthropic"]
    assert [(m["id"], m["name"], m["tier"]) for m in anthropic] == [
        ("claude-haiku-4-5-20251001", "Haiku 4.5", "fast"),
        ("claude-sonnet-5-5", "Sonnet 5.5", "balanced"),
        ("claude-opus-5-5", "Opus 5.5", "best"),
    ]


def test_catalog_is_cached_between_calls(anthropic_listing):
    get_catalog()
    get_catalog()
    assert anthropic_listing.models.list.call_count == 1


def test_family_alias_resolves_to_newest_discovered_model(anthropic_listing):
    assert resolve_model("sonnet") == "claude-sonnet-5-5"
    assert resolve_model("haiku") == "claude-haiku-4-5-20251001"
    assert resolve_model("claude-sonnet-5") == "claude-sonnet-5"
    assert resolve_model("gemini/gemini-2.5-pro") == "gemini/gemini-2.5-pro"


def test_failed_discovery_falls_back_to_built_in_models(anthropic_listing, caplog):
    anthropic_listing.models.list.side_effect = RuntimeError("network down")
    ids = [m["id"] for m in get_catalog() if m["provider"] == "anthropic"]
    assert ids == [m["id"] for m in model_catalog.STATIC_MODELS if m["provider"] == "anthropic"]
    assert resolve_model("opus") == "claude-opus-5-5"
    assert "network down" in caplog.text


def test_role_defaults_follow_the_newest_family_model(anthropic_listing):
    from pkm_bridge.models import get_role_model

    with patch.dict("pkm_bridge.models.MODEL_ROLES", {"scheduler": "sonnet"}):
        assert get_role_model("scheduler") == "claude-sonnet-5-5"


def test_openai_discovery_keeps_current_tool_calling_chat_models(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    now = datetime.now(timezone.utc).timestamp()
    day = 86400
    listing = [
        {"id": "gpt-5.6", "created": now - 30 * day},
        {"id": "gpt-5.5", "created": now - 90 * day},
        {"id": "gpt-5.4-mini", "created": now - 120 * day},
        {"id": "gpt-5.4-mini-2026-03-01", "created": now - 120 * day},
        {"id": "gpt-4o", "created": now - 800 * day},
        {"id": "gpt-realtime", "created": now - 30 * day},
        {"id": "text-embedding-3-large", "created": now - 30 * day},
    ]
    chat = {"mode": "chat", "supports_function_calling": True}
    model_cost = {
        "gpt-5.6": chat,
        "gpt-5.5": chat,
        "gpt-5.4-mini": chat,
        "gpt-5.4-mini-2026-03-01": chat,
        "gpt-4o": chat,
        "gpt-realtime": chat,
        "text-embedding-3-large": {"mode": "embedding"},
    }
    response = MagicMock()
    response.json.return_value = {"data": listing}
    with (
        patch.object(model_catalog.HttpSession, "get", return_value=response),
        patch.dict(model_catalog.litellm.model_cost, model_cost),
    ):
        models = model_catalog._discover_openai()
    assert sorted((m["id"], m["name"], m["tier"]) for m in models) == [
        ("gpt-5.4-mini", "GPT-5.4 Mini", "fast"),
        ("gpt-5.6", "GPT-5.6", "balanced"),
    ]


def test_available_models_require_a_provider_key(monkeypatch):
    for env_var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    assert {m["provider"] for m in get_available_models()} == {"google"}
