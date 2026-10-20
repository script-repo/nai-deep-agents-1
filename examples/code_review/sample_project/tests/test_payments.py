from decimal import Decimal

import pytest

from app.payments import apply_discount, to_cents


def test_to_cents_rounds_half_up() -> None:
    assert to_cents(Decimal("10.005")) == 1001


def test_apply_discount() -> None:
    assert apply_discount(10_000, 15) == 8_500


def test_apply_discount_rejects_bad_percent() -> None:
    with pytest.raises(ValueError):
        apply_discount(100, 101)
