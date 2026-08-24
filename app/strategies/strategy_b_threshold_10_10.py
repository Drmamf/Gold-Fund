from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
    StrategySignal,
)
from app.strategies.base import StrategyBase
from app.strategies.strategy_b_entry_state import StrategyBEntryState
from app.units import fraction_to_pct_points, pct_points_to_fraction


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class StrategyBConfig:
    strategy_id: str
    threshold_entry_fraction: Decimal
    ma7_second_entry_fraction: Decimal
    buy_rearm_fraction: Decimal
    ma7_enabled: bool
    ma7_lookback_days: int

    max_total_gold_fraction: Decimal
    max_per_fund_fraction: Decimal
    allow_partial_entry: bool

    relative_min_edge_fraction: Decimal
    relative_min_edge_pct_points: Decimal
    cash_parking_symbol: str

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategyBConfig":
        with Path(path).open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        entry = raw["entry"]
        risk = raw["risk"]
        rel = raw["relative_overlay"]
        parking = raw.get("cash_parking", {})
        ma7 = entry.get("ma7_fallback_second_entry", {}) or {}

        threshold_pct = entry.get("threshold_entry_pct", entry.get("first_entry_pct", 10.0))
        ma7_pct = ma7.get("entry_pct", entry.get("second_entry_pct", threshold_pct))
        threshold_entry = Decimal(str(threshold_pct)) / Decimal("100")
        ma7_entry = Decimal(str(ma7_pct)) / Decimal("100")
        rearm = pct_points_to_fraction(entry["buy_rearm_margin_pct_points"])
        max_total = Decimal(str(risk["max_total_gold_exposure_pct"])) / Decimal("100")
        max_fund = Decimal(str(risk["max_per_fund_exposure_pct"])) / Decimal("100")
        edge_pp = Decimal(str(rel["min_net_executable_edge_pct_points"]))
        lookback = int(ma7.get("lookback_complete_trading_days", 7))

        if not (ZERO < threshold_entry <= ONE):
            raise ValueError("threshold_entry_pct must be in (0, 100].")
        if not (ZERO < ma7_entry <= ONE):
            raise ValueError("MA7 entry_pct must be in (0, 100].")
        if rearm <= ZERO:
            raise ValueError("buy_rearm_margin_pct_points must be positive.")
        if not (ZERO < max_fund <= max_total <= ONE):
            raise ValueError("Invalid Strategy B exposure caps.")
        if edge_pp < ZERO:
            raise ValueError("relative minimum edge cannot be negative.")
        if lookback < 1:
            raise ValueError("MA7 lookback must be >= 1.")

        return cls(
            strategy_id=str(raw.get("strategy_id", "THRESHOLD_10_10_RELATIVE")),
            threshold_entry_fraction=threshold_entry,
            ma7_second_entry_fraction=ma7_entry,
            buy_rearm_fraction=rearm,
            ma7_enabled=bool(ma7.get("enabled", True)),
            ma7_lookback_days=lookback,
            max_total_gold_fraction=max_total,
            max_per_fund_fraction=max_fund,
            allow_partial_entry=bool(risk.get("allow_partial_entry", False)),
            relative_min_edge_fraction=pct_points_to_fraction(edge_pp),
            relative_min_edge_pct_points=edge_pp,
            cash_parking_symbol=str(parking.get("symbol", "آفران")),
        )

    # Compatibility aliases used by some executor/setup code.
    @property
    def first_entry_fraction(self) -> Decimal:
        return self.threshold_entry_fraction

    @property
    def second_entry_fraction(self) -> Decimal:
        return self.ma7_second_entry_fraction


