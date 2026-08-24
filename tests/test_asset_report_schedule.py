from __future__ import annotations

import unittest
from datetime import date

from app.asset_report_monitor import AssetCompositionReportMonitor
from app.jalali_utils import gregorian_to_jalali, jalali_to_gregorian


class AssetReportScheduleTest(unittest.TestCase):
    def setUp(self):
        self.monitor = AssetCompositionReportMonitor(
            schedule_path="config/fund_asset_composition_report_schedule.yaml",
            notifications=None,
            session_factory=lambda: None,
        )

    def test_jalali_round_trip_current_project_date(self):
        self.assertEqual(gregorian_to_jalali(date(2026, 8, 14)), (1405, 5, 23))
        self.assertEqual(jalali_to_gregorian(1405, 5, 23), date(2026, 8, 14))

    def test_month_end_rule_current_due_period(self):
        period_g, period_j, start_g = self.monitor._latest_due_period(
            date(2026, 8, 14), "jalali_month_end"
        )
        self.assertEqual(period_j, "1405/04/31")
        self.assertEqual(period_g, date(2026, 7, 22))
        self.assertEqual(start_g, date(2026, 7, 27))

    def test_zar_day14_rule_current_due_period(self):
        period_g, period_j, start_g = self.monitor._latest_due_period(
            date(2026, 8, 14), "jalali_day_of_month:14"
        )
        self.assertEqual(period_j, "1405/05/14")
        self.assertEqual(start_g, date(2026, 8, 10))


if __name__ == "__main__":
    unittest.main()
