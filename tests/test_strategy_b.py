from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.contracts import CommonSnapshot, FundSnapshot, FundValuation
from app.strategies.strategy_b_entry_state import StrategyBEntryState
from app.strategies.strategy_b_threshold_10_10 import Threshold1010RelativeStrategy


def common(day=14):
    return CommonSnapshot(
        collected_at=datetime(2026, 8, day, 12, 30, tzinfo=timezone.utc),
        usd_irr=Decimal("1"),
        ounce_usd=Decimal("1"),
        ime_bullion_price=Decimal("1"),
        ime_coin_price=Decimal("1"),
        bullion_bubble=Decimal("0"),
        coin_bubble=Decimal("0"),
        valuation_inputs_usable=True,
    )


def snap(fid, symbol, bubble_price=100, trade_value="1000000000"):
    p = Decimal(str(bubble_price))
    return FundSnapshot(
        fund_id=fid,
        symbol=symbol,
        close_price=p,
        nav_redemption=p,
        best_bid=p,
        best_ask=p,
        trade_value=Decimal(trade_value),
        trade_count=10,
        data_valid=True,
        signal_price=p,
    )


def val(fid, total, buy, sell):
    return FundValuation(
        fund_id=fid,
        nominal_bubble=Decimal(str(total)),
        intrinsic_bubble=Decimal("0"),
        total_bubble=Decimal(str(total)),
        buy_threshold=Decimal(str(buy)),
        sell_threshold=Decimal(str(sell)),
        valid=True,
    )


class StrategyBStateTest(unittest.TestCase):
    def test_first_entry_opens_ma7_fallback_and_closes_gate(self):
        st = StrategyBEntryState()
        st = st.after_executed_entry(
            fund_id=1,
            position_id=11,
            buy_threshold=Decimal("-0.011"),
            rearm_margin=Decimal("0.015"),
            route="THRESHOLD_REARM",
            executed_at="2026-08-14T12:00:00+00:00",
            market_date=datetime(2026, 8, 14).date(),
        )
        self.assertEqual(st.entry_count, 1)
        self.assertFalse(st.threshold_gate_open)
        self.assertEqual(st.rearm_threshold, Decimal("0.004"))
        self.assertTrue(st.ma7_fallback_eligible)

    def test_rearm_closes_ma7_and_allows_normal_entry2(self):
        st = StrategyBEntryState().after_executed_entry(
            fund_id=1,
            position_id=11,
            buy_threshold=Decimal("-0.011"),
            rearm_margin=Decimal("0.015"),
            route="THRESHOLD_REARM",
            executed_at="x",
            market_date=datetime(2026, 8, 13).date(),
        )
        st = st.preview_rearm(
            current_total_bubble=Decimal("0.004"),
            achieved_at="y",
        )
        self.assertTrue(st.threshold_gate_open)
        self.assertFalse(st.ma7_fallback_eligible)
        self.assertEqual(st.next_entry_number, 2)

    def test_ma7_only_allowed_for_entry2_before_rearm(self):
        st = StrategyBEntryState().after_executed_entry(
            fund_id=1,
            position_id=11,
            buy_threshold=Decimal("-0.011"),
            rearm_margin=Decimal("0.015"),
            route="THRESHOLD_REARM",
            executed_at="x",
            market_date=datetime(2026, 8, 13).date(),
        )
        self.assertTrue(st.ma7_fallback_allowed(datetime(2026, 8, 14).date()))
        st = st.after_executed_entry(
            fund_id=2,
            position_id=12,
            buy_threshold=Decimal("-0.018"),
            rearm_margin=Decimal("0.015"),
            route="MA7_FALLBACK",
            executed_at="z",
            market_date=datetime(2026, 8, 14).date(),
        )
        self.assertEqual(st.entry_count, 2)
        self.assertFalse(st.ma7_fallback_allowed(datetime(2026, 8, 15).date()))
        self.assertTrue(st.ma7_fallback_consumed)

    def test_entry3_and_entry4_are_normal_rearm_path(self):
        st = StrategyBEntryState(entry_count=2, threshold_gate_open=True)
        st = st.after_executed_entry(
            fund_id=3,
            position_id=13,
            buy_threshold=Decimal("-0.02"),
            rearm_margin=Decimal("0.015"),
            route="THRESHOLD_REARM",
            executed_at="x",
            market_date=datetime(2026, 8, 14).date(),
        )
        self.assertEqual(st.entry_count, 3)
        self.assertFalse(st.ma7_fallback_eligible)
        st = st.preview_rearm(current_total_bubble=Decimal("-0.005"), achieved_at="r")
        self.assertTrue(st.threshold_gate_open)
        self.assertEqual(st.next_entry_number, 4)


