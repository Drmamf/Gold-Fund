from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR


ZERO = Decimal("0")
TOMAN_TO_RIAL = Decimal("10")


def toman_to_rial(toman: int | str | Decimal) -> Decimal:
    return Decimal(str(toman)) * TOMAN_TO_RIAL


def live_buy_budget_rial(
    *,
    buying_power_rial: int | str | Decimal,
    cap_rial: int | str | Decimal,
) -> Decimal:
    """Cash allowed for a NEW live entry: min(broker power, configured cap)."""
    power = Decimal(str(buying_power_rial))
    cap = Decimal(str(cap_rial))
    if power < ZERO or cap < ZERO:
        return ZERO
    return min(power, cap)


def qty_for_budget(
    *,
    budget_rial: int | str | Decimal,
    price_rial: int | str | Decimal,
    unit_step: int | str | Decimal = 1,
) -> Decimal:
    budget = Decimal(str(budget_rial))
    price = Decimal(str(price_rial))
    step = Decimal(str(unit_step))
    if price <= ZERO or step <= ZERO or budget < price * step:
        return ZERO
    units = (budget / price).to_integral_value(rounding=ROUND_FLOOR)
    steps = (units / step).to_integral_value(rounding=ROUND_FLOOR)
    return steps * step


def is_whitelisted(symbol: str, whitelist: list[str] | tuple[str, ...]) -> bool:
    return str(symbol).strip() in {str(s).strip() for s in whitelist}
