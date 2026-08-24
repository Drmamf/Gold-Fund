from __future__ import annotations

import unittest
from decimal import Decimal

from app.config_loader import load_project_config
from app.providers.tsetmc_adapter import (
    TSETMCFundRawSnapshot,
    TSETMCOrderBook,
    TSETMCNav,
    TSETMCPriceActivity,
)
from datetime import datetime
from zoneinfo import ZoneInfo


class StrictPricingPolicyTest(unittest.TestCase):
    def test_project_config_is_strict(self):
        cfg = load_project_config(".")
        policy = cfg.market["market_data_policy"]
        self.assertEqual(
            policy["fund_valuation_price_source"], "BEST_ASK_ONLY"
        )
        self.assertEqual(
            policy["ime_valuation_price_source"], "BEST_ASK_ONLY"
        )
        self.assertFalse(policy["allow_price_fallback"])
        self.assertEqual(
            policy["nav_source"], "TSETMC_REDEMPTION_ONLY"
        )

    def test_signal_price_is_exact_best_ask(self):
        cfg = load_project_config(".")
        inst = cfg.instrument_by_symbol["عیار"]
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        raw = TSETMCFundRawSnapshot(
            instrument=inst,
            fetched_at=now,
            price=TSETMCPriceActivity(
                last_price=Decimal("90"),
                close_price=Decimal("91"),
                trade_value=Decimal("1000000"),
                trade_volume=Decimal("100"),
                trade_count=10,
                update_time=now,
                raw={},
            ),
            nav=TSETMCNav(
                nav_redemption=Decimal("100"),
                nav_issuance=None,
                update_time=now,
                raw={},
            ),
            order_book=TSETMCOrderBook(
                best_bid=Decimal("98"),
                best_bid_volume=Decimal("10"),
                best_bid_count=1,
                best_ask=Decimal("99"),
                best_ask_volume=Decimal("10"),
                best_ask_count=1,
                raw={},
            ),
            errors={},
        )
        self.assertEqual(raw.signal_price, Decimal("99"))
        self.assertNotEqual(raw.signal_price, Decimal("98.5"))
        self.assertNotEqual(raw.signal_price, Decimal("90"))
        self.assertTrue(raw.valuation_valid)


if __name__ == "__main__":
    unittest.main()
