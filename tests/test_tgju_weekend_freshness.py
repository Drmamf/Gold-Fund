from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.providers.tgju_adapter import TGJUAdapter, TEHRAN_TZ


class TGJUWeekendFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.adapter = TGJUAdapter(
            endpoint="https://example.invalid",
            ounce_max_age_seconds=900,
            ounce_closed_market_max_age_seconds=259200,
            ounce_carry_forward_weekdays=(5, 6),
        )

    def _row(self, source_dt):
        return {
            "ons": {
                "p": "2500.50",
                "ts": source_dt.strftime("%Y-%m-%d %H:%M:%S"),
            }
        }

    def test_saturday_old_friday_close_is_usable_carry_forward(self):
        fetched = datetime(2026, 8, 15, 12, 50, tzinfo=TEHRAN_TZ)
        source = fetched - timedelta(seconds=42782.1)
        quote = self.adapter._build_quote(
            current=self._row(source),
            key="ons",
            instrument="XAU_USD_OUNCE",
            unit="USD_PER_TROY_OUNCE",
            max_age_seconds=900,
            fetched_at_dt=fetched,
            closed_market_max_age_seconds=259200,
            carry_forward_weekdays=(5, 6),
        )
        self.assertTrue(quote.usable)
        self.assertIsNone(quote.error)
        self.assertEqual(
            quote.freshness_status,
            "MARKET_CLOSED_CARRY_FORWARD",
        )

    def test_monday_same_age_is_stale(self):
        fetched = datetime(2026, 8, 17, 12, 50, tzinfo=TEHRAN_TZ)
        source = fetched - timedelta(seconds=42782.1)
        quote = self.adapter._build_quote(
            current=self._row(source),
            key="ons",
            instrument="XAU_USD_OUNCE",
            unit="USD_PER_TROY_OUNCE",
            max_age_seconds=900,
            fetched_at_dt=fetched,
            closed_market_max_age_seconds=259200,
            carry_forward_weekdays=(5, 6),
        )
        self.assertFalse(quote.usable)
        self.assertIn("STALE_PRICE", quote.error or "")

    def test_fresh_weekend_quote_stays_live_fresh(self):
        fetched = datetime(2026, 8, 15, 12, 50, tzinfo=TEHRAN_TZ)
        source = fetched - timedelta(seconds=120)
        quote = self.adapter._build_quote(
            current=self._row(source),
            key="ons",
            instrument="XAU_USD_OUNCE",
            unit="USD_PER_TROY_OUNCE",
            max_age_seconds=900,
            fetched_at_dt=fetched,
            closed_market_max_age_seconds=259200,
            carry_forward_weekdays=(5, 6),
        )
        self.assertTrue(quote.usable)
        self.assertEqual(quote.freshness_status, "LIVE_FRESH")

    def test_weekend_older_than_72h_is_stale(self):
        fetched = datetime(2026, 8, 16, 12, 50, tzinfo=TEHRAN_TZ)
        source = fetched - timedelta(seconds=259201)
        quote = self.adapter._build_quote(
            current=self._row(source),
            key="ons",
            instrument="XAU_USD_OUNCE",
            unit="USD_PER_TROY_OUNCE",
            max_age_seconds=900,
            fetched_at_dt=fetched,
            closed_market_max_age_seconds=259200,
            carry_forward_weekdays=(5, 6),
        )
        self.assertFalse(quote.usable)
        self.assertIn("STALE_PRICE", quote.error or "")


if __name__ == "__main__":
    unittest.main()
