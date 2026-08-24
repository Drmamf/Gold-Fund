from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import time as time_module
import logging
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import yaml


WEEKDAY_NAME_TO_PYTHON = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


@dataclass(frozen=True)
class ScheduleEvent:
    phase: str
    scheduled_for: datetime

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.scheduled_for.isoformat()}"


@dataclass(frozen=True)
class MarketSchedule:
    timezone: ZoneInfo
    working_weekdays: frozenset[int]
    open_status_time: time
    warmup_time: time
    active_start: time
    active_end: time
    cycle_seconds: int
    close_snapshot_enabled: bool
    close_snapshot_time: time
    weekly_backup_weekday: int
    weekly_backup_time: time

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        hh, mm = map(int, str(value).split(":"))
        return time(hour=hh, minute=mm)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MarketSchedule":
        with Path(path).open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}

        app = payload.get("app", {})
        cfg = payload["schedule"]
        backup = payload.get("weekly_backup", {})

        weekdays = frozenset(
            WEEKDAY_NAME_TO_PYTHON[name]
            for name in cfg["working_weekdays"]
        )
        cycle_seconds = int(cfg.get("cycle_seconds", 180))
        if cycle_seconds < 1:
            raise ValueError("cycle_seconds must be positive.")

        open_status = cls._parse_hhmm(
            cfg.get("open_status_time", "12:00")
        )
        warmup = cls._parse_hhmm(cfg["warmup_time"])
        active_start = cls._parse_hhmm(cfg["active_start"])
        active_end = cls._parse_hhmm(cfg["active_end"])
        close_time = cls._parse_hhmm(
            cfg.get("close_snapshot_time", cfg["active_end"])
        )

        if not (open_status < warmup < active_start < active_end):
            raise ValueError(
                "Expected open_status < warmup < active_start < active_end."
            )
        if close_time != active_end:
            raise ValueError(
                "close_snapshot_time must equal active_end."
            )

        backup_day_name = backup.get("weekday", "Wednesday")
        return cls(
            timezone=ZoneInfo(app.get("timezone", "Asia/Tehran")),
            working_weekdays=weekdays,
            open_status_time=open_status,
            warmup_time=warmup,
            active_start=active_start,
            active_end=active_end,
            cycle_seconds=cycle_seconds,
            close_snapshot_enabled=bool(
                cfg.get("close_snapshot_enabled", True)
            ),
            close_snapshot_time=close_time,
            weekly_backup_weekday=WEEKDAY_NAME_TO_PYTHON[
                backup_day_name
            ],
            weekly_backup_time=cls._parse_hhmm(
                backup.get("time", "18:00")
            ),
        )

    def is_working_day(self, day: date) -> bool:
        return day.weekday() in self.working_weekdays

    def events_for_day(self, day: date) -> list[ScheduleEvent]:
        events: list[ScheduleEvent] = []
        tz = self.timezone

        if self.is_working_day(day):
            events.append(
                ScheduleEvent(
                    "OPEN_STATUS",
                    datetime.combine(
                        day, self.open_status_time, tzinfo=tz
                    ),
                )
            )
            events.append(
                ScheduleEvent(
                    "WARMUP",
                    datetime.combine(
                        day, self.warmup_time, tzinfo=tz
                    ),
                )
            )

            cursor = datetime.combine(
                day, self.active_start, tzinfo=tz
            )
            active_end_dt = datetime.combine(
                day, self.active_end, tzinfo=tz
            )
            step = timedelta(seconds=self.cycle_seconds)
            while cursor < active_end_dt:
                events.append(ScheduleEvent("ACTIVE", cursor))
                cursor += step

            if self.close_snapshot_enabled:
                events.append(
                    ScheduleEvent(
                        "CLOSE",
                        datetime.combine(
                            day,
                            self.close_snapshot_time,
                            tzinfo=tz,
                        ),
                    )
                )

        # Weekly backup is intentionally after market close on Wednesday.
        if day.weekday() == self.weekly_backup_weekday:
            events.append(
                ScheduleEvent(
                    "WEEKLY_BACKUP",
                    datetime.combine(
                        day, self.weekly_backup_time, tzinfo=tz
                    ),
                )
            )

        return sorted(
            events,
            key=lambda e: (
                e.scheduled_for,
                {
                    "OPEN_STATUS": 0,
                    "WARMUP": 1,
                    "ACTIVE": 2,
                    "CLOSE": 3,
                    "WEEKLY_BACKUP": 4,
                }.get(e.phase, 99),
            ),
        )

    def next_event_after(
        self,
        now: datetime,
        *,
        include_now: bool = True,
    ) -> ScheduleEvent:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware.")

        local_now = now.astimezone(self.timezone)
        for offset in range(0, 15):
            day = local_now.date() + timedelta(days=offset)
            for event in self.events_for_day(day):
                if include_now and event.scheduled_for >= local_now:
                    return event
                if not include_now and event.scheduled_for > local_now:
                    return event
        raise RuntimeError("Could not resolve next schedule event.")


