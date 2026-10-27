"""Discount rules for the order pricing service."""

TIER_DISCOUNTS = {
    "silver": 0.05,
    "gold": 0.10,
}

BULK_THRESHOLD = 500
BULK_BONUS = 0.02


def discount_rate(tier: str, amount: float) -> float:
    """Return the discount rate for one order.

    Unknown tiers raise ``ValueError`` so that callers never price an order
    with a silent fallback discount.
    """
    if tier not in TIER_DISCOUNTS:
        raise ValueError(f"unknown customer tier: {tier}")
    if amount < 0:
        raise ValueError("amount must not be negative")

    rate = TIER_DISCOUNTS[tier]
    if tier == "gold" and amount >= BULK_THRESHOLD:
        rate += BULK_BONUS
    return rate


def final_price(amount: float, tier: str) -> float:
    """Return the payable amount, rounded to two decimals."""
    return round(amount * (1 - discount_rate(tier, amount)), 2)
