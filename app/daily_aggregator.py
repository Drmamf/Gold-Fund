from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    CommonMarketSnapshot,
    DailyCommonSummary,
    DailyFundSummary,
    FundMarketSnapshot,
    FundValuationSnapshot,
    Instrument,
    MarketCycle,
    RelativeValueSnapshot,
)


ZERO = Decimal("0")


def _vals(items: Iterable[Any]) -> list[Decimal]:
    out: list[Decimal] = []
    for value in items:
        if value is None:
            continue
        try:
            out.append(Decimal(str(value)))
        except Exception:
            pass
    return out


def _mean(values: list[Decimal]) -> Optional[Decimal]:
    return sum(values, ZERO) / Decimal(len(values)) if values else None


def _last_non_null(rows, attr: str):
    for row in reversed(rows):
        value = getattr(row, attr, None)
        if value is not None:
            return value
    return None


def _quality(valid_count: int, total_count: int) -> str:
    if total_count <= 0 or valid_count <= 0:
        return "INVALID"
    if valid_count == total_count:
        return "GOOD"
    return "PARTIAL"


class PostgresDailyAggregator:
    """
    Recomputes and UPSERTs the current day after every WARMUP/ACTIVE/CLOSE.

    TSETMC trade value/count are cumulative intraday, therefore daily summary
    stores the LAST available value/count, never their mean.
    """

    def __init__(self, *, session_factory=SessionLocal):
        self.session_factory = session_factory

    def upsert_current_day(self, trade_date: date) -> None:
        with self.session_factory() as session:
            with session.begin():
                cycles = session.scalars(
                    select(MarketCycle)
                    .where(MarketCycle.market_date == trade_date)
                    .order_by(MarketCycle.scheduled_for, MarketCycle.id)
                ).all()
                cycle_ids = [int(c.id) for c in cycles]
                if not cycle_ids:
                    return

                common_rows = session.scalars(
                    select(CommonMarketSnapshot)
                    .where(CommonMarketSnapshot.cycle_id.in_(cycle_ids))
                    .order_by(CommonMarketSnapshot.collected_at)
                ).all()
                self._upsert_common(session, trade_date, common_rows)

                gold_funds = session.scalars(
                    select(Instrument).where(
                        Instrument.is_gold_fund.is_(True),
                        Instrument.is_active.is_(True),
                    )
                ).all()

                for inst in gold_funds:
                    self._upsert_fund(
                        session,
                        trade_date,
                        cycle_ids,
                        int(inst.id),
                    )

    def _upsert_common(self, session, trade_date: date, rows) -> None:
        target = session.get(DailyCommonSummary, trade_date)
        if target is None:
            target = DailyCommonSummary(trade_date=trade_date)
            session.add(target)

        target.observations_count = len(rows)
        usd = _vals(r.usd_irr for r in rows)
        ounce = _vals(r.ounce_usd for r in rows)
        bullion = _vals(r.bullion_bubble for r in rows)
        coin = _vals(r.coin_bubble for r in rows)

        target.mean_usd_irr = _mean(usd)
        target.last_usd_irr = _last_non_null(rows, "usd_irr")
        target.mean_ounce_usd = _mean(ounce)
        target.last_ounce_usd = _last_non_null(rows, "ounce_usd")

        target.mean_bullion_bubble = _mean(bullion)
        target.min_bullion_bubble = min(bullion) if bullion else None
        target.max_bullion_bubble = max(bullion) if bullion else None
        target.last_bullion_bubble = _last_non_null(rows, "bullion_bubble")

        target.mean_coin_bubble = _mean(coin)
        target.min_coin_bubble = min(coin) if coin else None
        target.max_coin_bubble = max(coin) if coin else None
        target.last_coin_bubble = _last_non_null(rows, "coin_bubble")

        target.first_snapshot_at = rows[0].collected_at if rows else None
        target.last_snapshot_at = rows[-1].collected_at if rows else None
        valid_count = sum(1 for r in rows if r.valuation_inputs_usable)
        target.data_quality_status = _quality(valid_count, len(rows))

    def _upsert_fund(
        self,
        session,
        trade_date: date,
        cycle_ids: list[int],
        fund_id: int,
    ) -> None:
        market_rows = session.scalars(
            select(FundMarketSnapshot)
            .where(
                FundMarketSnapshot.cycle_id.in_(cycle_ids),
                FundMarketSnapshot.fund_id == fund_id,
            )
            .order_by(FundMarketSnapshot.collected_at, FundMarketSnapshot.id)
        ).all()
        valuation_rows = session.scalars(
            select(FundValuationSnapshot)
            .where(
                FundValuationSnapshot.cycle_id.in_(cycle_ids),
                FundValuationSnapshot.fund_id == fund_id,
            )
            .order_by(FundValuationSnapshot.cycle_id)
        ).all()
        relative_rows = session.scalars(
            select(RelativeValueSnapshot)
            .where(
                RelativeValueSnapshot.cycle_id.in_(cycle_ids),
                RelativeValueSnapshot.fund_id == fund_id,
            )
            .order_by(RelativeValueSnapshot.cycle_id)
        ).all()

        target = session.scalar(
            select(DailyFundSummary).where(
                DailyFundSummary.trade_date == trade_date,
                DailyFundSummary.fund_id == fund_id,
            )
        )
        if target is None:
            target = DailyFundSummary(
                trade_date=trade_date,
                fund_id=fund_id,
            )
            session.add(target)

        target.observations_count = len(market_rows)

        nominal = _vals(r.nominal_bubble for r in valuation_rows)
        intrinsic = _vals(r.intrinsic_bubble for r in valuation_rows)
        total = _vals(r.total_bubble for r in valuation_rows)
        relative = _vals(r.relative_score for r in relative_rows)

        target.mean_nominal_bubble = _mean(nominal)
        target.min_nominal_bubble = min(nominal) if nominal else None
        target.max_nominal_bubble = max(nominal) if nominal else None
        target.last_nominal_bubble = _last_non_null(
            valuation_rows, "nominal_bubble"
        )

        target.mean_intrinsic_bubble = _mean(intrinsic)
        target.min_intrinsic_bubble = min(intrinsic) if intrinsic else None
        target.max_intrinsic_bubble = max(intrinsic) if intrinsic else None
        target.last_intrinsic_bubble = _last_non_null(
            valuation_rows, "intrinsic_bubble"
        )

        target.mean_total_bubble = _mean(total)
        target.min_total_bubble = min(total) if total else None
        target.max_total_bubble = max(total) if total else None
        target.last_total_bubble = _last_non_null(
            valuation_rows, "total_bubble"
        )

        target.mean_relative_score = _mean(relative)
        target.min_relative_score = min(relative) if relative else None
        target.max_relative_score = max(relative) if relative else None
        target.last_relative_score = _last_non_null(
            relative_rows, "relative_score"
        )

        # Cumulative intraday fields: last valid observation only.
        target.last_trade_value = _last_non_null(market_rows, "trade_value")
        target.last_trade_count = _last_non_null(market_rows, "trade_count")
        target.first_snapshot_at = (
            market_rows[0].collected_at if market_rows else None
        )
        target.last_snapshot_at = (
            market_rows[-1].collected_at if market_rows else None
        )

        valid_market_by_cycle = {
            int(r.cycle_id): bool(r.data_valid) for r in market_rows
        }
        valid_valuation_by_cycle = {
            int(r.cycle_id): bool(r.valuation_valid) for r in valuation_rows
        }
        observed_cycle_ids = set(valid_market_by_cycle) | set(valid_valuation_by_cycle)
        valid_count = sum(
            1
            for cid in observed_cycle_ids
            if valid_market_by_cycle.get(cid, False)
            and valid_valuation_by_cycle.get(cid, False)
        )
        target.data_quality_status = _quality(
            valid_count, len(observed_cycle_ids)
        )