class TradingScheduler:
    def __init__(
        self,
        schedule: MarketSchedule,
        pipeline,
        *,
        notifications=None,
        maintenance=None,
        now_fn: Optional[Callable[[], datetime]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        heartbeat_fn: Optional[Callable[[], None]] = None,
    ):
        self.schedule = schedule
        self.pipeline = pipeline
        self.notifications = notifications
        self.maintenance = maintenance
        self.now_fn = now_fn or (
            lambda: datetime.now(self.schedule.timezone)
        )
        self.sleep_fn = sleep_fn or time_module.sleep
        self.heartbeat_fn = heartbeat_fn
        self._last_event_key: Optional[str] = None
        self.logger = logging.getLogger("wallex_gold.scheduler")

    def dispatch(self, event: ScheduleEvent):
        trade_date = event.scheduled_for.date()

        if event.phase == "OPEN_STATUS":
            if self.maintenance is not None:
                self.maintenance.run(trade_date)
            if self.notifications is not None:
                self.notifications.send_market_open_status(trade_date)
            return None

        if event.phase == "WARMUP":
            return self.pipeline.run_warmup(
                trade_date=trade_date,
                scheduled_for=event.scheduled_for,
            )

        if event.phase == "ACTIVE":
            # One start-of-work card exactly on the first live slot.
            if (
                self.notifications is not None
                and event.scheduled_for.time()
                == self.schedule.active_start
            ):
                self.notifications.send_operational_start(
                    event.scheduled_for
                )

            return self.pipeline.run_active_cycle(
                trade_date=trade_date,
                scheduled_for=event.scheduled_for,
            )

        if event.phase == "CLOSE":
            cycle_id = self.pipeline.run_close(
                trade_date=trade_date,
                scheduled_for=event.scheduled_for,
            )
            if self.notifications is not None:
                self.notifications.send_close_bundle(trade_date)
            return cycle_id

        if event.phase == "WEEKLY_BACKUP":
            if self.notifications is not None:
                self.notifications.send_weekly_backup(trade_date)
            return None

        raise ValueError(f"Unknown schedule phase: {event.phase!r}")

    def run_forever(self) -> None:
        while True:
            now = self.now_fn().astimezone(self.schedule.timezone)
            event = self.schedule.next_event_after(now, include_now=True)

            delay = max(
                0.0,
                (event.scheduled_for - now).total_seconds(),
            )
            if delay > 0:
                # Keep bot_runs heartbeat fresh even when the next market event
                # is hours/days away.
                remaining = delay
                while remaining > 0:
                    chunk = min(remaining, 60.0)
                    self.sleep_fn(chunk)
                    remaining -= chunk
                    if self.heartbeat_fn is not None:
                        self.heartbeat_fn()

            if event.key == self._last_event_key:
                self.sleep_fn(0.25)
                continue

            try:
                if self.heartbeat_fn is not None:
                    self.heartbeat_fn()
                self.dispatch(event)
            except Exception:
                # One bad market/API/DB cycle must not kill the VPS daemon.
                self.logger.exception(
                    "Scheduled event failed | phase=%s | scheduled_for=%s",
                    event.phase,
                    event.scheduled_for.isoformat(),
                )
            finally:
                self._last_event_key = event.key
                if self.heartbeat_fn is not None:
                    self.heartbeat_fn()