class Threshold1010RelativeStrategy(StrategyBase):
    """
    Final Strategy B decision engine.

    Priority per cycle:
        1. EXIT by CURRENT fund's sell threshold.
        2. Relative rotation for surviving tranches.
        3. Normal threshold Entry N (N = 1,2,3,...) only when the account-level
           +1.50pp rearm gate is open.
        4. MA7 fallback Entry #2 only if Entry #1 has NOT rearmed.

    Important:
        * Relative rotation NEVER creates new gold exposure.
        * MA7 NEVER creates Entry #3 or later.
        * Signals are account-independent and persisted before execution.
    """

    strategy_id = "THRESHOLD_10_10_RELATIVE"

    def __init__(
        self,
        *,
        threshold_entry_fraction: Decimal = Decimal("0.10"),
        ma7_second_entry_fraction: Decimal = Decimal("0.10"),
        buy_rearm_fraction: Decimal = Decimal("0.015"),
        relative_min_edge_fraction: Decimal = Decimal("0.005"),
        ma7_enabled: bool = True,
        ma7_lookback_days: int = 7,
    ):
        self.threshold_entry_fraction = Decimal(str(threshold_entry_fraction))
        self.ma7_second_entry_fraction = Decimal(str(ma7_second_entry_fraction))
        self.buy_rearm_fraction = Decimal(str(buy_rearm_fraction))
        self.relative_min_edge_fraction = Decimal(str(relative_min_edge_fraction))
        self.ma7_enabled = bool(ma7_enabled)
        self.ma7_lookback_days = int(ma7_lookback_days)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Threshold1010RelativeStrategy":
        cfg = StrategyBConfig.from_yaml(path)
        obj = cls(
            threshold_entry_fraction=cfg.threshold_entry_fraction,
            ma7_second_entry_fraction=cfg.ma7_second_entry_fraction,
            buy_rearm_fraction=cfg.buy_rearm_fraction,
            relative_min_edge_fraction=cfg.relative_min_edge_fraction,
            ma7_enabled=cfg.ma7_enabled,
            ma7_lookback_days=cfg.ma7_lookback_days,
        )
        obj.strategy_id = cfg.strategy_id
        return obj

    @staticmethod
    def _d(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def _market_eligible(cls, snap: FundSnapshot | None) -> bool:
        """
        Eligibility for valuation/threshold SIGNAL generation.

        STRICT project rule: the only valid market price is Best Ask, because
        that is the cheapest price currently available to buy. Best Bid is not
        required here; it is checked later by the Relative engine / Executor
        when an actual sell is needed.
        """
        if snap is None or not snap.data_valid:
            return False
        ask = cls._d(snap.best_ask)
        signal_price = cls._d(snap.signal_price)
        trade_value = cls._d(snap.trade_value)

        if ask is None or ask <= ZERO:
            return False
        if signal_price is None or signal_price != ask:
            return False
        if trade_value is None or trade_value <= ZERO:
            return False
        if int(snap.trade_count or 0) <= 0:
            return False
        return True

    @staticmethod
    def _market_date(common: CommonSnapshot, runtime_state: dict) -> date:
        value = runtime_state.get("market_date")
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if value:
            try:
                return date.fromisoformat(str(value)[:10])
            except Exception:
                pass
        return common.collected_at.date()

    def _effective_entry_state(
        self,
        *,
        runtime_state: dict,
        valuations: Mapping[int, FundValuation],
        common: CommonSnapshot,
    ) -> StrategyBEntryState:
        state = StrategyBEntryState.from_mapping(runtime_state)
        ref_id = state.rearm_reference_fund_id
        if state.threshold_gate_open or ref_id is None:
            return state
        valuation = valuations.get(int(ref_id))
        if valuation is None or not valuation.valid:
            return state
        return state.preview_rearm(
            current_total_bubble=valuation.total_bubble,
            achieved_at=common.collected_at.isoformat(),
        )

    def _rotation_candidates(
        self,
        *,
        source_id: int,
        row: RelativeValueRow,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> list[dict[str, Any]]:
        symbol_to_id = {snap.symbol: int(fid) for fid, snap in funds.items()}
        candidates: list[dict[str, Any]] = []
        pairwise = dict((row.details or {}).get("pairwise_candidates") or {})

        for target_symbol, detail in pairwise.items():
            target_id = symbol_to_id.get(str(target_symbol))
            if target_id is None or target_id == source_id:
                continue
            target_market = funds.get(target_id)
            target_val = valuations.get(target_id)
            if not self._market_eligible(target_market):
                continue
            if target_val is None or not target_val.valid:
                continue
            if not bool(detail.get("executable", False)):
                continue
            edge = self._d(detail.get("net_executable_edge"))
            if edge is None or edge < self.relative_min_edge_fraction:
                continue
            candidates.append(
                {
                    "target_fund_id": target_id,
                    "target_symbol": target_symbol,
                    "gross_edge": detail.get("gross_edge"),
                    "spread_cost": detail.get("spread_cost"),
                    "fee_cost": detail.get("fee_cost"),
                    "total_switch_cost": detail.get("total_switch_cost"),
                    "net_executable_edge": str(edge),
                    "net_executable_edge_pct_points": str(fraction_to_pct_points(edge)),
                }
            )

        if not candidates and row.best_target_fund_id is not None:
            target_id = int(row.best_target_fund_id)
            edge = row.net_executable_edge
            if (
                target_id != source_id
                and edge is not None
                and edge >= self.relative_min_edge_fraction
                and self._market_eligible(funds.get(target_id))
                and valuations.get(target_id) is not None
                and valuations[target_id].valid
            ):
                candidates.append(
                    {
                        "target_fund_id": target_id,
                        "target_symbol": funds[target_id].symbol,
                        "gross_edge": str(row.gross_rotation_edge) if row.gross_rotation_edge is not None else None,
                        "spread_cost": str(row.spread_cost) if row.spread_cost is not None else None,
                        "fee_cost": str(row.fee_cost) if row.fee_cost is not None else None,
                        "total_switch_cost": None,
                        "net_executable_edge": str(edge),
                        "net_executable_edge_pct_points": str(fraction_to_pct_points(edge)),
                    }
                )

        candidates.sort(key=lambda c: Decimal(str(c["net_executable_edge"])), reverse=True)
        return candidates

    def generate_signals(
        self,
        *,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
        relative_rows: Mapping[int, RelativeValueRow],
        runtime_state: dict,
    ) -> Sequence[StrategySignal]:
        if not common.valuation_inputs_usable:
            return []

        signals: list[StrategySignal] = []
        per_fund_state = runtime_state.get("funds", {}) or {}
        open_positions = list(runtime_state.get("open_positions", []) or [])
        entry_state = self._effective_entry_state(
            runtime_state=runtime_state,
            valuations=valuations,
            common=common,
        )
        market_date = self._market_date(common, runtime_state)

        # ---------------------------------------------------------------
        # 1) EXIT: current fund's sell threshold always wins.
        # ---------------------------------------------------------------
        exit_position_ids: set[int] = set()
        for pos in open_positions:
            try:
                position_id = int(pos["position_id"])
                fund_id = int(pos["current_fund_id"])
            except Exception:
                continue
            valuation = valuations.get(fund_id)
            market = funds.get(fund_id)
            if valuation is None or not valuation.valid or not self._market_eligible(market):
                continue
            if valuation.total_bubble is None or valuation.sell_threshold is None:
                continue
            if valuation.total_bubble >= valuation.sell_threshold:
                exit_position_ids.add(position_id)
                signals.append(
                    StrategySignal(
                        strategy_id=self.strategy_id,
                        engine="THRESHOLD",
                        signal_type="THRESHOLD_SELL",
                        fund_id=fund_id,
                        source_fund_id=fund_id,
                        signal_stage="EXIT",
                        nominal_bubble=valuation.nominal_bubble,
                        intrinsic_bubble=valuation.intrinsic_bubble,
                        total_bubble=valuation.total_bubble,
                        payload={
                            "position_id": position_id,
                            "current_fund_id": fund_id,
                            "sell_threshold": str(valuation.sell_threshold),
                            "exit_rule": "CURRENT_FUND_SELL_THRESHOLD",
                            "priority": 1,
                        },
                    )
                )

        # ---------------------------------------------------------------
        # 2) RELATIVE ROTATION: only positions not exiting.
        # ---------------------------------------------------------------
        for pos in open_positions:
            try:
                position_id = int(pos["position_id"])
                source_id = int(pos["current_fund_id"])
            except Exception:
                continue
            if position_id in exit_position_ids:
                continue
            source_val = valuations.get(source_id)
            source_market = funds.get(source_id)
            row = relative_rows.get(source_id)
            if source_val is None or not source_val.valid:
                continue
            if not self._market_eligible(source_market):
                continue
            if row is None or row.relative_score is None:
                continue
            candidates = self._rotation_candidates(
                source_id=source_id,
                row=row,
                funds=funds,
                valuations=valuations,
            )
            if not candidates:
                continue
            best = candidates[0]
            best_target = int(best["target_fund_id"])
            best_edge = Decimal(str(best["net_executable_edge"]))
            signals.append(
                StrategySignal(
                    strategy_id=self.strategy_id,
                    engine="RELATIVE_OVERLAY",
                    signal_type="ROTATE_TO",
                    source_fund_id=source_id,
                    target_fund_id=best_target,
                    signal_stage="ROTATION",
                    relative_score=row.relative_score,
                    gross_edge=self._d(best.get("gross_edge")),
                    spread_cost=self._d(best.get("spread_cost")),
                    fee_cost=self._d(best.get("fee_cost")),
                    net_executable_edge=best_edge,
                    payload={
                        "position_id": position_id,
                        "origin_entry_type": pos.get("origin_entry_type"),
                        "best_market_target_fund_id": best_target,
                        "best_market_target_symbol": best.get("target_symbol"),
                        "candidate_targets": candidates,
                        "min_required_edge": str(self.relative_min_edge_fraction),
                        "min_required_edge_pct_points": str(
                            fraction_to_pct_points(self.relative_min_edge_fraction)
                        ),
                        "priority": 2,
                    },
                )
            )

        # ---------------------------------------------------------------
        # 3) NORMAL THRESHOLD ENTRY N.
        #    Only after the previous executed entry's +1.50pp rearm.
        # ---------------------------------------------------------------
        if entry_state.threshold_gate_open:
            next_number = entry_state.next_entry_number
            for fund_id, valuation in valuations.items():
                fund_id = int(fund_id)
                market = funds.get(fund_id)
                if valuation is None or not valuation.valid or not self._market_eligible(market):
                    continue
                if valuation.total_bubble is None or valuation.buy_threshold is None:
                    continue
                if valuation.total_bubble > valuation.buy_threshold:
                    continue

                fund_state = per_fund_state.get(str(fund_id), {}) or {}
                if str(fund_state.get("buy_state", "READY")).upper() == "LOCKED":
                    continue

                signals.append(
                    StrategySignal(
                        strategy_id=self.strategy_id,
                        engine="THRESHOLD",
                        signal_type="THRESHOLD_BUY",
                        fund_id=fund_id,
                        target_fund_id=fund_id,
                        signal_stage=f"ENTRY_{next_number}",
                        nominal_bubble=valuation.nominal_bubble,
                        intrinsic_bubble=valuation.intrinsic_bubble,
                        total_bubble=valuation.total_bubble,
                        payload={
                            "entry_number": next_number,
                            "entry_route": "THRESHOLD_REARM",
                            "allocation_fraction": str(self.threshold_entry_fraction),
                            "allocation_pct": str(self.threshold_entry_fraction * 100),
                            "buy_threshold": str(valuation.buy_threshold),
                            "rearm_threshold": str(
                                valuation.buy_threshold + self.buy_rearm_fraction
                            ),
                            "account_threshold_gate_open": True,
                            "previous_rearm_reference_fund_id": entry_state.rearm_reference_fund_id,
                            "last_rearm_achieved_at": entry_state.last_rearm_achieved_at,
                            "priority": 3,
                        },
                    )
                )

        # ---------------------------------------------------------------
        # 4) MA7 FALLBACK -- Entry #2 ONLY.
        #    Used only when Entry #1 has NOT achieved its +1.50pp rearm.
        # ---------------------------------------------------------------
        if self.ma7_enabled and entry_state.ma7_fallback_allowed(market_date):
            ma7 = runtime_state.get("ma7", {}) or {}
            previous_avg = self._d(ma7.get("previous_average_trade_value"))
            if previous_avg is None:
                previous_avg = self._d(ma7.get("previous_7d_average_trade_value"))
            history_days = int(ma7.get("history_days_available", 0) or 0)

            current_total_trade_value = ZERO
            for fund_id in valuations:
                snap = funds.get(int(fund_id))
                if snap is None:
                    continue
                value = self._d(snap.trade_value)
                if value is not None and value > ZERO:
                    current_total_trade_value += value

            ma7_confirmed = bool(
                previous_avg is not None
                and previous_avg > ZERO
                and history_days >= self.ma7_lookback_days
                and current_total_trade_value > previous_avg
            )

            if ma7_confirmed:
                candidates: list[dict[str, Any]] = []
                for fund_id, valuation in valuations.items():
                    fund_id = int(fund_id)
                    market = funds.get(fund_id)
                    if valuation is None or not valuation.valid:
                        continue
                    if valuation.total_bubble is None or valuation.buy_threshold is None:
                        continue
                    if not self._market_eligible(market):
                        continue
                    candidates.append(
                        {
                            "fund_id": fund_id,
                            "symbol": market.symbol if market else None,
                            "total_bubble": str(valuation.total_bubble),
                            "total_bubble_pct_points": str(
                                fraction_to_pct_points(valuation.total_bubble)
                            ),
                            "buy_threshold": str(valuation.buy_threshold),
                        }
                    )
                candidates.sort(key=lambda c: Decimal(str(c["total_bubble"])))

                if candidates:
                    best = candidates[0]
                    best_id = int(best["fund_id"])
                    best_val = valuations[best_id]
                    signals.append(
                        StrategySignal(
                            strategy_id=self.strategy_id,
                            engine="MA7_FALLBACK",
                            signal_type="MA7_FALLBACK_BUY_2",
                            fund_id=best_id,
                            target_fund_id=best_id,
                            signal_stage="ENTRY_2",
                            nominal_bubble=best_val.nominal_bubble,
                            intrinsic_bubble=best_val.intrinsic_bubble,
                            total_bubble=best_val.total_bubble,
                            payload={
                                "entry_number": 2,
                                "entry_route": "MA7_FALLBACK",
                                "allocation_fraction": str(self.ma7_second_entry_fraction),
                                "allocation_pct": str(self.ma7_second_entry_fraction * 100),
                                "candidate_funds_by_total_bubble": candidates,
                                "current_total_trade_value": str(current_total_trade_value),
                                "previous_7d_average_trade_value": str(previous_avg),
                                "history_days_available": history_days,
                                "history_days_used": ma7.get("history_days_used", []),
                                "ma7_primary_position_id": entry_state.ma7_primary_position_id,
                                "ma7_primary_fund_id": entry_state.ma7_primary_fund_id,
                                "ma7_fallback_since_date": (
                                    entry_state.ma7_fallback_since_date.isoformat()
                                    if entry_state.ma7_fallback_since_date else None
                                ),
                                "rearm_was_not_achieved": True,
                                "priority": 4,
                            },
                        )
                    )

        return signals
