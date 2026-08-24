from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class CommonSnapshot:
    collected_at: datetime

    # Raw common-market valuation inputs. Missing/invalid values remain None;
    # they are NEVER replaced with stale/alternate-source fallbacks.
    usd_irr: Optional[Decimal]
    ounce_usd: Optional[Decimal]

    # STRICT: IME valuation prices are Best Ask only.
    ime_bullion_price: Optional[Decimal]
    ime_coin_price: Optional[Decimal]

    # Filled by the common Valuation Engine later; Collector leaves these None.
    bullion_bubble: Optional[Decimal]
    coin_bubble: Optional[Decimal]

    valuation_inputs_usable: bool
    raw: dict[str, Any] = field(default_factory=dict)

    # Derived by SharedValuationEngine, never by Collector.
    pure_gold_irr_per_gram: Optional[Decimal] = None
    fair_bullion_price: Optional[Decimal] = None
    fair_coin_price: Optional[Decimal] = None


@dataclass(frozen=True)
class FundSnapshot:
    fund_id: int
    symbol: str
    close_price: Decimal
    nav_redemption: Decimal
    best_bid: Decimal
    best_ask: Decimal
    trade_value: Decimal
    trade_count: int
    data_valid: bool

    # STRICT PROJECT RULE:
    # signal_price is the cheapest current seller (Best Ask) only.
    # No midpoint / last / close fallback is allowed for valuation/signals.
    signal_price: Optional[Decimal] = None

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FundValuation:
    fund_id: int
    nominal_bubble: Optional[Decimal]
    intrinsic_bubble: Optional[Decimal]
    total_bubble: Optional[Decimal]
    buy_threshold: Optional[Decimal]
    sell_threshold: Optional[Decimal]
    valid: bool

    asset_composition_id: Optional[int] = None
    bullion_weight: Optional[Decimal] = None
    coin_weight: Optional[Decimal] = None
    fair_nav_factor: Optional[Decimal] = None


@dataclass(frozen=True)
class ValuationBatch:
    common: CommonSnapshot
    funds: Mapping[int, FundValuation]


@dataclass(frozen=True)
class RelativeValueRow:
    fund_id: int
    anchor_fund_id: int

    # All PCT-like values are stored as decimal fractions:
    # 0.005 = 0.50 percentage points.
    current_gap: Optional[Decimal]
    historical_normal_gap: Optional[Decimal]
    relative_score: Optional[Decimal]
    rank: Optional[int]

    best_target_fund_id: Optional[int]
    gross_rotation_edge: Optional[Decimal]
    spread_cost: Optional[Decimal]
    fee_cost: Optional[Decimal]
    net_executable_edge: Optional[Decimal]
    executable: bool

    # Optional full source->target diagnostics for audit/debug.
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategySignal:
    strategy_id: str
    engine: str
    signal_type: str
    fund_id: Optional[int] = None
    source_fund_id: Optional[int] = None
    target_fund_id: Optional[int] = None
    signal_stage: Optional[str] = None

    # Snapshot values copied onto the signal at generation time so later
    # execution/account constraints never erase the market reason for it.
    nominal_bubble: Optional[Decimal] = None
    intrinsic_bubble: Optional[Decimal] = None
    total_bubble: Optional[Decimal] = None
    relative_score: Optional[Decimal] = None
    gross_edge: Optional[Decimal] = None
    spread_cost: Optional[Decimal] = None
    fee_cost: Optional[Decimal] = None
    net_executable_edge: Optional[Decimal] = None

    payload: dict[str, Any] = field(default_factory=dict)
