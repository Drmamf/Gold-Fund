from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


NUM = Numeric(24, 8)
MONEY = Numeric(28, 4)
PCT = Numeric(18, 10)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(256))
    instrument_type: Mapped[str] = mapped_column(String(64), nullable=False)
    is_gold_fund: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_anchor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssetCompositionHistory(Base):
    __tablename__ = "asset_composition_history"
    __table_args__ = (
        UniqueConstraint("fund_id", "as_of_date", name="uq_asset_mix_fund_asof"),
        Index("ix_asset_mix_fund_asof", "fund_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)

    report_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    report_period_end_jalali: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_date_jalali: Mapped[str] = mapped_column(String(16), nullable=False)

    raw_bullion_weight: Mapped[Decimal] = mapped_column(PCT, nullable=False)
    raw_coin_weight: Mapped[Decimal] = mapped_column(PCT, nullable=False)
    normalized_bullion_weight: Mapped[Decimal] = mapped_column(PCT, nullable=False)
    normalized_coin_weight: Mapped[Decimal] = mapped_column(PCT, nullable=False)

    source_file: Mapped[Optional[str]] = mapped_column(String(512))
    source_hash: Mapped[Optional[str]] = mapped_column(String(128))
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    __table_args__ = (
        Index("ix_config_versions_scope_activated", "config_scope", "activated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    config_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    version_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MarketCycle(Base):
    __tablename__ = "market_cycles"
    __table_args__ = (
        UniqueConstraint(
            "market_date", "cycle_type", "scheduled_for",
            name="uq_market_cycle_schedule_slot",
        ),
        Index("ix_market_cycles_market_date_started", "market_date", "started_at"),
        Index("ix_market_cycles_scheduled_for", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    market_date: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE"
    )
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    market_is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    config_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("config_versions.id")
    )
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CommonMarketSnapshot(Base):
    __tablename__ = "common_market_snapshot"

    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("market_cycles.id", ondelete="CASCADE"), primary_key=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    usd_irr: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    usd_source_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    usd_age_seconds: Mapped[Optional[Decimal]] = mapped_column(NUM)

    ounce_usd: Mapped[Optional[Decimal]] = mapped_column(NUM)
    ounce_source_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ounce_age_seconds: Mapped[Optional[Decimal]] = mapped_column(NUM)

    ime_bullion_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    ime_coin_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    fair_bullion_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    fair_coin_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    bullion_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    coin_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    valuation_inputs_usable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    raw_tgju: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_ime: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class FundMarketSnapshot(Base):
    __tablename__ = "fund_market_snapshot"
    __table_args__ = (
        UniqueConstraint("cycle_id", "fund_id", name="uq_fund_market_cycle_fund"),
        Index("ix_fund_market_fund_collected", "fund_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("market_cycles.id", ondelete="CASCADE"), nullable=False
    )
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    last_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    close_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    nav_issuance: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    nav_redemption: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    best_bid: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    best_ask: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    signal_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    buy_exec_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    sell_exec_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    trade_value: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    trade_volume: Mapped[Optional[Decimal]] = mapped_column(NUM)
    trade_count: Mapped[Optional[int]] = mapped_column(BigInteger)

    data_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class FundValuationSnapshot(Base):
    __tablename__ = "fund_valuation_snapshot"
    __table_args__ = (
        UniqueConstraint("cycle_id", "fund_id", name="uq_fund_valuation_cycle_fund"),
        Index("ix_fund_valuation_fund_cycle", "fund_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("market_cycles.id", ondelete="CASCADE"), nullable=False
    )
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    asset_composition_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("asset_composition_history.id")
    )

    nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    # Temporary shadow values for forward comparison with the legacy model.
    old_intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    old_total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    buy_threshold: Mapped[Optional[Decimal]] = mapped_column(PCT)
    sell_threshold: Mapped[Optional[Decimal]] = mapped_column(PCT)
    valuation_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RelativeValueSnapshot(Base):
    __tablename__ = "relative_value_snapshot"
    __table_args__ = (
        UniqueConstraint("cycle_id", "fund_id", name="uq_relative_cycle_fund"),
        Index("ix_relative_fund_cycle", "fund_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("market_cycles.id", ondelete="CASCADE"), nullable=False
    )
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    anchor_fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)

    current_gap: Mapped[Optional[Decimal]] = mapped_column(PCT)
    historical_normal_gap: Mapped[Optional[Decimal]] = mapped_column(PCT)
    relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)
    relative_rank: Mapped[Optional[int]] = mapped_column(Integer)

    best_target_fund_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("instruments.id")
    )
    gross_rotation_edge: Mapped[Optional[Decimal]] = mapped_column(PCT)
    spread_cost: Mapped[Optional[Decimal]] = mapped_column(PCT)
    fee_cost: Mapped[Optional[Decimal]] = mapped_column(PCT)
    net_executable_edge: Mapped[Optional[Decimal]] = mapped_column(PCT)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Pairwise source->target diagnostics and total switch-cost details.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class DailyCommonSummary(Base):
    __tablename__ = "daily_common_summary"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    observations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    mean_usd_irr: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    last_usd_irr: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    mean_ounce_usd: Mapped[Optional[Decimal]] = mapped_column(NUM)
    last_ounce_usd: Mapped[Optional[Decimal]] = mapped_column(NUM)

    mean_bullion_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_bullion_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_bullion_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_bullion_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    mean_coin_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_coin_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_coin_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_coin_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    first_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_quality_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNKNOWN"
    )


class DailyFundSummary(Base):
    __tablename__ = "daily_fund_summary"
    __table_args__ = (
        UniqueConstraint("trade_date", "fund_id", name="uq_daily_fund_date_fund"),
        Index("ix_daily_fund_fund_date", "fund_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    observations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    mean_nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    mean_intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    mean_total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)

    mean_relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)
    min_relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)
    max_relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)
    last_relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)

    last_trade_value: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    last_trade_count: Mapped[Optional[int]] = mapped_column(BigInteger)

    first_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_quality_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNKNOWN"
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_strategy_generated", "strategy_id", "generated_at"),
        Index("ix_signals_cycle", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("market_cycles.id"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    engine: Mapped[str] = mapped_column(String(64), nullable=False)

    fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    source_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    target_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))

    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_stage: Mapped[Optional[str]] = mapped_column(String(64))

    nominal_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    intrinsic_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    total_bubble: Mapped[Optional[Decimal]] = mapped_column(PCT)
    relative_score: Mapped[Optional[Decimal]] = mapped_column(PCT)
    gross_edge: Mapped[Optional[Decimal]] = mapped_column(PCT)
    spread_cost: Mapped[Optional[Decimal]] = mapped_column(PCT)
    fee_cost: Mapped[Optional[Decimal]] = mapped_column(PCT)
    net_executable_edge: Mapped[Optional[Decimal]] = mapped_column(PCT)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    account_had_capacity: Mapped[Optional[bool]] = mapped_column(Boolean)
    trade_executed: Mapped[Optional[bool]] = mapped_column(Boolean)
    non_execution_reason: Mapped[Optional[str]] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_strategy_executed", "strategy_id", "executed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("market_cycles.id"), nullable=False)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"))
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    target_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))

    units: Mapped[Optional[Decimal]] = mapped_column(NUM)
    source_units: Mapped[Optional[Decimal]] = mapped_column(NUM)
    target_units: Mapped[Optional[Decimal]] = mapped_column(NUM)
    source_bid: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    target_ask: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    gross_value: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    sell_fee: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    buy_fee: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    bid_ask_cost: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    total_transaction_cost: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    portfolio_before: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    portfolio_after: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    reason: Mapped[Optional[str]] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PositionCurrent(Base):
    __tablename__ = "positions_current"
    __table_args__ = (
        Index("ix_positions_strategy_status", "strategy_id", "status"),
    )

    position_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)

    origin_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    current_fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    origin_entry_type: Mapped[Optional[str]] = mapped_column(String(64))
    parent_position_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    units: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    cost_basis: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    mark_price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    market_value: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    unrealized_pnl: Mapped[Optional[Decimal]] = mapped_column(MONEY)

    rotations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")


