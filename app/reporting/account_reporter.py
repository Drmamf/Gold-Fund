from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    AccountSnapshot,
    Instrument,
    PositionCurrent,
    Signal,
)


STRATEGY_A = "RELATIVE_BUY_HOLD"
STRATEGY_B = "THRESHOLD_10_10_RELATIVE"


class AccountReporter:
    def __init__(self, engine: Engine, *, timezone: str = "Asia/Tehran"):
        self.engine = engine
        self.tz = ZoneInfo(timezone)

    def _latest_snapshot(
        self,
        session: Session,
        strategy_id: str,
        *,
        before: datetime | None = None,
    ):
        stmt = select(AccountSnapshot).where(
            AccountSnapshot.strategy_id == strategy_id
        )
        if before is not None:
            stmt = stmt.where(AccountSnapshot.captured_at < before)
        return session.scalar(
            stmt.order_by(
                AccountSnapshot.captured_at.desc(),
                AccountSnapshot.id.desc(),
            ).limit(1)
        )

    def _symbol_map(self, session: Session) -> dict[int, str]:
        return {
            int(i): s
            for i, s in session.execute(
                select(Instrument.id, Instrument.symbol)
            ).all()
        }

    def _positions(self, session: Session, strategy_id: str):
        return session.scalars(
            select(PositionCurrent)
            .where(
                PositionCurrent.strategy_id == strategy_id,
                PositionCurrent.status == "OPEN",
            )
            .order_by(PositionCurrent.position_id)
        ).all()

    def snapshot_report(
        self,
        strategy_id: str,
        *,
        before: datetime | None = None,
        trade_date: date | None = None,
    ) -> dict[str, Any]:
        with Session(self.engine) as session:
            snap = self._latest_snapshot(
                session, strategy_id, before=before
            )
            if snap is None:
                return {"exists": False}

            symbols = self._symbol_map(session)
            positions = self._positions(session, strategy_id)

            portfolio = Decimal(snap.portfolio_value or 0)
            gold = Decimal(snap.gold_exposure or 0)
            ratio = (gold / portfolio) if portfolio > 0 else Decimal("0")

            report: dict[str, Any] = {
                "exists": True,
                "captured_at": snap.captured_at.astimezone(self.tz).strftime(
                    "%Y-%m-%d %H:%M"
                ) if snap.captured_at.tzinfo else snap.captured_at.strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "portfolio_value": snap.portfolio_value,
                "cash": snap.cash,
                "gold_exposure": snap.gold_exposure,
                "gold_exposure_ratio": ratio,
                "fixed_income_value": snap.fixed_income_value,
                "realized_pnl": snap.realized_pnl,
                "unrealized_pnl": snap.unrealized_pnl,
                "total_return": snap.total_return,
                "fees_total": snap.fees_total,
                "turnover": snap.turnover,
                "drawdown": snap.drawdown,
                "active_positions_count": snap.active_positions_count,
                "active_funds": [
                    symbols.get(int(p.current_fund_id), str(p.current_fund_id))
                    for p in positions
                ],
                "rotations_count": sum(
                    int(p.rotations_count or 0) for p in positions
                ),
            }

            if strategy_id == STRATEGY_A and positions:
                p = positions[0]
                report.update({
                    "current_fund": symbols.get(
                        int(p.current_fund_id), str(p.current_fund_id)
                    ),
                    "units": p.units,
                })

            if trade_date is not None:
                start = datetime.combine(
                    trade_date, time.min, tzinfo=self.tz
                )
                end = datetime.combine(
                    trade_date, time.max, tzinfo=self.tz
                )

                previous = self._latest_snapshot(
                    session, strategy_id, before=start
                )
                report["daily_pnl"] = (
                    Decimal(snap.portfolio_value or 0)
                    - Decimal(previous.portfolio_value or 0)
                    if previous is not None
                    else Decimal("0")
                )

                today_signals = session.scalars(
                    select(Signal).where(
                        Signal.strategy_id == strategy_id,
                        Signal.generated_at >= start,
                        Signal.generated_at <= end,
                    )
                ).all()
                report["signals_today"] = len(today_signals)
                report["entries_today"] = sum(
                    1 for s in today_signals
                    if s.signal_type in {
                        "THRESHOLD_BUY",
                        "MA7_FALLBACK_BUY_2",
                    }
                )
                report["exits_today"] = sum(
                    1 for s in today_signals
                    if s.signal_type == "THRESHOLD_SELL"
                )
                report["rotations_today"] = sum(
                    1 for s in today_signals
                    if s.signal_type == "ROTATE_TO"
                )

            return report
