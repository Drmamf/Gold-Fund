from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.contracts import CommonSnapshot, FundSnapshot
from app.pipeline import UnifiedTradingPipeline
from app.relative_value_engine import RelativeValueConfig, SharedRelativeValueEngine
from app.strategies.strategy_a_relative_buy_hold import RelativeBuyHoldStrategy
from app.strategies.strategy_b_threshold_10_10 import Threshold1010RelativeStrategy
from app.valuation_engine import AssetMix, SharedValuationEngine, ValuationConfig
from app.units import pct_points_to_fraction


SYMBOLS = [
    "عیار", "کهربا", "مثقال", "گوهر", "گنج",
    "آلتون", "زر", "لیان", "رز ترنج", "زروان",
]
BASELINES_PP = {
    "عیار": "0",
    "کهربا": "0.895296",
    "مثقال": "0.926440",
    "گوهر": "1.039804",
    "گنج": "1.306113",
    "آلتون": "1.102191",
    "زر": "2.131982",
    "لیان": "1.362867",
    "رز ترنج": "1.309263",
    "زروان": "1.264320",
}
THRESHOLDS = {
    "عیار": ("-1.10", "2.80"),
    "کهربا": ("-1.80", "2.10"),
    "مثقال": ("-1.75", "2.00"),
    "گوهر": ("-1.75", "2.00"),
    "گنج": ("-2.15", "2.80"),
    "آلتون": ("-2.00", "2.20"),
    "زر": ("-3.00", "1.70"),
    "لیان": ("-3.00", "1.65"),
    "رز ترنج": ("-2.85", "1.15"),
    "زروان": ("-2.80", "1.60"),
}


class StaticMixProvider:
    def latest_for_date(self, trade_date):
        return {
            fid: AssetMix(
                composition_id=100 + fid,
                fund_id=fid,
                as_of_date=trade_date,
                bullion_weight=Decimal("1"),
                coin_weight=Decimal("0"),
            )
            for fid in range(1, 11)
        }


class FakeCollector:
    def __init__(self, at):
        self.at = at
    def collect(self, *, cycle_id=None):
        common = CommonSnapshot(
            collected_at=self.at,
            usd_irr=Decimal("100"),
            ounce_usd=Decimal("31.1034768"),
            ime_bullion_price=Decimal("9.95"),
            ime_coin_price=Decimal("731.97"),
            bullion_bubble=None,
            coin_bubble=None,
            valuation_inputs_usable=True,
        )
        funds = {}
        for fid, symbol in enumerate(SYMBOLS, 1):
            ask = Decimal("100")
            if symbol == "عیار":
                ask = Decimal("99")
            if symbol == "زر":
                ask = Decimal("95")
            funds[fid] = FundSnapshot(
                fund_id=fid,
                symbol=symbol,
                close_price=Decimal("1"),
                nav_redemption=Decimal("100"),
                best_bid=ask - Decimal("0.10"),
                best_ask=ask,
                trade_value=Decimal("100000000"),
                trade_count=100,
                data_valid=True,
                signal_price=ask,
            )
        return common, funds


class FakeRepo:
    def __init__(self):
        self.signals = []
        self.valuations = None
        self.relative = None
        self.completed = False
    def start_cycle(self, **kwargs): return 42
    def store_raw_market(self, *args): pass
    def store_valuations(self, cycle_id, common, valuations):
        self.valuations = valuations
    def store_relative(self, cycle_id, relative_rows):
        self.relative = relative_rows
    def load_strategy_state(self, strategy_id, *, market_date):
        if strategy_id == "RELATIVE_BUY_HOLD":
            return {"current_fund_id": 1, "current_position_id": 1}
        return {
            "market_date": market_date.isoformat(),
            "funds": {},
            "open_positions": [],
            "entry_count": 0,
            "threshold_gate_open": True,
            "ma7_fallback_eligible": False,
            "ma7_fallback_consumed": False,
            "ma7": {"history_days_available": 0, "previous_7d_average_trade_value": None},
        }
    def store_signals(self, cycle_id, signals):
        base = len(self.signals)
        self.signals.extend(signals)
        return (
        list(range(base + 1, base + 1 + len(signals))),
        list(signals),
    )
    def complete_cycle(self, cycle_id): self.completed = True
    def fail_cycle(self, cycle_id, exc): raise AssertionError(exc)


class FakeExecutor:
    def __init__(self): self.calls = []
    def execute_strategy_signals(self, **kwargs): self.calls.append(kwargs)
    def mark_strategy_account(self, **kwargs): pass


class FakeDaily:
    def __init__(self): self.calls = 0
    def upsert_current_day(self, trade_date): self.calls += 1


class FakeNotifications:
    def __init__(self): self.signal_count = 0
    def notify_signals(self, **kwargs): self.signal_count += len(kwargs["signals"])


class EndToEndPipelineTest(unittest.TestCase):
    def test_active_cycle_connects_real_engines_and_both_strategies(self):
        at = datetime(2026, 8, 15, 12, 5, tzinfo=ZoneInfo("Asia/Tehran"))
        thresholds = {
            s: (pct_points_to_fraction(b), pct_points_to_fraction(x))
            for s, (b, x) in THRESHOLDS.items()
        }
        valuation = SharedValuationEngine(
            ValuationConfig(
                troy_ounce_grams=Decimal("31.1034768"),
                bullion_certificate_grams=Decimal("0.1"),
                bullion_fineness=Decimal("0.995"),
                coin_pure_gold_grams=Decimal("7.3197"),
                threshold_by_symbol=thresholds,
            ),
            composition_provider=StaticMixProvider(),
        )
        relative = SharedRelativeValueEngine(
            RelativeValueConfig(
                anchor_symbol="عیار",
                normal_gap_by_symbol={
                    s: pct_points_to_fraction(v) for s, v in BASELINES_PP.items()
                },
                sell_fee_rate=Decimal("0.00125"),
                buy_fee_rate=Decimal("0.00125"),
            )
        )
        a = RelativeBuyHoldStrategy(min_net_edge_pct_points=Decimal("0.50"))
        b = Threshold1010RelativeStrategy()
        repo = FakeRepo()
        executor = FakeExecutor()
        daily = FakeDaily()
        notes = FakeNotifications()

        pipe = UnifiedTradingPipeline(
            collector=FakeCollector(at),
            valuation_engine=valuation,
            relative_engine=relative,
            repository=repo,
            executor=executor,
            daily_aggregator=daily,
            strategies=[a, b],
            notifications=notes,
        )
        cycle_id = pipe.run_active_cycle(trade_date=at.date(), scheduled_for=at)

        self.assertEqual(cycle_id, 42)
        self.assertTrue(repo.completed)
        self.assertTrue(repo.valuations[7].valid)  # Zar
        self.assertLess(repo.valuations[7].total_bubble, pct_points_to_fraction("-3"))
        self.assertIsNotNone(repo.relative[1].best_target_fund_id)
        self.assertEqual(repo.relative[1].best_target_fund_id, 7)

        a_signals = [s for s in repo.signals if s.strategy_id == "RELATIVE_BUY_HOLD"]
        b_signals = [s for s in repo.signals if s.strategy_id == "THRESHOLD_10_10_RELATIVE"]
        self.assertEqual(len(a_signals), 1)
        self.assertEqual(a_signals[0].target_fund_id, 7)
        self.assertTrue(any(s.fund_id == 7 for s in b_signals))
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(notes.signal_count, len(repo.signals))
        self.assertEqual(daily.calls, 1)


if __name__ == "__main__":
    unittest.main()
