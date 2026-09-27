"""Tests for the per-model "deep thinking" request parameters."""

import pytest

from pkm_bridge.models import (
    INTERLEAVED_THINKING_BETA,
    THINKING_BUDGET_TOKENS,
    thinking_params,
)

ADAPTIVE = {"thinking": {"type": "adaptive", "display": "summarized"}}


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
    ],
)
def test_current_models_use_adaptive_thinking_without_beta(model):
    assert thinking_params(model) == ADAPTIVE


@pytest.mark.parametrize(
    "model",
    ["claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-sonnet-4-5", "claude-opus-4-1"],
)
def test_older_models_use_a_budget_and_the_interleaved_beta(model):
    assert thinking_params(model) == {
        "thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
        "extra_headers": {"anthropic-beta": INTERLEAVED_THINKING_BETA},
    }


def test_non_anthropic_models_get_no_thinking_params():
    assert thinking_params("gemini/gemini-2.5-pro") == {}
