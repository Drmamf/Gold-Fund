from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.scheduler import MarketSchedule, TradingScheduler


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

    def test_missed_close_after_active_overrun(self):
        now = datetime(2026, 9, 14, 18, 0, 27, tzinfo=self.tz)
        missed = self.schedule.missed_close_event(now)
        self.assertIsNotNone(missed)
        self.assertEqual(missed.phase, "CLOSE")
        self.assertEqual(missed.scheduled_for.strftime("%H:%M:%S"), "18:00:00")
        nxt = self.schedule.next_event_after(now)
        self.assertNotEqual(nxt.phase, "CLOSE")

    def test_pick_event_runs_overrun_close(self):
        now = datetime(2026, 9, 14, 18, 0, 27, tzinfo=self.tz)
        sched = TradingScheduler(self.schedule, pipeline=None, now_fn=lambda: now)
        event = sched.pick_event(now)
        self.assertEqual(event.phase, "CLOSE")

    def test_pick_event_skips_close_once_sent(self):
        now = datetime(2026, 9, 14, 18, 0, 27, tzinfo=self.tz)
        sched = TradingScheduler(self.schedule, pipeline=None, now_fn=lambda: now)
        sched._close_sent_on.add(date(2026, 9, 14))
        event = sched.pick_event(now)
        self.assertEqual(event.phase, "OPEN_STATUS")
        self.assertEqual(event.scheduled_for.date(), date(2026, 9, 15))


if __name__ == "__main__":
    unittest.main()
