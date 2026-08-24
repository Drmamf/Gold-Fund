from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from app.contracts import FundSnapshot, FundValuation, RelativeValueRow
from app.units import pct_points_to_fraction


ZERO = Decimal("0")
ONE = Decimal("1")


def _d(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite():
        return None
    return result


def _positive(value: Any) -> Optional[Decimal]:
    result = _d(value)
    return result if result is not None and result > ZERO else None


@dataclass(frozen=True)
class RelativeValueConfig:
    anchor_symbol: str
    normal_gap_by_symbol: Mapping[str, Decimal]

    sell_fee_rate: Decimal
    buy_fee_rate: Decimal

    require_valid_market_snapshot: bool = True
    require_positive_trade_activity: bool = True
    require_two_sided_order_book: bool = True
    keep_pairwise_candidates: bool = True

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RelativeValueConfig":
        with Path(path).open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}

        cfg = payload["relative_value"]
        baseline = cfg["historical_baseline"]["normal_gap_pct_points"]
        costs = cfg.get("execution_costs", {})
        quality = cfg.get("market_quality", {})
        diagnostics = cfg.get("diagnostics", {})

        gaps = {
            str(symbol): pct_points_to_fraction(value)
            for symbol, value in baseline.items()
        }

        sell_fee = Decimal(str(costs.get("sell_fee_rate", "0.00125")))
        buy_fee = Decimal(str(costs.get("buy_fee_rate", "0.00125")))

        if not (ZERO <= sell_fee < ONE):
            raise ValueError("sell_fee_rate must be in [0, 1).")
        if not (ZERO <= buy_fee < ONE):
            raise ValueError("buy_fee_rate must be in [0, 1).")

        return cls(
            anchor_symbol=str(cfg.get("anchor_symbol", "عیار")),
            normal_gap_by_symbol=gaps,
            sell_fee_rate=sell_fee,
            buy_fee_rate=buy_fee,
            require_valid_market_snapshot=bool(
                quality.get("require_valid_market_snapshot", True)
            ),
            require_positive_trade_activity=bool(
                quality.get("require_positive_trade_activity", True)
            ),
            require_two_sided_order_book=bool(
                quality.get("require_two_sided_order_book", True)
            ),
            keep_pairwise_candidates=bool(
                diagnostics.get("keep_pairwise_candidates", True)
            ),
        )


@dataclass(frozen=True)
class PairCost:
    source_reference_price: Decimal
    target_reference_price: Decimal
    source_bid: Decimal
    target_ask: Decimal

    spread_cost: Decimal
    fee_cost: Decimal
    total_switch_cost: Decimal


