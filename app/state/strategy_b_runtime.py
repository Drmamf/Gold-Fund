from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyFundSummary, Instrument, PositionCurrent, StrategyRuntimeState
from app.strategies.strategy_b_entry_state import StrategyBEntryState


class StrategyBRuntimeStateBuilder:
    """Read-only adapter: PostgreSQL -> Strategy B runtime context."""

    def __init__(self, strategy_id: str = "THRESHOLD_10_10_RELATIVE", lookback_days: int = 7):
        self.strategy_id = strategy_id
        self.lookback_days = int(lookback_days)

    def build(self, session: Session, market_date: date) -> dict[str, Any]:
        account_row = session.scalar(
            select(StrategyRuntimeState).where(
                StrategyRuntimeState.strategy_id == self.strategy_id,
                StrategyRuntimeState.scope_key == "GLOBAL",
                StrategyRuntimeState.state_key == "account",
            )
        )
        account = dict(account_row.state_value or {}) if account_row else {}
        account = StrategyBEntryState.from_mapping(account).merge_into(account)

        fund_states: dict[str, Any] = {}
        rows = session.scalars(
            select(StrategyRuntimeState).where(
                StrategyRuntimeState.strategy_id == self.strategy_id,
                StrategyRuntimeState.state_key == "buy_signal_state",
            )
        ).all()
        for row in rows:
            if row.fund_id is not None:
                fund_states[str(int(row.fund_id))] = dict(row.state_value or {})

        positions = session.scalars(
            select(PositionCurrent).where(
                PositionCurrent.strategy_id == self.strategy_id,
                PositionCurrent.status == "OPEN",
            )
        ).all()
        open_positions = [
            {
                "position_id": int(p.position_id),
                "parent_position_id": p.parent_position_id,
                "origin_entry_type": p.origin_entry_type,
                "origin_fund_id": p.origin_fund_id,
                "current_fund_id": int(p.current_fund_id),
                "rotations_count": int(p.rotations_count or 0),
            }
            for p in positions
        ]

        # Sum the FINAL daily trade value of all ACTIVE gold funds for each
        # prior trading day. A date counts only when every active gold fund has
        # a non-null daily final value, so the MA7 denominator never contains
        # partial/incomplete market days.
        gold_fund_count = session.scalar(
            select(func.count(Instrument.id)).where(
                Instrument.is_gold_fund.is_(True),
                Instrument.is_active.is_(True),
            )
        ) or 0

        daily = session.execute(
            select(
                DailyFundSummary.trade_date,
                func.sum(DailyFundSummary.last_trade_value).label("total_trade_value"),
            )
            .join(Instrument, Instrument.id == DailyFundSummary.fund_id)
            .where(
                Instrument.is_gold_fund.is_(True),
                Instrument.is_active.is_(True),
                DailyFundSummary.trade_date < market_date,
                DailyFundSummary.last_trade_value.is_not(None),
            )
            .group_by(DailyFundSummary.trade_date)
            .having(func.count(DailyFundSummary.fund_id) == gold_fund_count)
            .order_by(DailyFundSummary.trade_date.desc())
            .limit(self.lookback_days)
        ).all()

        history = [
            (d, Decimal(str(v)))
            for d, v in daily
            if v is not None and Decimal(str(v)) > 0
        ]
        values = [v for _, v in history]
        previous_avg = (
            sum(values, Decimal("0")) / Decimal(len(values)) if values else None
        )

        out = dict(account)
        out.update(
            {
                "market_date": market_date.isoformat(),
                "funds": fund_states,
                "open_positions": open_positions,
                "ma7": {
                    "history_days_available": len(values),
                    "history_days_used": [d.isoformat() for d, _ in history],
                    "previous_7d_average_trade_value": (
                        str(previous_avg) if previous_avg is not None else None
                    ),
                },
            }
        )
        return out
