from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


ZERO = Decimal("0")
ONE = Decimal("1")


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    value = _d(value)
    step = _d(step)
    if step <= ZERO:
        raise ValueError("unit_step must be positive")
    if value <= ZERO:
        return ZERO
    n = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return n * step


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    value = _d(value)
    step = _d(step)
    if step <= ZERO:
        raise ValueError("unit_step must be positive")
    if value <= ZERO:
        return ZERO
    n = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return n * step


@dataclass(frozen=True)
class BuyPlan:
    budget: Decimal
    units: Decimal
    price: Decimal
    gross_value: Decimal
    buy_fee: Decimal
    cash_outflow: Decimal
    unused_budget: Decimal


@dataclass(frozen=True)
class ExitPlan:
    units: Decimal
    price: Decimal
    gross_value: Decimal
    sell_fee: Decimal
    net_proceeds: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class RotationPlan:
    source_units: Decimal
    source_bid: Decimal
    gross_sell_value: Decimal
    sell_fee: Decimal
    net_sell_proceeds: Decimal
    realized_pnl: Decimal

    target_units: Decimal
    target_ask: Decimal
    gross_buy_value: Decimal
    buy_fee: Decimal
    cash_used_for_target: Decimal
    cash_remainder: Decimal


@dataclass(frozen=True)
class FixedIncomeBuyPlan:
    budget: Decimal
    units: Decimal
    price: Decimal
    gross_value: Decimal
    buy_fee: Decimal
    cash_outflow: Decimal
    unused_budget: Decimal


@dataclass(frozen=True)
class FixedIncomeSellPlan:
    units_sold: Decimal
    price: Decimal
    gross_value: Decimal
    sell_fee: Decimal
    net_proceeds: Decimal
    cost_basis_removed: Decimal
    realized_pnl: Decimal
    units_after: Decimal
    cost_basis_after: Decimal


