from __future__ import annotations

from decimal import Decimal
import unittest

from app.live.policy import FAIL_WORDS, OK_WORDS, notification_ok, order_only_queued, order_rejected
from app.live.sizing import (
    is_whitelisted,
    live_buy_budget_rial,
    meets_etf_min_notional,
    qty_for_budget,
    rotation_buy_budget_rial,
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

    def test_queued_core_toast_is_not_a_fill(self):
        toast = (
            "گوهر - ثبت سفارش در هسته\n"
            "سفارش با موفقیت ارسال شد.\n"
            "گوهر - در حال ارسال سفارش"
        )
        self.assertTrue(order_only_queued(toast))
        self.assertFalse(order_rejected(toast))

    def test_etf_min_notional_rejects_crumb_tickets(self):
        self.assertFalse(meets_etf_min_notional(qty=1, price_rial=200_000))
        self.assertTrue(meets_etf_min_notional(qty=5, price_rial=200_000))
        self.assertTrue(meets_etf_min_notional(qty=44, price_rial=1_126_501))

    def test_rotation_budget_does_not_spend_the_rest_of_the_account(self):
        sleeve = Decimal("50467956")
        budget = rotation_buy_budget_rial(
            buying_power_rial=117_935_544,
            sell_notional_rial=sleeve,
        )
        self.assertEqual(budget, sleeve)
        crumbs = rotation_buy_budget_rial(
            buying_power_rial=252_056,
            sell_notional_rial=sleeve,
        )
        self.assertEqual(crumbs, Decimal("252056"))
        self.assertFalse(
            meets_etf_min_notional(
                qty=qty_for_budget(budget_rial=crumbs, price_rial=202_455),
                price_rial=202_455,
            )
        )

    def test_min_value_toast_is_rejected(self):
        text = "حداقل ارزش سفارش برای صندوق‌های سرمایه‌گذاری قابل معامله 1,000,000 ریال است"
        ok, _ = notification_ok(text)
        self.assertFalse(ok)
        self.assertTrue(order_rejected(text))


if __name__ == "__main__":
    unittest.main()