class SharedRelativeValueEngine:
    """
    Account-independent shared Relative Value engine.

    Core definitions
    ----------------
    For fund i, with Ayyar as the normalization anchor:

        current_gap_i
            = total_bubble_ayyar - total_bubble_i

        relative_score_i
            = current_gap_i - historical_normal_gap_i

    Interpretation:
        score > 0  -> fund is cheaper than its normal relationship to Ayyar
        score = 0  -> around normal
        score < 0  -> fund is expensive vs its normal relationship to Ayyar

    For a currently-held source A and candidate target B:

        gross_rotation_edge(A -> B)
            = score_B - score_A

    The engine evaluates every direct pair A -> B, not only Ayyar paths.

    It then subtracts the executable one-way switching drag using:
        - source best bid
        - target best ask
        - source/target valuation reference = Best Ask
        - sell fee
        - buy fee

        net_executable_edge
            = gross_rotation_edge - total_switch_cost

    Strategy A/B decide their own minimum required edge (e.g. +0.50pp).
    This engine only computes the market-relative opportunity.
    """

    def __init__(self, config: RelativeValueConfig):
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SharedRelativeValueEngine":
        return cls(RelativeValueConfig.from_yaml(path))

    def _find_anchor_id(
        self,
        funds: Mapping[int, FundSnapshot],
    ) -> int:
        for fund_id, snapshot in funds.items():
            if snapshot.symbol == self.config.anchor_symbol:
                return fund_id
        raise ValueError(
            f"Relative Value anchor {self.config.anchor_symbol!r} "
            "is missing from fund snapshots."
        )

    def _market_eligible(self, snapshot: FundSnapshot) -> bool:
        if self.config.require_valid_market_snapshot and not snapshot.data_valid:
            return False

        if self.config.require_positive_trade_activity:
            trade_value = _d(snapshot.trade_value)
            trade_count = snapshot.trade_count
            if trade_value is None or trade_value <= ZERO:
                return False
            if trade_count is None or int(trade_count) <= 0:
                return False

        if self.config.require_two_sided_order_book:
            if _positive(snapshot.best_bid) is None:
                return False
            if _positive(snapshot.best_ask) is None:
                return False
            if _positive(snapshot.best_ask) < _positive(snapshot.best_bid):
                return False

        return True

    @staticmethod
    def _valuation_eligible(valuation: Optional[FundValuation]) -> bool:
        if valuation is None or not valuation.valid:
            return False
        total = _d(valuation.total_bubble)
        if total is None:
            return False
        # A total bubble <= -100% is mathematically invalid.
        return total > Decimal("-1")

    @staticmethod
    def _reference_price(snapshot: FundSnapshot) -> Optional[Decimal]:
        # STRICT: Relative valuation reference is Best Ask only, supplied as
        # signal_price by SharedMarketCollector. No midpoint/last/close fallback.
        explicit = _positive(snapshot.signal_price)
        ask = _positive(snapshot.best_ask)
        if explicit is None or ask is None:
            return None

        # Reject any accidental future wiring that sends a non-ask signal price.
        tolerance = max(Decimal("0.00000001"), ask * Decimal("0.000000001"))
        if abs(explicit - ask) > tolerance:
            return None
        return ask

    def _pair_cost(
        self,
        source: FundSnapshot,
        target: FundSnapshot,
    ) -> Optional[PairCost]:
        source_bid = _positive(source.best_bid)
        target_ask = _positive(target.best_ask)
        source_ref = self._reference_price(source)
        target_ref = self._reference_price(target)

        if None in (source_bid, target_ask, source_ref, target_ref):
            return None

        assert source_bid is not None
        assert target_ask is not None
        assert source_ref is not None
        assert target_ref is not None

        if source_bid > source_ref:
            # A stale/custom signal price should never create a negative spread.
            source_ref = max(
                source_ref,
                (source_bid + (_positive(source.best_ask) or source_bid))
                / Decimal("2"),
            )

        if target_ask < target_ref:
            target_ref = min(
                target_ref,
                ((_positive(target.best_bid) or target_ask) + target_ask)
                / Decimal("2"),
            )

        source_execution_factor = min(ONE, source_bid / source_ref)
        target_execution_factor = min(ONE, target_ref / target_ask)

        spread_survival = source_execution_factor * target_execution_factor
        spread_cost = max(ZERO, ONE - spread_survival)

        # Sell: proceeds * (1 - sell_fee)
        # Buy:  units = cash / [ask * (1 + buy_fee)]
        fee_survival = (
            (ONE - self.config.sell_fee_rate)
            / (ONE + self.config.buy_fee_rate)
        )
        fee_cost = max(ZERO, ONE - fee_survival)

        total_survival = spread_survival * fee_survival
        total_switch_cost = max(ZERO, ONE - total_survival)

        return PairCost(
            source_reference_price=source_ref,
            target_reference_price=target_ref,
            source_bid=source_bid,
            target_ask=target_ask,
            spread_cost=spread_cost,
            fee_cost=fee_cost,
            total_switch_cost=total_switch_cost,
        )

    def calculate(
        self,
        common,  # kept for the shared pipeline protocol; no refetch is allowed
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> Mapping[int, RelativeValueRow]:
        anchor_id = self._find_anchor_id(funds)
        anchor_valuation = valuations.get(anchor_id)

        if not self._valuation_eligible(anchor_valuation):
            # Fail closed: without a valid anchor we cannot normalize the market.
            return {
                fund_id: RelativeValueRow(
                    fund_id=fund_id,
                    anchor_fund_id=anchor_id,
                    current_gap=None,
                    historical_normal_gap=None,
                    relative_score=None,
                    rank=None,
                    best_target_fund_id=None,
                    gross_rotation_edge=None,
                    spread_cost=None,
                    fee_cost=None,
                    net_executable_edge=None,
                    executable=False,
                    details={"reason": "INVALID_ANCHOR_VALUATION"},
                )
                for fund_id in funds
            }

        assert anchor_valuation is not None
        anchor_total = _d(anchor_valuation.total_bubble)
        assert anchor_total is not None

        # ---------- Step 1: cross-sectional scores ----------
        current_gap: dict[int, Decimal] = {}
        normal_gap: dict[int, Decimal] = {}
        score: dict[int, Decimal] = {}
        unavailable_reason: dict[int, str] = {}

        for fund_id, snapshot in funds.items():
            valuation = valuations.get(fund_id)

            if not self._valuation_eligible(valuation):
                unavailable_reason[fund_id] = "INVALID_FUND_VALUATION"
                continue

            if snapshot.symbol not in self.config.normal_gap_by_symbol:
                unavailable_reason[fund_id] = "MISSING_HISTORICAL_NORMAL_GAP"
                continue

            total = _d(valuation.total_bubble)
            assert total is not None

            observed = anchor_total - total
            baseline = self.config.normal_gap_by_symbol[snapshot.symbol]

            current_gap[fund_id] = observed
            normal_gap[fund_id] = baseline
            score[fund_id] = observed - baseline

        # Ayyar should be exactly zero by construction, independent of tiny
        # configuration noise.
        if anchor_id in score:
            current_gap[anchor_id] = ZERO
            normal_gap[anchor_id] = ZERO
            score[anchor_id] = ZERO

        ranked_ids = sorted(
            score.keys(),
            key=lambda fid: (score[fid], -fid),
            reverse=True,
        )
        rank_by_id = {
            fund_id: rank
            for rank, fund_id in enumerate(ranked_ids, start=1)
        }

        # ---------- Step 2: best executable target for EACH source ----------
        rows: dict[int, RelativeValueRow] = {}

        for source_id, source_snapshot in funds.items():
            source_score = score.get(source_id)

            if source_score is None:
                rows[source_id] = RelativeValueRow(
                    fund_id=source_id,
                    anchor_fund_id=anchor_id,
                    current_gap=current_gap.get(source_id),
                    historical_normal_gap=normal_gap.get(source_id),
                    relative_score=None,
                    rank=None,
                    best_target_fund_id=None,
                    gross_rotation_edge=None,
                    spread_cost=None,
                    fee_cost=None,
                    net_executable_edge=None,
                    executable=False,
                    details={
                        "reason": unavailable_reason.get(
                            source_id, "SOURCE_SCORE_UNAVAILABLE"
                        )
                    },
                )
                continue

            pairwise: dict[str, Any] = {}
            best: Optional[dict[str, Any]] = None

            # If the held source itself is not tradeable now, it cannot be sold.
            source_market_ok = self._market_eligible(source_snapshot)

            for target_id, target_snapshot in funds.items():
                if target_id == source_id:
                    continue

                target_score = score.get(target_id)
                if target_score is None:
                    continue

                gross_edge = target_score - source_score

                # A target with no relative improvement should never be chosen.
                if gross_edge <= ZERO:
                    continue

                target_market_ok = self._market_eligible(target_snapshot)

                pair_record: dict[str, Any] = {
                    "source_symbol": source_snapshot.symbol,
                    "target_symbol": target_snapshot.symbol,
                    "gross_edge": str(gross_edge),
                    "source_market_ok": source_market_ok,
                    "target_market_ok": target_market_ok,
                }

                if not source_market_ok or not target_market_ok:
                    pair_record["executable"] = False
                    pair_record["reason"] = "MARKET_QUALITY_GATE"
                    if self.config.keep_pairwise_candidates:
                        pairwise[target_snapshot.symbol] = pair_record
                    continue

                cost = self._pair_cost(source_snapshot, target_snapshot)
                if cost is None:
                    pair_record["executable"] = False
                    pair_record["reason"] = "EXECUTION_PRICE_UNAVAILABLE"
                    if self.config.keep_pairwise_candidates:
                        pairwise[target_snapshot.symbol] = pair_record
                    continue

                net_edge = gross_edge - cost.total_switch_cost

                pair_record.update(
                    {
                        "spread_cost": str(cost.spread_cost),
                        "fee_cost": str(cost.fee_cost),
                        "total_switch_cost": str(cost.total_switch_cost),
                        "net_executable_edge": str(net_edge),
                        "source_reference_price": str(
                            cost.source_reference_price
                        ),
                        "source_bid": str(cost.source_bid),
                        "target_reference_price": str(
                            cost.target_reference_price
                        ),
                        "target_ask": str(cost.target_ask),
                        "executable": net_edge > ZERO,
                    }
                )

                if self.config.keep_pairwise_candidates:
                    pairwise[target_snapshot.symbol] = pair_record

                candidate = {
                    "target_id": target_id,
                    "target_symbol": target_snapshot.symbol,
                    "gross_edge": gross_edge,
                    "spread_cost": cost.spread_cost,
                    "fee_cost": cost.fee_cost,
                    "total_switch_cost": cost.total_switch_cost,
                    "net_edge": net_edge,
                }

                # Choose the target by NET executable edge, not raw score.
                if best is None or candidate["net_edge"] > best["net_edge"]:
                    best = candidate

            if best is None:
                rows[source_id] = RelativeValueRow(
                    fund_id=source_id,
                    anchor_fund_id=anchor_id,
                    current_gap=current_gap[source_id],
                    historical_normal_gap=normal_gap[source_id],
                    relative_score=source_score,
                    rank=rank_by_id[source_id],
                    best_target_fund_id=None,
                    gross_rotation_edge=None,
                    spread_cost=None,
                    fee_cost=None,
                    net_executable_edge=None,
                    executable=False,
                    details={
                        "pairwise_candidates": pairwise,
                        "reason": (
                            "SOURCE_MARKET_NOT_EXECUTABLE"
                            if not source_market_ok
                            else "NO_BETTER_TARGET"
                        ),
                    },
                )
                continue

            rows[source_id] = RelativeValueRow(
                fund_id=source_id,
                anchor_fund_id=anchor_id,
                current_gap=current_gap[source_id],
                historical_normal_gap=normal_gap[source_id],
                relative_score=source_score,
                rank=rank_by_id[source_id],
                best_target_fund_id=int(best["target_id"]),
                gross_rotation_edge=best["gross_edge"],
                spread_cost=best["spread_cost"],
                fee_cost=best["fee_cost"],
                net_executable_edge=best["net_edge"],
                # "Executable" here means technically positive after direct
                # bid/ask + fees. Strategies still apply e.g. +0.50pp margin.
                executable=best["net_edge"] > ZERO,
                details={
                    "best_target_symbol": best["target_symbol"],
                    "total_switch_cost": str(best["total_switch_cost"]),
                    "pairwise_candidates": pairwise,
                },
            )

        return rows
