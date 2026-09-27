"""Tests for Anthropic cost rates and cost calculation."""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm_bridge import models
from pkm_bridge.llm import LLMResponse, response_cost


def test_haiku_4_5_rates():
    assert models.get_cost_rates("claude-haiku-4-5") == {
        "input": 1.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
        "output": 5.00,
    }


def test_sonnet5_standard_pricing():
    """$2/$10 is Sonnet 5's standard price; the planned rise to $3/$15 was cancelled."""
    rates = models.get_cost_rates("claude-sonnet-5")
    assert (rates["input"], rates["output"]) == (2.00, 10.00)
    assert (rates["cache_write"], rates["cache_read"]) == (2.50, 0.20)


@pytest.mark.parametrize(
    "model, cache_read",
    [("claude-opus-5-5", 0.20), ("claude-fable-5-1", 0.25), ("claude-fable-5", 1.00)],
)
def test_models_with_nonstandard_cache_reads(model, cache_read):
    assert models.get_cost_rates(model)["cache_read"] == cache_read


def test_cost_includes_cache_tokens_and_web_searches():
    # 1M of each token kind on Haiku 4.5: 1 + 5 + 1.25 + 0.10, plus 2 searches at $0.01.
    cost = models.get_anthropic_cost(
        "claude-haiku-4-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        web_search_requests=2,
    )
    assert cost == pytest.approx(7.37)


def test_dated_snapshot_uses_longest_known_prefix():
    assert models.get_cost_rates("claude-haiku-4-5-20251001") == models.get_cost_rates(
        "claude-haiku-4-5"
    )
    assert models.get_cost_rates("claude-opus-5-5") != models.get_cost_rates("claude-opus-5")


def test_unknown_model_is_costed_conservatively_and_warns(caplog):
    models._warned_unpriced_models.discard("claude-does-not-exist")
    with caplog.at_level(logging.WARNING, logger="pkm_bridge.models"):
        rates = models.get_cost_rates("claude-does-not-exist")
        models.get_cost_rates("claude-does-not-exist")
    most_expensive = max(r["output"] for r in models.ANTHROPIC_COST_RATES.values())
    assert rates["output"] == most_expensive
    assert len([r for r in caplog.records if "claude-does-not-exist" in r.message]) == 1


def _anthropic_response(**usage):
    return SimpleNamespace(usage=SimpleNamespace(**usage))


def test_token_usage_reads_optional_sdk_fields():
    response = _anthropic_response(
        input_tokens=3,
        output_tokens=200,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=40_000,
        server_tool_use=SimpleNamespace(web_search_requests=1),
    )
    usage = models.TokenUsage.from_response(response)
    assert usage == models.TokenUsage(3, 200, 0, 40_000, 1)


def test_billable_input_weights_cache_tokens_by_price():
    usage = models.TokenUsage(input_tokens=100, cache_write_tokens=1000, cache_read_tokens=10_000)
    # Haiku 4.5: writes cost 1.25x uncached input, reads 0.1x.
    assert usage.billable_input_tokens("claude-haiku-4-5") == pytest.approx(100 + 1250 + 1000)
    assert usage.billable_input_tokens("gpt-4o") == 11_100


def test_response_cost_for_anthropic_includes_cache_tokens():
    response = _anthropic_response(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=1_000_000
    )
    assert response_cost("claude-sonnet-5", response) == pytest.approx(0.20)


def test_response_cost_for_litellm_uses_its_price_table():
    response = LLMResponse(_raw_response=object())
    with patch("pkm_bridge.llm.litellm.completion_cost", return_value=0.0123):
        assert response_cost("gpt-4o", response) == 0.0123
    with patch("pkm_bridge.llm.litellm.completion_cost", side_effect=ValueError("unknown")):
        assert response_cost("gpt-4o", response) == 0.0