class PositionEvent(Base):
    __tablename__ = "position_events"
    __table_args__ = (
        Index("ix_position_events_position_time", "position_id", "happened_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions_current.position_id"), nullable=False
    )
    cycle_id: Mapped[int] = mapped_column(ForeignKey("market_cycles.id"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    target_fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))

    units: Mapped[Optional[Decimal]] = mapped_column(NUM)
    price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    gross_value: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    fees: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"))

    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (
        UniqueConstraint("cycle_id", "strategy_id", name="uq_account_cycle_strategy"),
        Index("ix_account_strategy_cycle", "strategy_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("market_cycles.id"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)

    portfolio_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gold_exposure: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fixed_income_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)

    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    total_return: Mapped[Decimal] = mapped_column(PCT, nullable=False, default=0)
    fees_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    turnover: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    drawdown: Mapped[Decimal] = mapped_column(PCT, nullable=False, default=0)

    active_positions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_funds: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyRuntimeState(Base):
    __tablename__ = "strategy_runtime_state"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "scope_key", "state_key",
            name="uq_strategy_state_scope_key"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False, default="GLOBAL")
    fund_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    state_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AssetReportStatus(Base):
    __tablename__ = "asset_report_status"
    __table_args__ = (
        UniqueConstraint(
            "fund_id", "expected_period_end",
            name="uq_asset_report_fund_period"
        ),
        Index("ix_asset_report_due", "reminder_start_date", "composition_updated"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)

    expected_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    expected_period_end_jalali: Mapped[str] = mapped_column(String(16), nullable=False)
    reminder_start_date: Mapped[date] = mapped_column(Date, nullable=False)

    report_received: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    report_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    composition_updated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    composition_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DataError(Base):
    __tablename__ = "data_errors"
    __table_args__ = (
        Index("ix_data_errors_source_time", "source", "occurred_at"),
        Index("ix_data_errors_cycle", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_cycles.id"))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id"))
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="ERROR")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notification_status_time", "status", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    cycle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_cycles.id"))
    strategy_id: Mapped[Optional[str]] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[Optional[str]] = mapped_column(String(256))
    message_hash: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(256))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class BotRun(Base):
    __tablename__ = "bot_runs"
    __table_args__ = (
        Index("ix_bot_runs_instance_started", "instance_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    host_name: Mapped[Optional[str]] = mapped_column(String(256))
    process_id: Mapped[Optional[int]] = mapped_column(Integer)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    version: Mapped[Optional[str]] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class LiveOrder(Base):
    """Real-broker Strategy A fills. Never mixed with paper transactions."""

    __tablename__ = "live_orders"
    __table_args__ = (
        UniqueConstraint("intent_key", name="uq_live_orders_intent_key"),
        Index("ix_live_orders_signal", "signal_id"),
        Index("ix_live_orders_status_time", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    intent_key: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"))
    cycle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_cycles.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    source_symbol: Mapped[Optional[str]] = mapped_column(String(64))
    target_symbol: Mapped[Optional[str]] = mapped_column(String(64))
    price: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    quantity: Mapped[Optional[Decimal]] = mapped_column(NUM)
    notional_rial: Mapped[Optional[Decimal]] = mapped_column(MONEY)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    broker_notification: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LiveAccountState(Base):
    """Singleton live book for Strategy A on the real Karamad account."""

    __tablename__ = "live_account_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_symbol: Mapped[Optional[str]] = mapped_column(String(64))
    current_units: Mapped[Decimal] = mapped_column(NUM, nullable=False, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    freeze_reason: Mapped[Optional[str]] = mapped_column(String(512))
    last_signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
