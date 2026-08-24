from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
    StrategySignal,
)
from app.database import SessionLocal
from app.execution.strategy_a_executor import load_strategy_a_runtime_state
from app.models import (
    CommonMarketSnapshot,
    DataError,
    FundMarketSnapshot,
    FundValuationSnapshot,
    MarketCycle,
    RelativeValueSnapshot,
    Signal,
    StrategyRuntimeState,
)
from app.state.strategy_b_runtime import StrategyBRuntimeStateBuilder


STRATEGY_A = "RELATIVE_BUY_HOLD"
STRATEGY_B = "THRESHOLD_10_10_RELATIVE"

logger = logging.getLogger("wallex_gold.repository")


class CycleAlreadyProcessed(RuntimeError):
    pass


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


class PostgresRepository:
    """All shared Pipeline persistence and restart-state reads."""

    def __init__(
        self,
        *,
        session_factory=SessionLocal,
        strategy_b_lookback_days: int = 7,
        config_version_id: int | None = None,
    ):
        self.session_factory = session_factory
        self.config_version_id = config_version_id
        self.strategy_b_builder = StrategyBRuntimeStateBuilder(
            lookback_days=strategy_b_lookback_days
        )

    def start_cycle(
        self,
        *,
        market_date: date,
        cycle_type: str,
        scheduled_for: datetime,
        market_is_open: bool,
    ) -> int:
        with self.session_factory() as session:
            existing = session.scalar(
                select(MarketCycle).where(
                    MarketCycle.market_date == market_date,
                    MarketCycle.cycle_type == cycle_type,
                    MarketCycle.scheduled_for == scheduled_for,
                )
            )
            if existing is not None:
                if existing.status == "COMPLETED":
                    raise CycleAlreadyProcessed(
                        f"Cycle already completed: {cycle_type} {scheduled_for.isoformat()}"
                    )
                # A prior failed/incomplete attempt at the same exact slot is
                # not replayed because execution/account side effects may have
                # committed independently. Mark it skipped and move on.
                raise CycleAlreadyProcessed(
                    f"Cycle slot already exists with status={existing.status}: "
                    f"{cycle_type} {scheduled_for.isoformat()}"
                )

            row = MarketCycle(
                market_date=market_date,
                cycle_type=cycle_type,
                scheduled_for=scheduled_for,
                market_is_open=market_is_open,
                status="STARTED",
                config_version_id=self.config_version_id,
                error_count=0,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise CycleAlreadyProcessed(
                    f"Duplicate scheduled cycle slot: {cycle_type} {scheduled_for.isoformat()}"
                ) from exc
            return int(row.id)

    def store_raw_market(
        self,
        cycle_id: int,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        tgju = common.raw.get("tgju") or {}
        usd = tgju.get("usd") or {}
        ounce = tgju.get("gold_ounce") or {}
        ime = common.raw.get("ime") or {}

        with self.session_factory() as session:
            with session.begin():
                common_row = session.get(CommonMarketSnapshot, cycle_id)
                if common_row is None:
                    common_row = CommonMarketSnapshot(
                        cycle_id=cycle_id,
                        collected_at=common.collected_at,
                    )
                    session.add(common_row)

                common_row.collected_at = common.collected_at
                common_row.usd_irr = common.usd_irr
                common_row.usd_source_timestamp = _parse_dt(usd.get("source_timestamp"))
                common_row.usd_age_seconds = _d(usd.get("age_seconds"))
                common_row.ounce_usd = common.ounce_usd
                common_row.ounce_source_timestamp = _parse_dt(
                    ounce.get("source_timestamp")
                )
                common_row.ounce_age_seconds = _d(ounce.get("age_seconds"))
                common_row.ime_bullion_price = common.ime_bullion_price
                common_row.ime_coin_price = common.ime_coin_price
                common_row.valuation_inputs_usable = bool(
                    common.valuation_inputs_usable
                )
                common_row.raw_tgju = _json_safe(tgju)
                common_row.raw_ime = _json_safe(ime)

                for fund_id, snap in funds.items():
                    raw = snap.raw or {}
                    price = raw.get("price") or {}
                    nav = raw.get("nav") or {}
                    fetched = _parse_dt(raw.get("fetched_at")) or common.collected_at

                    row = session.scalar(
                        select(FundMarketSnapshot).where(
                            FundMarketSnapshot.cycle_id == cycle_id,
                            FundMarketSnapshot.fund_id == int(fund_id),
                        )
                    )
                    if row is None:
                        row = FundMarketSnapshot(
                            cycle_id=cycle_id,
                            fund_id=int(fund_id),
                            collected_at=fetched,
                        )
                        session.add(row)

                    row.collected_at = fetched
                    row.last_price = _d(price.get("last_price"))
                    row.close_price = _d(price.get("close_price")) or _d(
                        snap.close_price
                    )
                    row.nav_issuance = _d(nav.get("nav_issuance"))
                    row.nav_redemption = _d(snap.nav_redemption)
                    row.best_bid = _d(snap.best_bid)
                    row.best_ask = _d(snap.best_ask)
                    row.signal_price = _d(snap.signal_price)
                    row.buy_exec_price = _d(snap.best_ask)
                    row.sell_exec_price = _d(snap.best_bid)
                    row.trade_value = _d(snap.trade_value)
                    row.trade_volume = _d(price.get("trade_volume"))
                    row.trade_count = int(snap.trade_count or 0)
                    row.data_valid = bool(snap.data_valid)
                    row.raw_payload = _json_safe(raw)

    def store_valuations(
        self,
        cycle_id: int,
        common: CommonSnapshot,
        valuations: Mapping[int, FundValuation],
    ) -> None:
        with self.session_factory() as session:
            with session.begin():
                common_row = session.get(CommonMarketSnapshot, cycle_id)
                if common_row is None:
                    raise RuntimeError(
                        f"common_market_snapshot missing for cycle_id={cycle_id}"
                    )
                common_row.fair_bullion_price = common.fair_bullion_price
                common_row.fair_coin_price = common.fair_coin_price
                common_row.bullion_bubble = common.bullion_bubble
                common_row.coin_bubble = common.coin_bubble
                common_row.valuation_inputs_usable = bool(
                    common.valuation_inputs_usable
                )

                for fund_id, valuation in valuations.items():
                    row = session.scalar(
                        select(FundValuationSnapshot).where(
                            FundValuationSnapshot.cycle_id == cycle_id,
                            FundValuationSnapshot.fund_id == int(fund_id),
                        )
                    )
                    if row is None:
                        row = FundValuationSnapshot(
                            cycle_id=cycle_id,
                            fund_id=int(fund_id),
                        )
                        session.add(row)
                    row.asset_composition_id = valuation.asset_composition_id
                    row.nominal_bubble = valuation.nominal_bubble
                    row.intrinsic_bubble = valuation.intrinsic_bubble
                    row.total_bubble = valuation.total_bubble
                    # Legacy-shadow fields intentionally remain NULL: the new
                    # executable Ask-only definition must never be mixed with an
                    # unverified approximation of the old model.
                    row.old_intrinsic_bubble = None
                    row.old_total_bubble = None
                    row.buy_threshold = valuation.buy_threshold
                    row.sell_threshold = valuation.sell_threshold
                    row.valuation_valid = bool(valuation.valid)

    def store_relative(
        self,
        cycle_id: int,
        relative_rows: Mapping[int, RelativeValueRow],
    ) -> None:
        with self.session_factory() as session:
            with session.begin():
                for fund_id, value in relative_rows.items():
                    row = session.scalar(
                        select(RelativeValueSnapshot).where(
                            RelativeValueSnapshot.cycle_id == cycle_id,
                            RelativeValueSnapshot.fund_id == int(fund_id),
                        )
                    )
                    if row is None:
                        row = RelativeValueSnapshot(
                            cycle_id=cycle_id,
                            fund_id=int(fund_id),
                            anchor_fund_id=int(value.anchor_fund_id),
                        )
                        session.add(row)
                    row.anchor_fund_id = int(value.anchor_fund_id)
                    row.current_gap = value.current_gap
                    row.historical_normal_gap = value.historical_normal_gap
                    row.relative_score = value.relative_score
                    row.relative_rank = value.rank
                    row.best_target_fund_id = value.best_target_fund_id
                    row.gross_rotation_edge = value.gross_rotation_edge
                    row.spread_cost = value.spread_cost
                    row.fee_cost = value.fee_cost
                    row.net_executable_edge = value.net_executable_edge
                    row.executable = bool(value.executable)
                    row.details = _json_safe(value.details)

    def load_strategy_state(
        self,
        strategy_id: str,
        *,
        market_date: date,
    ) -> dict:
        with self.session_factory() as session:
            if strategy_id == STRATEGY_A:
                return load_strategy_a_runtime_state(session, strategy_id)
            if strategy_id == STRATEGY_B:
                return self.strategy_b_builder.build(session, market_date)

            rows = session.scalars(
                select(StrategyRuntimeState).where(
                    StrategyRuntimeState.strategy_id == strategy_id
                )
            ).all()
            return {
                f"{row.scope_key}:{row.state_key}": _json_safe(row.state_value)
                for row in rows
            }

    def store_signals(
        self,
        cycle_id: int,
        signals: Sequence[StrategySignal],
    ) -> tuple[list[int], list[StrategySignal]]:
        if not signals:
            return [], []
        with self.session_factory() as session:
            with session.begin():
                rows: list[Signal] = []
                accepted_signals: list[StrategySignal] = []

                for signal in signals:

                    # Strategy B rotation dedup:
                    # Same position/source/target within cooldown window
                    # is the same opportunity, not a new signal.
                    if (
                        signal.strategy_id == STRATEGY_B
                        and signal.signal_type == "ROTATE_TO"
                    ):
                        position_id = (signal.payload or {}).get("position_id")

                        if position_id is not None:
                            recent_duplicate = session.scalar(
                                select(Signal.id).where(
                                    Signal.strategy_id == signal.strategy_id,
                                    Signal.signal_type == signal.signal_type,
                                    Signal.source_fund_id == signal.source_fund_id,
                                    Signal.target_fund_id == signal.target_fund_id,
                                    Signal.payload["position_id"].astext
                                    == str(position_id),
                                    Signal.generated_at >= (
                                        datetime.now(timezone.utc)
                                        - timedelta(minutes=30)
                                    ),
                                ).limit(1)
                            )

                            logger.info(
                                "ROTATE_DEDUP_CHECK | position=%s source=%s target=%s found_duplicate=%s",
                                position_id,
                                signal.source_fund_id,
                                signal.target_fund_id,
                                recent_duplicate,
                            )

                            if recent_duplicate is not None:
                                logger.info(
                                    "ROTATE_SIGNAL_SUPPRESSED_DUPLICATE | "
                                    "position=%s source=%s target=%s cooldown_minutes=30",
                                    position_id,
                                    signal.source_fund_id,
                                    signal.target_fund_id,
                                )
                                continue

                    row = Signal(
                        cycle_id=cycle_id,
                        strategy_id=signal.strategy_id,
                        engine=signal.engine,
                        fund_id=signal.fund_id,
                        source_fund_id=signal.source_fund_id,
                        target_fund_id=signal.target_fund_id,
                        signal_type=signal.signal_type,
                        signal_stage=signal.signal_stage,
                        nominal_bubble=signal.nominal_bubble,
                        intrinsic_bubble=signal.intrinsic_bubble,
                        total_bubble=signal.total_bubble,
                        relative_score=signal.relative_score,
                        gross_edge=signal.gross_edge,
                        spread_cost=signal.spread_cost,
                        fee_cost=signal.fee_cost,
                        net_executable_edge=signal.net_executable_edge,
                        account_had_capacity=None,
                        trade_executed=None,
                        non_execution_reason=None,
                        payload=_json_safe(signal.payload),
                    )
                    session.add(row)
                    rows.append(row)
                    accepted_signals.append(signal)

                session.flush()
                return [int(row.id) for row in rows], accepted_signals

    def complete_cycle(self, cycle_id: int) -> None:
        with self.session_factory() as session:
            with session.begin():
                row = session.get(MarketCycle, cycle_id)
                if row is None:
                    raise RuntimeError(f"market cycle not found: {cycle_id}")
                row.status = "COMPLETED"
                row.completed_at = datetime.now(timezone.utc)

    def fail_cycle(self, cycle_id: int, exc: Exception) -> None:
        # Failure persistence itself is best-effort; never mask the original.
        try:
            with self.session_factory() as session:
                with session.begin():
                    row = session.get(MarketCycle, cycle_id)
                    if row is not None:
                        row.status = "FAILED"
                        row.completed_at = datetime.now(timezone.utc)
                        row.error_count = int(row.error_count or 0) + 1
                    session.add(
                        DataError(
                            cycle_id=cycle_id,
                            source="PIPELINE",
                            error_type=type(exc).__name__,
                            severity="ERROR",
                            message=str(exc),
                            details={"repr": repr(exc)},
                        )
                    )
        except Exception:
            pass