class StrategyBSignalTest(unittest.TestCase):
    def setUp(self):
        self.strategy = Threshold1010RelativeStrategy()
        self.funds = {
            1: snap(1, "عیار"),
            2: snap(2, "زر"),
        }

    def test_initial_threshold_entry(self):
        vals = {
            1: val(1, "-0.012", "-0.011", "0.028"),
            2: val(2, "-0.020", "-0.030", "0.017"),
        }
        sigs = self.strategy.generate_signals(
            common=common(), funds=self.funds, valuations=vals,
            relative_rows={}, runtime_state={"funds": {}, "open_positions": [], "entry_count": 0, "threshold_gate_open": True},
        )
        buys = [s for s in sigs if s.signal_type == "THRESHOLD_BUY"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].signal_stage, "ENTRY_1")

    def test_no_normal_entry_before_rearm_but_ma7_entry2_is_available(self):
        vals = {
            1: val(1, "-0.020", "-0.011", "0.028"),
            2: val(2, "-0.040", "-0.030", "0.017"),
        }
        state = {
            "entry_count": 1,
            "threshold_gate_open": False,
            "rearm_reference_fund_id": 1,
            "rearm_reference_buy_threshold": "-0.011",
            "rearm_threshold": "0.004",
            "last_entry_position_id": 11,
            "last_entry_fund_id": 1,
            "last_entry_number": 1,
            "last_entry_market_date": "2026-08-13",
            "ma7_fallback_eligible": True,
            "ma7_fallback_since_date": "2026-08-13",
            "ma7_primary_position_id": 11,
            "ma7_primary_fund_id": 1,
            "funds": {"1": {"buy_state": "LOCKED"}, "2": {"buy_state": "READY"}},
            "open_positions": [],
            "market_date": "2026-08-14",
            "ma7": {
                "previous_7d_average_trade_value": "1000000000",
                "history_days_available": 7,
            },
        }
        sigs = self.strategy.generate_signals(
            common=common(), funds=self.funds, valuations=vals,
            relative_rows={}, runtime_state=state,
        )
        self.assertFalse(any(s.signal_type == "THRESHOLD_BUY" for s in sigs))
        ma7 = [s for s in sigs if s.signal_type == "MA7_FALLBACK_BUY_2"]
        self.assertEqual(len(ma7), 1)
        self.assertEqual(ma7[0].signal_stage, "ENTRY_2")
        # lowest total bubble is Zar
        self.assertEqual(ma7[0].target_fund_id, 2)

    def test_rearm_disables_ma7_and_opens_normal_entry2(self):
        vals = {
            # Reference Ayyar has reached rearm threshold +0.40%
            1: val(1, "0.004", "-0.011", "0.028"),
            # Zar has a valid fresh threshold hit
            2: val(2, "-0.031", "-0.030", "0.017"),
        }
        state = {
            "entry_count": 1,
            "threshold_gate_open": False,
            "rearm_reference_fund_id": 1,
            "rearm_reference_buy_threshold": "-0.011",
            "rearm_threshold": "0.004",
            "ma7_fallback_eligible": True,
            "ma7_fallback_since_date": "2026-08-13",
            "ma7_primary_position_id": 11,
            "ma7_primary_fund_id": 1,
            "funds": {"1": {"buy_state": "LOCKED"}, "2": {"buy_state": "READY"}},
            "open_positions": [],
            "market_date": "2026-08-14",
            "ma7": {"previous_7d_average_trade_value": "1", "history_days_available": 7},
        }
        sigs = self.strategy.generate_signals(
            common=common(), funds=self.funds, valuations=vals,
            relative_rows={}, runtime_state=state,
        )
        buys = [s for s in sigs if s.signal_type == "THRESHOLD_BUY"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].signal_stage, "ENTRY_2")
        self.assertFalse(any(s.signal_type == "MA7_FALLBACK_BUY_2" for s in sigs))

    def test_entry3_label_after_two_executed_entries(self):
        vals = {
            1: val(1, "-0.012", "-0.011", "0.028"),
            2: val(2, "-0.020", "-0.030", "0.017"),
        }
        state = {
            "entry_count": 2,
            "threshold_gate_open": True,
            "funds": {"1": {"buy_state": "READY"}},
            "open_positions": [],
        }
        sigs = self.strategy.generate_signals(
            common=common(), funds=self.funds, valuations=vals,
            relative_rows={}, runtime_state=state,
        )
        buys = [s for s in sigs if s.signal_type == "THRESHOLD_BUY"]
        self.assertEqual(buys[0].signal_stage, "ENTRY_3")
        self.assertFalse(any(s.signal_type == "MA7_FALLBACK_BUY_2" for s in sigs))


if __name__ == "__main__":
    unittest.main()