class StrategyBExecutionMath:
    """Pure Decimal math for Strategy B paper execution."""

    @staticmethod
    def plan_budgeted_buy(
        *,
        total_cash_budget: Decimal,
        ask_price: Decimal,
        buy_fee_rate: Decimal,
        unit_step: Decimal = Decimal("1"),
    ) -> BuyPlan:
        """
        `total_cash_budget` is the ALL-IN account outflow target.

        Example: if Entry #1 is 10% of portfolio, the maximum cash outflow
        including the buy fee is exactly that 10% budget (subject to unit
        rounding). This is slightly more exact than the legacy approximation.
        """
        budget = _d(total_cash_budget)
        ask = _d(ask_price)
        fee = _d(buy_fee_rate)
        step = _d(unit_step)

        if budget <= ZERO:
            raise ValueError("total_cash_budget must be positive")
        if ask <= ZERO:
            raise ValueError("ask_price must be positive")
        if not (ZERO <= fee < ONE):
            raise ValueError("buy_fee_rate must be in [0, 1)")

        max_units = budget / (ask * (ONE + fee))
        units = _floor_to_step(max_units, step)
        if units <= ZERO:
            raise ValueError("budget is too small to buy one unit_step")

        gross = units * ask
        buy_fee = gross * fee
        outflow = gross + buy_fee
        unused = max(ZERO, budget - outflow)

        return BuyPlan(
            budget=budget,
            units=units,
            price=ask,
            gross_value=gross,
            buy_fee=buy_fee,
            cash_outflow=outflow,
            unused_budget=unused,
        )

    @staticmethod
    def plan_full_exit(
        *,
        units: Decimal,
        bid_price: Decimal,
        sell_fee_rate: Decimal,
        cost_basis: Decimal,
    ) -> ExitPlan:
        u = _d(units)
        bid = _d(bid_price)
        fee = _d(sell_fee_rate)
        basis = _d(cost_basis)

        if u <= ZERO:
            raise ValueError("units must be positive")
        if bid <= ZERO:
            raise ValueError("bid_price must be positive")
        if not (ZERO <= fee < ONE):
            raise ValueError("sell_fee_rate must be in [0, 1)")

        gross = u * bid
        sell_fee = gross * fee
        net = gross - sell_fee
        return ExitPlan(
            units=u,
            price=bid,
            gross_value=gross,
            sell_fee=sell_fee,
            net_proceeds=net,
            realized_pnl=net - basis,
        )

    @staticmethod
    def plan_full_rotation(
        *,
        source_units: Decimal,
        source_bid: Decimal,
        source_cost_basis: Decimal,
        target_ask: Decimal,
        sell_fee_rate: Decimal,
        buy_fee_rate: Decimal,
        unit_step: Decimal = Decimal("1"),
    ) -> RotationPlan:
        """
        Rotate ONLY the source tranche's own proceeds.

        Global account cash/Afran is deliberately excluded so a 10% tranche
        cannot silently become larger during a relative rotation.
        """
        exit_plan = StrategyBExecutionMath.plan_full_exit(
            units=source_units,
            bid_price=source_bid,
            sell_fee_rate=sell_fee_rate,
            cost_basis=source_cost_basis,
        )
        buy_plan = StrategyBExecutionMath.plan_budgeted_buy(
            total_cash_budget=exit_plan.net_proceeds,
            ask_price=target_ask,
            buy_fee_rate=buy_fee_rate,
            unit_step=unit_step,
        )

        return RotationPlan(
            source_units=exit_plan.units,
            source_bid=exit_plan.price,
            gross_sell_value=exit_plan.gross_value,
            sell_fee=exit_plan.sell_fee,
            net_sell_proceeds=exit_plan.net_proceeds,
            realized_pnl=exit_plan.realized_pnl,
            target_units=buy_plan.units,
            target_ask=buy_plan.price,
            gross_buy_value=buy_plan.gross_value,
            buy_fee=buy_plan.buy_fee,
            cash_used_for_target=buy_plan.cash_outflow,
            cash_remainder=exit_plan.net_proceeds - buy_plan.cash_outflow,
        )

    @staticmethod
    def plan_fixed_income_buy(
        *,
        total_cash_budget: Decimal,
        ask_price: Decimal,
        buy_fee_rate: Decimal,
        unit_step: Decimal = Decimal("1"),
    ) -> FixedIncomeBuyPlan:
        plan = StrategyBExecutionMath.plan_budgeted_buy(
            total_cash_budget=total_cash_budget,
            ask_price=ask_price,
            buy_fee_rate=buy_fee_rate,
            unit_step=unit_step,
        )
        return FixedIncomeBuyPlan(
            budget=plan.budget,
            units=plan.units,
            price=plan.price,
            gross_value=plan.gross_value,
            buy_fee=plan.buy_fee,
            cash_outflow=plan.cash_outflow,
            unused_budget=plan.unused_budget,
        )

    @staticmethod
    def plan_fixed_income_sell_for_net_cash(
        *,
        current_units: Decimal,
        current_cost_basis: Decimal,
        desired_net_cash: Decimal,
        bid_price: Decimal,
        sell_fee_rate: Decimal,
        unit_step: Decimal = Decimal("1"),
    ) -> FixedIncomeSellPlan:
        units = _d(current_units)
        basis = _d(current_cost_basis)
        desired = _d(desired_net_cash)
        bid = _d(bid_price)
        fee = _d(sell_fee_rate)
        step = _d(unit_step)

        if units <= ZERO:
            raise ValueError("current_units must be positive")
        if desired <= ZERO:
            raise ValueError("desired_net_cash must be positive")
        if bid <= ZERO:
            raise ValueError("bid_price must be positive")
        if not (ZERO <= fee < ONE):
            raise ValueError("sell_fee_rate must be in [0, 1)")

        net_per_unit = bid * (ONE - fee)
        required_units = desired / net_per_unit
        units_to_sell = min(units, _ceil_to_step(required_units, step))
        if units_to_sell <= ZERO:
            raise ValueError("computed units_to_sell is zero")

        gross = units_to_sell * bid
        sell_fee = gross * fee
        net = gross - sell_fee

        ratio = units_to_sell / units
        removed_basis = basis * ratio
        units_after = units - units_to_sell
        basis_after = max(ZERO, basis - removed_basis)

        return FixedIncomeSellPlan(
            units_sold=units_to_sell,
            price=bid,
            gross_value=gross,
            sell_fee=sell_fee,
            net_proceeds=net,
            cost_basis_removed=removed_basis,
            realized_pnl=net - removed_basis,
            units_after=units_after,
            cost_basis_after=basis_after,
        )

    @staticmethod
    def fits_entry_caps(
        *,
        portfolio_value: Decimal,
        total_gold_exposure: Decimal,
        target_fund_exposure: Decimal,
        all_in_entry_budget: Decimal,
        max_total_gold_fraction: Decimal,
        max_per_fund_fraction: Decimal,
    ) -> bool:
        portfolio = _d(portfolio_value)
        gold = _d(total_gold_exposure)
        fund = _d(target_fund_exposure)
        budget = _d(all_in_entry_budget)
        total_cap = _d(max_total_gold_fraction)
        fund_cap = _d(max_per_fund_fraction)

        if portfolio <= ZERO or budget <= ZERO:
            return False
        return (
            gold + budget <= portfolio * total_cap
            and fund + budget <= portfolio * fund_cap
        )

    @staticmethod
    def fits_rotation_fund_cap(
        *,
        portfolio_value: Decimal,
        target_fund_exposure_before: Decimal,
        projected_target_market_value: Decimal,
        max_per_fund_fraction: Decimal,
    ) -> bool:
        portfolio = _d(portfolio_value)
        before = _d(target_fund_exposure_before)
        projected = _d(projected_target_market_value)
        cap = _d(max_per_fund_fraction)
        if portfolio <= ZERO or projected < ZERO:
            return False
        return before + projected <= portfolio * cap
