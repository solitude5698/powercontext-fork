"""Regression tests for existing tiers: these must keep passing."""

import pytest

from order_pricing import discount_rate, final_price


def test_silver_has_flat_five_percent():
    assert discount_rate("silver", 100) == 0.05
    assert discount_rate("silver", 5000) == 0.05


def test_gold_base_rate_below_threshold():
    assert discount_rate("gold", 499.99) == 0.10


def test_gold_gets_bulk_bonus_at_threshold():
    assert discount_rate("gold", 500) == pytest.approx(0.12)
    assert discount_rate("gold", 2000) == pytest.approx(0.12)


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        discount_rate("platinum", 100)


def test_negative_amount_raises():
    with pytest.raises(ValueError):
        discount_rate("gold", -1)


def test_final_price_rounds_to_two_decimals():
    assert final_price(99.99, "silver") == 94.99
    assert final_price(1000, "gold") == 880.0
