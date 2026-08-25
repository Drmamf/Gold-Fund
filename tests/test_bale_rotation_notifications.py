from __future__ import annotations

import unittest
from datetime import date

from app.notifications.service import BaleNotificationCoordinator


class StrategyBRotationNotificationStateTest(unittest.TestCase):
    def setUp(self):
        self.market_date = date(2026, 8, 25)
        self.key = "25:8:3"

    def test_continuing_opportunity_stays_suppressed(self):
        retained, state = (
            BaleNotificationCoordinator._rotation_notification_transition(
                previous_state={
                    "market_date": self.market_date.isoformat(),
                    "active_keys": [self.key],
                    "notified_keys": [self.key],
                },
                market_date=self.market_date,
                current_keys={self.key},
            )
        )
        self.assertEqual(retained, {self.key})
        self.assertEqual(state["notified_keys"], [self.key])

    def test_disappearance_rearms_same_opportunity(self):
        retained, cleared = (
            BaleNotificationCoordinator._rotation_notification_transition(
                previous_state={
                    "market_date": self.market_date.isoformat(),
                    "active_keys": [self.key],
                    "notified_keys": [self.key],
                },
                market_date=self.market_date,
                current_keys=set(),
            )
        )
        self.assertEqual(retained, set())
        self.assertEqual(cleared["notified_keys"], [])

        retained_after_return, _ = (
            BaleNotificationCoordinator._rotation_notification_transition(
                previous_state=cleared,
                market_date=self.market_date,
                current_keys={self.key},
            )
        )
        self.assertEqual(retained_after_return, set())

    def test_target_change_is_a_new_opportunity(self):
        new_key = "25:8:4"
        retained, _ = (
            BaleNotificationCoordinator._rotation_notification_transition(
                previous_state={
                    "market_date": self.market_date.isoformat(),
                    "active_keys": [self.key],
                    "notified_keys": [self.key],
                },
                market_date=self.market_date,
                current_keys={new_key},
            )
        )
        self.assertEqual(retained, set())

    def test_new_trading_day_rearms_notifications(self):
        retained, _ = (
            BaleNotificationCoordinator._rotation_notification_transition(
                previous_state={
                    "market_date": "2026-08-24",
                    "active_keys": [self.key],
                    "notified_keys": [self.key],
                },
                market_date=self.market_date,
                current_keys={self.key},
            )
        )
        self.assertEqual(retained, set())


if __name__ == "__main__":
    unittest.main()
