from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.contracts import CommonSnapshot, FundSnapshot
from app.valuation_engine import AssetMix, SharedValuationEngine, ValuationConfig
from app.units import pct_points_to_fraction


class StaticMixProvider:
    def latest_for_date(self, trade_date):
        return {
            1: AssetMix(
                composition_id=11,
                fund_id=1,
                as_of_date=trade_date,
                bullion_weight=Decimal("0.8"),
                coin_weight=Decimal("0.2"),
            )
        }


class ValuationEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = SharedValuationEngine(
            ValuationConfig(
                troy_ounce_grams=Decimal("31.1034768"),
                bullion_certificate_grams=Decimal("0.1"),
                bullion_fineness=Decimal("0.995"),
                coin_pure_gold_grams=Decimal("7.3197"),
                threshold_by_symbol={
                    "عیار": (
                        pct_points_to_fraction("-1.10"),
                        pct_points_to_fraction("2.80"),
                    )
                },
            ),
            composition_provider=StaticMixProvider(),
        )
        self.now = datetime(2026, 8, 15, 12, 5, tzinfo=ZoneInfo("Asia/Tehran"))

    def common(self):
        # Pure gold = 100 IRR/g exactly.
        return CommonSnapshot(
            collected_at=self.now,
            usd_irr=Decimal("100"),
            ounce_usd=Decimal("31.1034768"),
            ime_bullion_price=Decimal("9.95"),
            ime_coin_price=Decimal("731.97"),
            bullion_bubble=None,
            coin_bubble=None,
            valuation_inputs_usable=True,
        )

    def fund(self, ask="99"):
        return FundSnapshot(
            fund_id=1,
            symbol="عیار",
            close_price=Decimal("77"),  # must never be used
            nav_redemption=Decimal("100"),
            best_bid=Decimal("98.9"),
            best_ask=Decimal(ask),
            trade_value=Decimal("1000000"),
            trade_count=10,
            data_valid=True,
            signal_price=Decimal(ask),
        )

    def test_exact_formula_and_ask_only_nominal(self):
        batch = self.engine.calculate(
            self.common(), {1: self.fund("99")}, date(2026, 8, 15)
        )
        row = batch.funds[1]
        self.assertTrue(row.valid)
        self.assertEqual(batch.common.pure_gold_irr_per_gram, Decimal("100"))
        self.assertEqual(batch.common.bullion_bubble, Decimal("0"))
        self.assertEqual(batch.common.coin_bubble, Decimal("0"))
        self.assertEqual(row.intrinsic_bubble, Decimal("0"))
        self.assertEqual(row.nominal_bubble, Decimal("-0.01"))
        self.assertEqual(row.total_bubble, Decimal("-0.01"))
        self.assertEqual(row.asset_composition_id, 11)

    def test_signal_price_must_equal_ask(self):
        fund = self.fund("99")
        fund = FundSnapshot(**{**fund.__dict__, "signal_price": Decimal("98")})
        batch = self.engine.calculate(
            self.common(), {1: fund}, date(2026, 8, 15)
        )
        self.assertFalse(batch.funds[1].valid)

    def test_missing_common_input_fails_all_closed(self):
        common = self.common()
        common = CommonSnapshot(**{**common.__dict__, "ime_coin_price": None})
        batch = self.engine.calculate(common, {1: self.fund()}, date(2026, 8, 15))
        self.assertFalse(batch.common.valuation_inputs_usable)
        self.assertFalse(batch.funds[1].valid)


if __name__ == "__main__":
    unittest.main()
