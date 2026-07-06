"""Tests for date-aware Anthropic cost rates (Sonnet 5 introductory pricing)."""

from datetime import date

from pkm_bridge import models


def test_sonnet5_intro_pricing_through_aug_31():
    """Introductory $2/$10 applies through 2026-08-31 (inclusive)."""
    for d in (date(2026, 7, 1), date(2026, 8, 31)):
        rates = models.get_cost_rates("claude-sonnet-5", d)
        assert rates["input"] == 2.00
        assert rates["output"] == 10.00
        # Cache rates follow the table convention: 1.25x input, 0.10x input.
        assert rates["cache_write"] == 2.50
        assert rates["cache_read"] == 0.20


def test_sonnet5_standard_pricing_from_sep_1():
    """Standard $3/$15 applies from 2026-09-01 onward."""
    rates = models.get_cost_rates("claude-sonnet-5", date(2026, 9, 1))
    assert rates["input"] == 3.00
    assert rates["output"] == 15.00
    assert rates["cache_write"] == 3.75
    assert rates["cache_read"] == 0.30


def test_sonnet5_cost_across_boundary():
    """1M input + 1M output: $12 during intro, $18 after."""
    args = ("claude-sonnet-5", 1_000_000, 1_000_000)
    assert models.get_anthropic_cost(*args, on_date=date(2026, 8, 31)) == 12.00
    assert models.get_anthropic_cost(*args, on_date=date(2026, 9, 1)) == 18.00


def test_unknown_model_falls_back_to_haiku_rates():
    rates = models.get_cost_rates("claude-does-not-exist")
    assert rates == models.ANTHROPIC_COST_RATES["claude-haiku-4-5"]
