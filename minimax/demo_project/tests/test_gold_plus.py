"""Target tests for the new gold_plus tier.

These tests fail before the feature lands and must all pass afterwards.
"""

import pytest

from order_pricing import discount_rate, final_price


def test_gold_plus_large_order_uses_fifteen_percent():
    assert discount_rate("gold_plus", 1000) == pytest.approx(0.15)
    assert discount_rate("gold_plus", 5000) == pytest.approx(0.15)


def test_gold_plus_below_large_threshold_follows_gold_bonus():
    assert discount_rate("gold_plus", 999.99) == pytest.approx(0.12)
    assert discount_rate("gold_plus", 500) == pytest.approx(0.12)


def test_gold_plus_small_order_keeps_gold_base_rate():
    assert discount_rate("gold_plus", 499.99) == pytest.approx(0.10)


def test_gold_plus_final_price():
    assert final_price(1500, "gold_plus") == 1275.0
    assert final_price(100, "gold_plus") == 90.0
