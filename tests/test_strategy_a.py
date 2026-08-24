from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
)
from app.execution.strategy_a_math import StrategyAExecutionMath
from app.strategies.strategy_a_relative_buy_hold import RelativeBuyHoldStrategy


D = Decimal


def common():
    return CommonSnapshot(
        collected_at=datetime.now(timezone.utc),
        usd_irr=D("1"),
        ounce_usd=D("1"),
        ime_bullion_price=D("1"),
        ime_coin_price=D("1"),
        bullion_bubble=D("0"),
        coin_bubble=D("0"),
        valuation_inputs_usable=True,
    )


def fund(fid, symbol, bid, ask):
    return FundSnapshot(
        fund_id=fid,
        symbol=symbol,
        close_price=(D(str(bid)) + D(str(ask))) / D("2"),
        nav_redemption=D("100"),
        best_bid=D(str(bid)),
        best_ask=D(str(ask)),
        trade_value=D("1000000"),
        trade_count=100,
        data_valid=True,
        signal_price=(D(str(bid)) + D(str(ask))) / D("2"),
    )


def val(fid):
    return FundValuation(
        fund_id=fid,
        nominal_bubble=D("0"),
        intrinsic_bubble=D("0"),
        total_bubble=D("0"),
        buy_threshold=None,
        sell_threshold=None,
        valid=True,
    )


def rel(source_id, target_id, net_edge):
    return RelativeValueRow(
        fund_id=source_id,
        anchor_fund_id=1,
        current_gap=D("0"),
        historical_normal_gap=D("0"),
        relative_score=D("0"),
        rank=2,
        best_target_fund_id=target_id,
        gross_rotation_edge=D("0.01"),
        spread_cost=D("0.001"),
        fee_cost=D("0.0025"),
        net_executable_edge=D(str(net_edge)),
        executable=True,
    )


class StrategyASignalTests(unittest.TestCase):
    def setUp(self):
        self.strategy = RelativeBuyHoldStrategy("0.50")
        self.funds = {1: fund(1, "عیار", 99, 101), 2: fund(2, "لیان", 199, 201)}
        self.vals = {1: val(1), 2: val(2)}
        self.state = {"current_fund_id": 1}

    def test_049pp_holds(self):
        rows = {1: rel(1, 2, "0.0049")}
        signals = self.strategy.generate_signals(
            common=common(),
            funds=self.funds,
            valuations=self.vals,
            relative_rows=rows,
            runtime_state=self.state,
        )
        self.assertEqual(signals, [])

    def test_050pp_rotates(self):
        rows = {1: rel(1, 2, "0.0050")}
        signals = self.strategy.generate_signals(
            common=common(),
            funds=self.funds,
            valuations=self.vals,
            relative_rows=rows,
            runtime_state=self.state,
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].source_fund_id, 1)
        self.assertEqual(signals[0].target_fund_id, 2)
        self.assertEqual(signals[0].signal_type, "ROTATE_TO")

    def test_no_position_means_bootstrap_not_signal(self):
        signals = self.strategy.generate_signals(
            common=common(),
            funds=self.funds,
            valuations=self.vals,
            relative_rows={1: rel(1, 2, "0.02")},
            runtime_state={},
        )
        self.assertEqual(signals, [])


class StrategyAExecutionMathTests(unittest.TestCase):
    def test_initial_buy_keeps_nonnegative_cash(self):
        plan = StrategyAExecutionMath.plan_buy(
            available_cash=D("1000000000"),
            ask_price=D("100000"),
            buy_fee_rate=D("0.00125"),
            unit_step=D("1"),
        )
        self.assertGreater(plan.units, 0)
        self.assertGreaterEqual(plan.cash_after, 0)
        self.assertLess(plan.cash_after, D("100000") * D("1.00125"))

    def test_rotation_sells_all_source_and_buys_max_target(self):
        plan = StrategyAExecutionMath.plan_rotation(
            source_units=D("1000"),
            source_bid=D("110000"),
            source_cost_basis=D("100000000"),
            starting_cash=D("50000"),
            target_ask=D("55000"),
            sell_fee_rate=D("0.00125"),
            buy_fee_rate=D("0.00125"),
            unit_step=D("1"),
        )
        self.assertEqual(plan.source_units, D("1000"))
        self.assertGreater(plan.target_units, D("0"))
        self.assertGreaterEqual(plan.cash_after, D("0"))
        self.assertGreater(plan.realized_pnl, D("0"))


if __name__ == "__main__":
    unittest.main()
