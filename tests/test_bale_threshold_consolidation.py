from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.contracts import FundSnapshot, StrategySignal
from app.notifications.service import BaleNotificationCoordinator, STRATEGY_B


class DummyClientConfig:
    chat_id = "test"


class DummyClient:
    config = DummyClientConfig()


class CapturingCoordinator(BaleNotificationCoordinator):
    def __init__(self):
        # Do not need a real DB/client for this unit test.
        self.tz = ZoneInfo("Asia/Tehran")
        self.client = DummyClient()
        self.sent = []

    def send_text(self, text: str, **kwargs):
        self.sent.append((text, kwargs))
        return True


def fund(fid: int, symbol: str) -> FundSnapshot:
    return FundSnapshot(
        fund_id=fid,
        symbol=symbol,
        close_price=Decimal("100"),
        nav_redemption=Decimal("100"),
        best_bid=Decimal("99"),
        best_ask=Decimal("100"),
        trade_value=Decimal("1000"),
        trade_count=1,
        data_valid=True,
        signal_price=Decimal("100"),
    )


class BaleThresholdConsolidationTest(unittest.TestCase):
    def test_only_lowest_bubble_threshold_candidate_is_notified(self):
        c = CapturingCoordinator()
        funds = {1: fund(1, "عیار"), 7: fund(7, "زر"), 10: fund(10, "زروان")}
        signals = [
            StrategySignal(
                strategy_id=STRATEGY_B,
                engine="THRESHOLD",
                signal_type="THRESHOLD_BUY",
                fund_id=1,
                signal_stage="ENTRY_1",
                total_bubble=Decimal("-0.0147"),
                payload={
                    "entry_route": "THRESHOLD_REARM",
                    "entry_number": 1,
                    "buy_threshold": Decimal("-0.011"),
                    "rearm_threshold": Decimal("0.004"),
                    "allocation_pct": 10,
                },
            ),
            StrategySignal(
                strategy_id=STRATEGY_B,
                engine="THRESHOLD",
                signal_type="THRESHOLD_BUY",
                fund_id=7,
                signal_stage="ENTRY_1",
                total_bubble=Decimal("-0.0353"),
                payload={
                    "entry_route": "THRESHOLD_REARM",
                    "entry_number": 1,
                    "buy_threshold": Decimal("-0.03"),
                    "rearm_threshold": Decimal("-0.015"),
                    "allocation_pct": 10,
                },
            ),
            StrategySignal(
                strategy_id=STRATEGY_B,
                engine="THRESHOLD",
                signal_type="THRESHOLD_BUY",
                fund_id=10,
                signal_stage="ENTRY_1",
                total_bubble=Decimal("-0.0294"),
                payload={
                    "entry_route": "THRESHOLD_REARM",
                    "entry_number": 1,
                    "buy_threshold": Decimal("-0.028"),
                    "rearm_threshold": Decimal("-0.013"),
                    "allocation_pct": 10,
                },
            ),
        ]

        c.notify_signals(
            cycle_id=1,
            signals=signals,
            funds=funds,
            at=datetime(2026, 8, 15, 13, 17, tzinfo=ZoneInfo("Asia/Tehran")),
        )

        self.assertEqual(len(c.sent), 1)
        text = c.sent[0][0]
        self.assertIn("زر", text)
        self.assertIn("3 صندوق", text)
        self.assertIn("ENTRY 1", text)
        self.assertIn("Threshold Rearm", text)
        self.assertNotIn("ENTRY_1", text)
        self.assertNotIn("THRESHOLD_REARM", text)


if __name__ == "__main__":
    unittest.main()
