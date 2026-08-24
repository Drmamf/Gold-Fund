from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select

from app.database import SessionLocal
from app.jalali_utils import (
    gregorian_to_jalali,
    jalali_date_text,
    jalali_month_length,
    jalali_to_gregorian,
)
from app.models import AssetCompositionHistory, AssetReportStatus, Instrument


class AssetCompositionReportMonitor:
    """Daily working-day reminder for missing monthly composition updates."""

    def __init__(
        self,
        *,
        schedule_path: str | Path,
        notifications=None,
        session_factory=SessionLocal,
    ):
        self.schedule_path = Path(schedule_path)
        self.notifications = notifications
        self.session_factory = session_factory
        with self.schedule_path.open("r", encoding="utf-8") as fh:
            root = (yaml.safe_load(fh) or {})["asset_composition_report_schedule"]
        self.cfg = root
        self.policy = root.get("policy", {})
        self.funds_cfg = root.get("funds", {})
        self.tz = ZoneInfo(root.get("timezone", "Asia/Tehran"))
        self.offset_days = int(self.policy.get("reminder_start_offset_days", 5))

    @staticmethod
    def _next_jalali_month(y: int, m: int) -> tuple[int, int]:
        return (y + 1, 1) if m == 12 else (y, m + 1)

    @classmethod
    def _jalali_month_end(cls, y: int, m: int) -> tuple[int, int, int]:
        return y, m, jalali_month_length(y, m)

    @staticmethod
    def _previous_jalali_month(y: int, m: int) -> tuple[int, int]:
        return (y - 1, 12) if m == 1 else (y, m - 1)

    def _period_end_for_month(self, y: int, m: int, rule: str) -> tuple[int, int, int]:
        if rule == "jalali_month_end":
            return self._jalali_month_end(y, m)
        if rule.startswith("jalali_day_of_month:"):
            day = int(rule.split(":", 1)[1])
            return y, m, day
        raise ValueError(f"Unsupported report_period_end_rule: {rule}")

    def _latest_due_period(self, today_g: date, rule: str) -> tuple[date, str, date]:
        y, m, _ = gregorian_to_jalali(today_g)

        # Search current + prior months and select latest period whose reminder
        # start date has already arrived.
        candidates: list[tuple[date, str, date]] = []
        cy, cm = y, m
        for _ in range(0, 14):
            py, pm, pd = self._period_end_for_month(cy, cm, rule)
            period_g = jalali_to_gregorian(py, pm, pd)
            reminder_start = period_g + timedelta(days=self.offset_days)
            if reminder_start <= today_g:
                candidates.append((period_g, jalali_date_text(py, pm, pd), reminder_start))
            cy, cm = self._previous_jalali_month(cy, cm)

        if not candidates:
            raise RuntimeError(f"Could not resolve due report period for rule={rule}")
        return max(candidates, key=lambda x: x[0])

    def run(self, today_g: date) -> list[dict[str, Any]]:
        due: list[dict[str, Any]] = []
        now = datetime.now(self.tz)

        with self.session_factory() as session:
            with session.begin():
                instruments = {
                    r.symbol: r
                    for r in session.scalars(
                        select(Instrument).where(Instrument.is_gold_fund.is_(True))
                    ).all()
                }

                for symbol, cfg in self.funds_cfg.items():
                    inst = instruments.get(symbol)
                    if inst is None:
                        continue
                    period_g, period_j, reminder_start = self._latest_due_period(
                        today_g, str(cfg["report_period_end_rule"])
                    )

                    latest_mix = session.scalar(
                        select(AssetCompositionHistory)
                        .where(AssetCompositionHistory.fund_id == int(inst.id))
                        .order_by(
                            AssetCompositionHistory.as_of_date.desc(),
                            AssetCompositionHistory.id.desc(),
                        )
                        .limit(1)
                    )
                    updated = bool(
                        latest_mix is not None
                        and latest_mix.as_of_date >= period_g
                    )

                    status = session.scalar(
                        select(AssetReportStatus).where(
                            AssetReportStatus.fund_id == int(inst.id),
                            AssetReportStatus.expected_period_end == period_g,
                        )
                    )
                    if status is None:
                        status = AssetReportStatus(
                            fund_id=int(inst.id),
                            expected_period_end=period_g,
                            expected_period_end_jalali=period_j,
                            reminder_start_date=reminder_start,
                            report_received=updated,
                            report_received_at=(now if updated else None),
                            composition_updated=updated,
                            composition_updated_at=(now if updated else None),
                            reminder_count=0,
                        )
                        session.add(status)
                        session.flush()
                    elif updated and not status.composition_updated:
                        status.report_received = True
                        status.report_received_at = now
                        status.composition_updated = True
                        status.composition_updated_at = now

                    if updated:
                        continue

                    already_today = bool(
                        status.last_reminder_at is not None
                        and status.last_reminder_at.astimezone(self.tz).date() == today_g
                    )
                    if already_today:
                        continue

                    payload = {
                        "status_id": int(status.id),
                        "symbol": symbol,
                        "fund_id": int(inst.id),
                        "expected_period_end": period_g.isoformat(),
                        "expected_period_end_jalali": period_j,
                        "reminder_start_date": reminder_start.isoformat(),
                        "latest_composition_as_of": (
                            latest_mix.as_of_date.isoformat() if latest_mix else None
                        ),
                        "latest_composition_as_of_jalali": (
                            latest_mix.as_of_date_jalali if latest_mix else None
                        ),
                    }
                    due.append(payload)

        # Send outside DB transaction. Mark as reminded only after Bale accepts
        # the message; a failed notification can then be retried next run/day.
        if self.notifications is not None:
            for item in due:
                sent = self.notifications.send_asset_composition_reminder(item)
                if not sent:
                    continue
                try:
                    with self.session_factory() as session:
                        with session.begin():
                            status = session.get(AssetReportStatus, int(item["status_id"]))
                            if status is not None:
                                status.last_reminder_at = datetime.now(self.tz)
                                status.reminder_count = int(status.reminder_count or 0) + 1
                except Exception:
                    pass
        return due
