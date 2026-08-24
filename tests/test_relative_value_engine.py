from __future__ import annotations

import unittest
from decimal import Decimal

from app.contracts import FundSnapshot, FundValuation
from app.relative_value_engine import (
    RelativeValueConfig,
    SharedRelativeValueEngine,
)
from app.units import pct_points_to_fraction


def snap(fid, symbol, price):
    p = Decimal(str(price))
    return FundSnapshot(
        fund_id=fid,
        symbol=symbol,
        close_price=p,
        nav_redemption=p,
        best_bid=p * Decimal("0.9995"),
        best_ask=p * Decimal("1.0005"),
        trade_value=Decimal("1000000000"),
        trade_count=100,
        data_valid=True,
        signal_price=p * Decimal("1.0005"),
    )


def valuation(fid, total_bubble):
    return FundValuation(
        fund_id=fid,
        nominal_bubble=None,
        intrinsic_bubble=None,
        total_bubble=Decimal(str(total_bubble)),
        buy_threshold=None,
        sell_threshold=None,
        valid=True,
    )


class RelativeValueEngineTest(unittest.TestCase):
    def setUp(self):
        # Baselines in fraction form.
        self.cfg = RelativeValueConfig(
            anchor_symbol="عیار",
            normal_gap_by_symbol={
                "عیار": pct_points_to_fraction("0"),
                "زر": pct_points_to_fraction("2.131982"),
                "آلتون": pct_points_to_fraction("1.102191"),
                "لیان": pct_points_to_fraction("1.362867"),
            },
            sell_fee_rate=Decimal("0.00125"),
            buy_fee_rate=Decimal("0.00125"),
        )
        self.engine = SharedRelativeValueEngine(self.cfg)

        self.funds = {
            1: snap(1, "عیار", 100),
            2: snap(2, "زر", 80),
            3: snap(3, "آلتون", 60),
            4: snap(4, "لیان", 40),
        }

        # Construct desired relative scores:
        # Ayyar = 0.00pp
        # Zar   = -0.40pp
        # Alton = +0.50pp
        # Lian  = +1.20pp
        #
        # score_i = (AyyarTotal - FundTotal) - baseline_i
        anchor_total = Decimal("0.01")

        def fund_total(baseline_pp, score_pp):
            gap = (
                pct_points_to_fraction(baseline_pp)
                + pct_points_to_fraction(score_pp)
            )
            return anchor_total - gap

        self.vals = {
            1: valuation(1, anchor_total),
            2: valuation(2, fund_total("2.131982", "-0.40")),
            3: valuation(3, fund_total("1.102191", "0.50")),
            4: valuation(4, fund_total("1.362867", "1.20")),
        }

    def test_scores_and_rank(self):
        rows = self.engine.calculate(None, self.funds, self.vals)

        self.assertEqual(rows[1].relative_score, Decimal("0"))
        self.assertAlmostEqual(float(rows[2].relative_score), -0.004, places=10)
        self.assertAlmostEqual(float(rows[3].relative_score), 0.005, places=10)
        self.assertAlmostEqual(float(rows[4].relative_score), 0.012, places=10)

        self.assertEqual(rows[4].rank, 1)  # Lian cheapest
        self.assertEqual(rows[3].rank, 2)
        self.assertEqual(rows[1].rank, 3)
        self.assertEqual(rows[2].rank, 4)

    def test_zar_rotates_directly_to_lian(self):
        rows = self.engine.calculate(None, self.funds, self.vals)
        self.assertEqual(rows[2].best_target_fund_id, 4)
        self.assertTrue(rows[2].executable)
        self.assertGreater(rows[2].net_executable_edge, Decimal("0"))

    def test_lian_holds(self):
        rows = self.engine.calculate(None, self.funds, self.vals)
        self.assertIsNone(rows[4].best_target_fund_id)
        self.assertFalse(rows[4].executable)

    def test_ayyar_can_rotate_to_non_anchor(self):
        rows = self.engine.calculate(None, self.funds, self.vals)
        self.assertEqual(rows[1].best_target_fund_id, 4)
        self.assertGreater(rows[1].net_executable_edge, Decimal("0"))

    def test_bad_orderbook_fails_closed(self):
        bad_funds = dict(self.funds)
        bad_funds[2] = FundSnapshot(
            fund_id=2,
            symbol="زر",
            close_price=Decimal("80"),
            nav_redemption=Decimal("80"),
            best_bid=Decimal("0"),
            best_ask=Decimal("80"),
            trade_value=Decimal("1000"),
            trade_count=10,
            data_valid=True,
            signal_price=Decimal("80"),
        )
        rows = self.engine.calculate(None, bad_funds, self.vals)
        self.assertFalse(rows[2].executable)


if __name__ == "__main__":
    unittest.main()
