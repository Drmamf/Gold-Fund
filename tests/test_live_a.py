from __future__ import annotations

from decimal import Decimal
import unittest

from app.live.policy import FAIL_WORDS, OK_WORDS, notification_ok
from app.live.sizing import (
    is_whitelisted,
    live_buy_budget_rial,
    qty_for_budget,
    toman_to_rial,
)


WHITELIST = [
    "عیار",
    "کهربا",
    "مثقال",
    "گوهر",
    "گنج",
    "آلتون",
    "زر",
    "لیان",
    "رز ترنج",
    "زروان",
]


class LiveSizingTests(unittest.TestCase):
    def test_cap_is_fifty_million_toman(self):
        cap = toman_to_rial(50_000_000)
        self.assertEqual(cap, Decimal("500000000"))

    def test_budget_uses_min_of_power_and_cap(self):
        cap = toman_to_rial(50_000_000)
        self.assertEqual(
            live_buy_budget_rial(buying_power_rial=2_000_000_000, cap_rial=cap),
            cap,
        )
        self.assertEqual(
            live_buy_budget_rial(buying_power_rial=120_000_000, cap_rial=cap),
            Decimal("120000000"),
        )

    def test_qty_floors_to_whole_units(self):
        qty = qty_for_budget(budget_rial=500_000_000, price_rial=120_000)
        self.assertEqual(qty, Decimal("4166"))
        self.assertEqual(qty_for_budget(budget_rial=100, price_rial=120_000), Decimal("0"))

    def test_whitelist_is_the_ten_gold_funds_only(self):
        self.assertTrue(is_whitelisted("عیار", WHITELIST))
        self.assertTrue(is_whitelisted("رز ترنج", WHITELIST))
        self.assertFalse(is_whitelisted("آفران", WHITELIST))
        self.assertFalse(is_whitelisted("خودرو", WHITELIST))


class LiveNotificationTests(unittest.TestCase):
    def test_success_and_failure_words(self):
        ok, _ = notification_ok("سفارش با موفقیت ثبت شد")
        self.assertTrue(ok)
        bad, reason = notification_ok("خطا: قدرت خرید کافی نیست")
        self.assertFalse(bad)
        self.assertIn("کافی نیست", reason)
        missing, code = notification_ok(None)
        self.assertFalse(missing)
        self.assertEqual(code, "NO_BROKER_NOTIFICATION")
        self.assertTrue(any("موفق" in w for w in OK_WORDS))
        self.assertTrue(any("خطا" in w for w in FAIL_WORDS))


if __name__ == "__main__":
    unittest.main()
