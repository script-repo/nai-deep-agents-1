"""Payment helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal) -> int:
    """Convert a decimal amount in dollars to integer cents, rounding half up."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_discount(total_cents: int, percent: int) -> int:
    """Apply a whole-number percentage discount."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return total_cents - total_cents * percent // 100
