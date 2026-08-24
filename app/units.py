from __future__ import annotations

from decimal import Decimal


def pct_points_to_fraction(value: float | int | str | Decimal) -> Decimal:
    """
    Human config percentage-points -> canonical DB fraction.
    Example: -1.10 (%) -> -0.011
    """
    return Decimal(str(value)) / Decimal("100")


def fraction_to_pct_points(value: float | int | str | Decimal) -> Decimal:
    """
    Canonical DB fraction -> human percentage-points.
    Example: -0.011 -> -1.10 (%)
    """
    return Decimal(str(value)) * Decimal("100")
