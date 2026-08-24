from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

ZERO = Decimal("0")
ONE = Decimal("1")

@dataclass(frozen=True)
class BuyPlan:
    units: Decimal
    gross_value: Decimal
    buy_fee: Decimal
    cash_after: Decimal


@dataclass(frozen=True)
class RotationPlan:
    source_units: Decimal
    gross_sell_value: Decimal
    sell_fee: Decimal
    cash_after_sell: Decimal

    target_units: Decimal
    gross_buy_value: Decimal
    buy_fee: Decimal
    cash_after: Decimal

    realized_pnl: Decimal


class StrategyAExecutionMath:
    """Pure deterministic sizing math; intentionally independent of SQL."""

    @staticmethod
    def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        if value <= ZERO or step <= ZERO:
            return ZERO
        steps = (value / step).to_integral_value(rounding=ROUND_FLOOR)
        return steps * step

    @classmethod
    def plan_buy(
        cls,
        *,
        available_cash: Decimal,
        ask_price: Decimal,
        buy_fee_rate: Decimal,
        unit_step: Decimal,
    ) -> BuyPlan:
        if available_cash <= ZERO:
            raise ValueError("available_cash must be positive.")
        if ask_price <= ZERO:
            raise ValueError("ask_price must be positive.")

        all_in_unit_cost = ask_price * (ONE + buy_fee_rate)
        units = cls.floor_to_step(available_cash / all_in_unit_cost, unit_step)
        if units <= ZERO:
            raise ValueError("INSUFFICIENT_CASH_FOR_ONE_UNIT")

        gross = units * ask_price
        fee = gross * buy_fee_rate
        cash_after = available_cash - gross - fee
        if cash_after < ZERO:
            raise ArithmeticError("Buy sizing produced negative cash.")

        return BuyPlan(
            units=units,
            gross_value=gross,
            buy_fee=fee,
            cash_after=cash_after,
        )

    @classmethod
    def plan_rotation(
        cls,
        *,
        source_units: Decimal,
        source_bid: Decimal,
        source_cost_basis: Decimal,
        starting_cash: Decimal,
        target_ask: Decimal,
        sell_fee_rate: Decimal,
        buy_fee_rate: Decimal,
        unit_step: Decimal,
    ) -> RotationPlan:
        if source_units <= ZERO:
            raise ValueError("source_units must be positive.")
        if source_bid <= ZERO or target_ask <= ZERO:
            raise ValueError("Execution prices must be positive.")
        if starting_cash < ZERO:
            raise ValueError("starting_cash cannot be negative.")

        gross_sell = source_units * source_bid
        sell_fee = gross_sell * sell_fee_rate
        net_sell = gross_sell - sell_fee
        cash_after_sell = starting_cash + net_sell

        buy = cls.plan_buy(
            available_cash=cash_after_sell,
            ask_price=target_ask,
            buy_fee_rate=buy_fee_rate,
            unit_step=unit_step,
        )

        realized_pnl = net_sell - source_cost_basis

        return RotationPlan(
            source_units=source_units,
            gross_sell_value=gross_sell,
            sell_fee=sell_fee,
            cash_after_sell=cash_after_sell,
            target_units=buy.units,
            gross_buy_value=buy.gross_value,
            buy_fee=buy.buy_fee,
            cash_after=buy.cash_after,
            realized_pnl=realized_pnl,
        )
