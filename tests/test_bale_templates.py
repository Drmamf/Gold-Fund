from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.contracts import FundSnapshot, StrategySignal
from app.notifications.templates import (
    api_error_card,
    signal_card,
)


def snap(fid, symbol):
    return FundSnapshot(
        fund_id=fid,
        symbol=symbol,
        close_price=Decimal("100"),
        nav_redemption=Decimal("100"),
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        trade_value=Decimal("1000"),
        trade_count=1,
        data_valid=True,
    )


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.at = datetime(
            2026, 8, 15, 13, 0,
            tzinfo=ZoneInfo("Asia/Tehran")
        )
        self.funds = {1: snap(1, "زر"), 2: snap(2, "لیان")}

    def test_strategy_a_visual_signal(self):
        signal = StrategySignal(
            strategy_id="RELATIVE_BUY_HOLD",
            engine="RELATIVE_VALUE",
            signal_type="ROTATE_TO",
            source_fund_id=1,
            target_fund_id=2,
            net_executable_edge=Decimal("0.007"),
            payload={"min_required_pct_points": 0.50},
        )
        text = signal_card(signal, self.funds, at=self.at)
        self.assertIn("🔵", text)
        self.assertIn("زر → لیان", text)
        self.assertIn("فقط Signal", text)

    def test_api_error_has_source(self):
        text = api_error_card(
            source="TGJU",
            operation="fetch_market_snapshot",
            error="timeout",
            occurred_at=self.at,
            endpoint="https://example.invalid",
        )
        self.assertIn("TGJU", text)
        self.assertIn("timeout", text)
        self.assertIn("⚠️", text)


if __name__ == "__main__":
    unittest.main()
