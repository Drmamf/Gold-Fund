from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
    StrategySignal,
)
from app.strategies.base import StrategyBase
from app.units import fraction_to_pct_points, pct_points_to_fraction


@dataclass(frozen=True)
class StrategyAConfig:
    strategy_id: str
    anchor_symbol: str
    initial_capital_irr: Decimal
    initial_allocation_pct: Decimal
    single_holding_only: bool
    min_net_edge_pct_points: Decimal
    min_net_edge_fraction: Decimal

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategyAConfig":
        with Path(path).open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}

        edge_pp = Decimal(
            str(payload["relative_rotation"]["min_net_executable_edge_pct_points"])
        )
        initial_pct = Decimal(str(payload["account"]["initial_allocation_pct"]))

        if initial_pct != Decimal("100"):
            raise ValueError(
                "Strategy A is a 100% single-holding strategy; "
                "initial_allocation_pct must be exactly 100."
            )
        if edge_pp < 0:
            raise ValueError("min_net_executable_edge_pct_points cannot be negative.")

        return cls(
            strategy_id=str(payload.get("strategy_id", "RELATIVE_BUY_HOLD")),
            anchor_symbol=str(payload.get("anchor_symbol", "عیار")),
            initial_capital_irr=Decimal(str(payload["account"]["initial_capital_irr"])),
            initial_allocation_pct=initial_pct,
            single_holding_only=bool(payload.get("single_holding_only", True)),
            min_net_edge_pct_points=edge_pp,
            min_net_edge_fraction=pct_points_to_fraction(edge_pp),
        )


class RelativeBuyHoldStrategy(StrategyBase):
    """
    Strategy A signal engine.

    The strategy has no threshold-entry logic, no MA7 entry, no per-fund cap,
    and no fixed-income parking. It always owns one gold fund and rotates the
    whole logical position only when the shared Relative Value engine reports
    a sufficiently large NET executable edge.

    Important separation of concerns:
      * Shared Relative Value engine decides market-relative opportunity.
      * Strategy A applies the policy threshold (default 0.50pp).
      * Executor performs the account-level sell/buy and persists state.
    """

    strategy_id = "RELATIVE_BUY_HOLD"

    def __init__(
        self,
        min_net_edge_pct_points: float | Decimal = Decimal("0.50"),
        *,
        anchor_symbol: str = "عیار",
    ):
        self.min_net_edge_pct_points = Decimal(str(min_net_edge_pct_points))
        self.min_net_edge_fraction = pct_points_to_fraction(
            self.min_net_edge_pct_points
        )
        self.anchor_symbol = anchor_symbol

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RelativeBuyHoldStrategy":
        cfg = StrategyAConfig.from_yaml(path)
        obj = cls(
            min_net_edge_pct_points=cfg.min_net_edge_pct_points,
            anchor_symbol=cfg.anchor_symbol,
        )
        obj.strategy_id = cfg.strategy_id
        return obj

    def generate_signals(
        self,
        *,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
        relative_rows: Mapping[int, RelativeValueRow],
        runtime_state: dict,
    ) -> Sequence[StrategySignal]:
        # Valuation-derived rotations fail closed when common pricing inputs are stale/invalid.
        if not common.valuation_inputs_usable:
            return []

        # Bootstrap is an account initialization event, not a market signal.
        current_fund_id = runtime_state.get("current_fund_id")
        if current_fund_id is None:
            return []

        try:
            current_fund_id = int(current_fund_id)
        except (TypeError, ValueError):
            return []

        source_market = funds.get(current_fund_id)
        source_valuation = valuations.get(current_fund_id)
        row = relative_rows.get(current_fund_id)

        # Fail closed on any missing/invalid shared input.
        if source_market is None or not source_market.data_valid:
            return []
        if source_valuation is None or not source_valuation.valid:
            return []
        if not row or not row.executable or row.best_target_fund_id is None:
            return []

        target_id = int(row.best_target_fund_id)
        if target_id == current_fund_id:
            return []

        target_market = funds.get(target_id)
        target_valuation = valuations.get(target_id)
        if target_market is None or not target_market.data_valid:
            return []
        if target_valuation is None or not target_valuation.valid:
            return []

        edge = row.net_executable_edge
        if edge is None or edge < self.min_net_edge_fraction:
            return []

        return [
            StrategySignal(
                strategy_id=self.strategy_id,
                engine="RELATIVE_VALUE",
                signal_type="ROTATE_TO",
                source_fund_id=current_fund_id,
                target_fund_id=target_id,
                signal_stage="ROTATION",
                relative_score=row.relative_score,
                gross_edge=row.gross_rotation_edge,
                spread_cost=row.spread_cost,
                fee_cost=row.fee_cost,
                net_executable_edge=row.net_executable_edge,
                payload={
                    "source_symbol": source_market.symbol,
                    "target_symbol": target_market.symbol,
                    "source_rank": row.rank,
                    "net_executable_edge_pct_points": float(
                        fraction_to_pct_points(edge)
                    ),
                    "min_required_pct_points": float(
                        self.min_net_edge_pct_points
                    ),
                    "single_holding_only": True,
                },
            )
        ]
