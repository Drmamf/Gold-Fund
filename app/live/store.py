from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    FundMarketSnapshot,
    Instrument,
    LiveAccountState,
    LiveOrder,
    MarketCycle,
    Signal,
)


STRATEGY_A = "RELATIVE_BUY_HOLD"


class LiveStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def ensure_state(self) -> LiveAccountState:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(LiveAccountState, 1)
                if row is None:
                    row = LiveAccountState(id=1, current_units=Decimal("0"), frozen=False, details={})
                    session.add(row)
                    session.flush()
                return row

    def get_state(self) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.get(LiveAccountState, 1)
            if row is None:
                self.ensure_state()
                row = session.get(LiveAccountState, 1)
            return {
                "current_symbol": row.current_symbol,
                "current_units": Decimal(str(row.current_units or 0)),
                "frozen": bool(row.frozen),
                "freeze_reason": row.freeze_reason,
                "last_signal_id": row.last_signal_id,
                "details": dict(row.details or {}),
            }

    def set_state(self, **kwargs) -> None:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(LiveAccountState, 1)
                if row is None:
                    row = LiveAccountState(id=1, current_units=Decimal("0"), details={})
                    session.add(row)
                    session.flush()
                for key, value in kwargs.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now(timezone.utc)

    def claim_intent(self, intent_key: str, **fields) -> Optional[int]:
        with self.session_factory() as session:
            try:
                with session.begin():
                    existing = session.scalar(
                        select(LiveOrder).where(LiveOrder.intent_key == intent_key)
                    )
                    if existing is not None:
                        if existing.status in {"FILLED", "DRY_RUN", "PARTIAL"}:
                            return None
                        existing.status = "PENDING"
                        existing.error_message = None
                        for key, value in fields.items():
                            setattr(existing, key, value)
                        existing.updated_at = datetime.now(timezone.utc)
                        return int(existing.id)
                    row = LiveOrder(intent_key=intent_key, status="PENDING", details={}, **fields)
                    session.add(row)
                    session.flush()
                    return int(row.id)
            except IntegrityError:
                return None

    def update_order(self, order_id: int, **fields) -> None:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(LiveOrder, order_id)
                if row is None:
                    return
                for key, value in fields.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now(timezone.utc)

    def pending_rotations(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            claimed = select(LiveOrder.signal_id).where(LiveOrder.signal_id.is_not(None))
            rows = session.execute(
                select(Signal, MarketCycle)
                .join(MarketCycle, MarketCycle.id == Signal.cycle_id)
                .where(
                    Signal.strategy_id == STRATEGY_A,
                    Signal.signal_type == "ROTATE_TO",
                    MarketCycle.cycle_type == "ACTIVE",
                    MarketCycle.status == "COMPLETED",
                    Signal.id.not_in(claimed),
                )
                .order_by(Signal.id.asc())
            ).all()
            out = []
            for signal, cycle in rows:
                out.append(
                    {
                        "signal_id": int(signal.id),
                        "cycle_id": int(cycle.id),
                        "source_fund_id": signal.source_fund_id,
                        "target_fund_id": signal.target_fund_id,
                        "payload": dict(signal.payload or {}),
                    }
                )
            return out

    def quotes_for_cycle(self, cycle_id: int) -> dict[str, dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.execute(
                select(Instrument.symbol, FundMarketSnapshot)
                .join(Instrument, Instrument.id == FundMarketSnapshot.fund_id)
                .where(FundMarketSnapshot.cycle_id == cycle_id)
            ).all()
            out: dict[str, dict[str, Any]] = {}
            for symbol, snap in rows:
                out[str(symbol)] = {
                    "fund_id": int(snap.fund_id),
                    "best_bid": snap.best_bid,
                    "best_ask": snap.best_ask,
                    "data_valid": bool(snap.data_valid),
                }
            return out

    def symbol_for_fund_id(self, fund_id: int) -> Optional[str]:
        with self.session_factory() as session:
            row = session.get(Instrument, int(fund_id))
            return None if row is None else str(row.symbol)

    def latest_active_quotes(self) -> dict[str, dict[str, Any]]:
        with self.session_factory() as session:
            cycle = session.scalar(
                select(MarketCycle)
                .where(
                    MarketCycle.cycle_type == "ACTIVE",
                    MarketCycle.status == "COMPLETED",
                )
                .order_by(MarketCycle.id.desc())
                .limit(1)
            )
            if cycle is None:
                return {}
            return self.quotes_for_cycle(int(cycle.id))
