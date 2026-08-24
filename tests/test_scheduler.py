from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.scheduler import MarketSchedule


class MarketScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule = MarketSchedule.from_yaml("config/app.yaml")
        cls.tz = ZoneInfo("Asia/Tehran")

    def test_friday_has_no_market_events(self):
        events = self.schedule.events_for_day(date(2026, 8, 14))
        self.assertEqual(events, [])

    def test_saturday_schedule(self):
        events = self.schedule.events_for_day(date(2026, 8, 15))
        self.assertEqual(events[0].phase, "OPEN_STATUS")
        self.assertEqual(events[0].scheduled_for.strftime("%H:%M"), "12:00")
        self.assertEqual(events[1].phase, "WARMUP")
        self.assertEqual(events[1].scheduled_for.strftime("%H:%M"), "12:03")

        active = [e for e in events if e.phase == "ACTIVE"]
        self.assertEqual(active[0].scheduled_for.strftime("%H:%M"), "12:05")
        self.assertEqual(active[1].scheduled_for.strftime("%H:%M"), "12:08")
        self.assertEqual(active[-1].scheduled_for.strftime("%H:%M"), "17:59")

        self.assertEqual(
            [e for e in events if e.phase == "CLOSE"][0]
            .scheduled_for.strftime("%H:%M"),
            "18:00"
        )

    def test_wednesday_has_1830_backup(self):
        # 2026-08-19 is Wednesday.
        events = self.schedule.events_for_day(date(2026, 8, 19))
        backups = [e for e in events if e.phase == "WEEKLY_BACKUP"]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].scheduled_for.strftime("%H:%M"), "18:30")

    def test_no_active_at_or_after_1800(self):
        events = self.schedule.events_for_day(date(2026, 8, 15))
        for event in events:
            if event.phase == "ACTIVE":
                self.assertLess(event.scheduled_for.time(), self.schedule.active_end)


if __name__ == "__main__":
    unittest.main()
