from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import CommonSnapshot, FundSnapshot, FundValuation
from app.database import SessionLocal
from app.execution.strategy_b_math import StrategyBExecutionMath
from app.models import (
    AccountSnapshot,
    Instrument,
    PositionCurrent,
    PositionEvent,
    Signal,
    StrategyRuntimeState,
    Transaction,
)
from app.strategies.strategy_b_threshold_10_10 import StrategyBConfig
from app.strategies.strategy_b_entry_state import StrategyBEntryState


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class StrategyBExecutionConfig:
    strategy_id: str
    initial_capital_irr: Decimal
    threshold_entry_fraction: Decimal
    ma7_second_entry_fraction: Decimal
    min_entry_cash_irr: Decimal

    max_total_gold_fraction: Decimal
    max_per_fund_fraction: Decimal
    allow_partial_entry: bool

    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    gold_unit_step: Decimal

    fixed_income_enabled: bool
    fixed_income_symbol: str
    fixed_income_buy_fee_rate: Decimal
    fixed_income_sell_fee_rate: Decimal
    fixed_income_unit_step: Decimal
    fixed_income_min_sweep_cash: Decimal
    fixed_income_keep_cash_buffer: Decimal

    buy_rearm_fraction: Decimal

    @classmethod
    def from_yaml(
        cls,
        strategy_path: str | Path,
        relative_path: str | Path,
    ) -> "StrategyBExecutionConfig":
        strategy_cfg = StrategyBConfig.from_yaml(strategy_path)
        with Path(strategy_path).open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        with Path(relative_path).open("r", encoding="utf-8") as fh:
            rel = (yaml.safe_load(fh) or {})["relative_value"]

        account = raw.get("account", {})
        entry = raw["entry"]
        execution = raw.get("execution", {})
        parking = raw.get("cash_parking", {})
        costs = rel["execution_costs"]

        initial = Decimal(str(account.get("initial_capital_irr", "1000000000")))
        min_entry = Decimal(str(entry.get("min_entry_cash_irr", "1000000")))
        buy_fee = Decimal(str(costs["buy_fee_rate"]))
        sell_fee = Decimal(str(costs["sell_fee_rate"]))
        gold_step = Decimal(str(execution.get("unit_step", "1")))

        fi_buy_fee = Decimal(str(parking.get("buy_fee_rate", "0.00075")))
        fi_sell_fee = Decimal(str(parking.get("sell_fee_rate", "0.00075")))
        fi_step = Decimal(str(parking.get("unit_step", "1")))
        fi_min_sweep = Decimal(str(parking.get("min_sweep_cash_irr", "1000000")))
        fi_buffer = Decimal(str(parking.get("keep_cash_buffer_irr", "0")))

        for name, rate in {
            "buy_fee_rate": buy_fee,
            "sell_fee_rate": sell_fee,
            "fixed_income_buy_fee_rate": fi_buy_fee,
            "fixed_income_sell_fee_rate": fi_sell_fee,
        }.items():
            if not (ZERO <= rate < ONE):
                raise ValueError(f"{name} must be in [0,1).")
        if initial <= ZERO or min_entry < ZERO:
            raise ValueError("Strategy B capital settings are invalid.")
        if gold_step <= ZERO or fi_step <= ZERO:
            raise ValueError("unit steps must be positive.")

        return cls(
            strategy_id=strategy_cfg.strategy_id,
            initial_capital_irr=initial,
            threshold_entry_fraction=strategy_cfg.threshold_entry_fraction,
            ma7_second_entry_fraction=strategy_cfg.ma7_second_entry_fraction,
            min_entry_cash_irr=min_entry,
            max_total_gold_fraction=strategy_cfg.max_total_gold_fraction,
            max_per_fund_fraction=strategy_cfg.max_per_fund_fraction,
            allow_partial_entry=strategy_cfg.allow_partial_entry,
            buy_fee_rate=buy_fee,
            sell_fee_rate=sell_fee,
            gold_unit_step=gold_step,
            fixed_income_enabled=bool(parking.get("enabled", True)),
            fixed_income_symbol=str(parking.get("symbol", "آفران")),
            fixed_income_buy_fee_rate=fi_buy_fee,
            fixed_income_sell_fee_rate=fi_sell_fee,
            fixed_income_unit_step=fi_step,
            fixed_income_min_sweep_cash=fi_min_sweep,
            fixed_income_keep_cash_buffer=fi_buffer,
            buy_rearm_fraction=strategy_cfg.buy_rearm_fraction,
        )


class StrategyBExecutor:
    """
    PostgreSQL-backed paper executor for Strategy B.

    Invariants:
      * every gold entry is an independent tranche / position_id;
      * normal Entry #1/#2/#3/... are 10% all-in portfolio budgets;
      * MA7 can create only the exceptional Entry #2 when Entry #1 has not rearmed;
      * no partial entries;
      * total gold <= 100%, each current fund <= 30%;
      * EXIT has priority over relative rotation;
      * a rotation keeps the same position_id and origin_entry_type;
      * current fund's SELL threshold is used after rotation;
      * leftover non-gold capital is parked in Afran when a valid quote exists;
      * signals are persisted before this executor and are never deleted.
    """

    def __init__(
        self,
        config: StrategyBExecutionConfig,
        *,
        session_factory=SessionLocal,
    ):
        self.config = config
        self.session_factory = session_factory

    @classmethod
    def from_yaml(
        cls,
        strategy_path: str | Path,
        relative_path: str | Path,
        *,
        session_factory=SessionLocal,
    ) -> "StrategyBExecutor":
        return cls(
            StrategyBExecutionConfig.from_yaml(strategy_path, relative_path),
            session_factory=session_factory,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _d(value: Any, default: Decimal = ZERO) -> Decimal:
        if value is None:
            return default
        try:
            return Decimal(str(value))
        except Exception:
            return default

    @classmethod
    def _positive(cls, value: Any) -> Optional[Decimal]:
        d = cls._d(value)
        return d if d > ZERO else None

    @classmethod
    def _mark_price(cls, snap: FundSnapshot | None) -> Optional[Decimal]:
        """Current holding mark: executable liquidation Bid, else current Ask.

        No midpoint/last/close fallback is ever used.
        """
        if snap is None:
            return None
        bid = cls._positive(snap.best_bid)
        if bid is not None:
            return bid
        return cls._positive(snap.best_ask)

    @classmethod
    def _buy_tradeable(cls, snap: FundSnapshot | None) -> bool:
        if snap is None:
            return False
        ask = cls._positive(snap.best_ask)
        trade_value = cls._positive(snap.trade_value)
        if ask is None:
            return False
        if trade_value is None or int(snap.trade_count or 0) <= 0:
            return False
        return True

    @classmethod
    def _sell_tradeable(cls, snap: FundSnapshot | None) -> bool:
        if snap is None:
            return False
        bid = cls._positive(snap.best_bid)
        trade_value = cls._positive(snap.trade_value)
        if bid is None:
            return False
        if trade_value is None or int(snap.trade_count or 0) <= 0:
            return False
        return True

    @staticmethod
    def _snapshot_by_symbol(
        funds: Mapping[int, FundSnapshot], symbol: str
    ) -> tuple[Optional[int], Optional[FundSnapshot]]:
        for fund_id, snap in funds.items():
            if snap.symbol == symbol:
                return int(fund_id), snap
        return None, None

    def _instrument_id(self, session: Session, symbol: str) -> Optional[int]:
        row = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        return int(row.id) if row is not None else None

    def _open_positions(self, session: Session) -> list[PositionCurrent]:
        return session.scalars(
            select(PositionCurrent)
            .where(
                PositionCurrent.strategy_id == self.config.strategy_id,
                PositionCurrent.status == "OPEN",
            )
            .order_by(PositionCurrent.position_id.asc())
        ).all()

    def _load_account_state(self, session: Session) -> dict:
        row = session.scalar(
            select(StrategyRuntimeState).where(
                StrategyRuntimeState.strategy_id == self.config.strategy_id,
                StrategyRuntimeState.scope_key == "GLOBAL",
                StrategyRuntimeState.state_key == "account",
            )
        )
        if row is None:
            return {
                "initial_capital": str(self.config.initial_capital_irr),
                "cash": str(self.config.initial_capital_irr),
                "fixed_income_units": "0",
                "fixed_income_cost_basis": "0",
                "fixed_income_last_mark_price": None,
                "realized_pnl": "0",
                "fees_total": "0",
                "turnover": "0",
                "high_watermark": str(self.config.initial_capital_irr),
                "entry_count": 0,
                "threshold_gate_open": True,
                "rearm_reference_fund_id": None,
                "rearm_reference_buy_threshold": None,
                "rearm_threshold": None,
                "last_rearm_achieved_at": None,
                "last_entry_position_id": None,
                "last_entry_fund_id": None,
                "last_entry_number": None,
                "last_entry_route": None,
                "last_entry_at": None,
                "last_entry_market_date": None,
                "ma7_fallback_eligible": False,
                "ma7_fallback_consumed": False,
                "ma7_fallback_since_date": None,
                "ma7_primary_position_id": None,
                "ma7_primary_fund_id": None,
                "ma7_fallback_closed_reason": None,
            }
        return dict(row.state_value or {})

    def _save_account_state(self, session: Session, state: dict) -> None:
        row = session.scalar(
            select(StrategyRuntimeState).where(
                StrategyRuntimeState.strategy_id == self.config.strategy_id,
                StrategyRuntimeState.scope_key == "GLOBAL",
                StrategyRuntimeState.state_key == "account",
            )
        )
        if row is None:
            session.add(
                StrategyRuntimeState(
                    strategy_id=self.config.strategy_id,
                    scope_key="GLOBAL",
                    state_key="account",
                    state_value=state,
                )
            )
            # SessionLocal uses autoflush=False.
            # Prevent a second pending INSERT for GLOBAL/account.
            session.flush()
        else:
            row.state_value = state
            row.updated_at = self._now()

    def _buy_state_row(self, session: Session, fund_id: int) -> StrategyRuntimeState:
        scope = f"FUND:{int(fund_id)}"
        row = session.scalar(
            select(StrategyRuntimeState).where(
                StrategyRuntimeState.strategy_id == self.config.strategy_id,
                StrategyRuntimeState.scope_key == scope,
                StrategyRuntimeState.state_key == "buy_signal_state",
            )
        )
        if row is None:
            row = StrategyRuntimeState(
                strategy_id=self.config.strategy_id,
                scope_key=scope,
                fund_id=int(fund_id),
                state_key="buy_signal_state",
                state_value={
                    "buy_state": "READY",
                    "last_buy_trigger_at": None,
                    "last_rearmed_at": None,
                    "last_total_bubble": None,
                },
            )
            session.add(row)
            session.flush()
        return row

    def _lock_threshold_signal_states(self, session: Session, signals: list[Signal]) -> None:
        now = self._now().isoformat()
        for signal in signals:
            if signal.signal_type != "THRESHOLD_BUY" or signal.fund_id is None:
                continue
            row = self._buy_state_row(session, int(signal.fund_id))
            state = dict(row.state_value or {})
            state["buy_state"] = "LOCKED"
            state["last_buy_trigger_at"] = now
            state["last_total_bubble"] = (
                str(signal.total_bubble) if signal.total_bubble is not None else None
            )
            if signal.payload:
                state["buy_threshold"] = signal.payload.get("buy_threshold")
                state["rearm_threshold"] = signal.payload.get("rearm_threshold")
            row.state_value = state
            row.updated_at = self._now()

    def _update_rearm_states(
        self,
        session: Session,
        valuations: Mapping[int, FundValuation],
    ) -> None:
        now = self._now().isoformat()
        for fund_id, valuation in valuations.items():
            if not valuation.valid or valuation.total_bubble is None:
                continue
            if valuation.buy_threshold is None:
                continue

            row = self._buy_state_row(session, int(fund_id))
            state = dict(row.state_value or {})
            state["last_total_bubble"] = str(valuation.total_bubble)
            state["buy_threshold"] = str(valuation.buy_threshold)
            rearm_threshold = valuation.buy_threshold + self.config.buy_rearm_fraction
            state["rearm_threshold"] = str(rearm_threshold)

            if (
                str(state.get("buy_state", "READY")).upper() == "LOCKED"
                and valuation.total_bubble >= rearm_threshold
            ):
                state["buy_state"] = "READY"
                state["last_rearmed_at"] = now

            row.state_value = state
            row.updated_at = self._now()

    def _apply_account_rearm(
        self,
        state: dict,
        valuations: Mapping[int, FundValuation],
    ) -> StrategyBEntryState:
        entry_state = StrategyBEntryState.from_mapping(state)
        ref_id = entry_state.rearm_reference_fund_id
        if not entry_state.threshold_gate_open and ref_id is not None:
            valuation = valuations.get(int(ref_id))
            if valuation is not None and valuation.valid:
                entry_state = entry_state.preview_rearm(
                    current_total_bubble=valuation.total_bubble,
                    achieved_at=self._now().isoformat(),
                )
        merged = entry_state.merge_into(state)
        state.clear()
        state.update(merged)
        return entry_state

    @staticmethod
    def _market_date(common: CommonSnapshot) -> date:
        return common.collected_at.date()

    def _mark_positions(
        self,
        positions: list[PositionCurrent],
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        for pos in positions:
            snap = funds.get(int(pos.current_fund_id))
            mark = self._mark_price(snap)
            if mark is None:
                mark = self._positive(pos.mark_price)
            if mark is None:
                continue
            units = self._d(pos.units)
            value = units * mark
            pos.mark_price = mark
            pos.market_value = value
            pos.unrealized_pnl = value - self._d(pos.cost_basis)

    def _fixed_income_mark(
        self,
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> tuple[Optional[int], Optional[FundSnapshot], Decimal]:
        fi_id, fi_snap = self._snapshot_by_symbol(funds, self.config.fixed_income_symbol)
        mark = self._mark_price(fi_snap)
        if mark is None:
            mark = self._positive(state.get("fixed_income_last_mark_price"))
        if mark is not None:
            state["fixed_income_last_mark_price"] = str(mark)
        return fi_id, fi_snap, mark or ZERO

    def _portfolio_metrics(
        self,
        state: dict,
        positions: list[PositionCurrent],
        funds: Mapping[int, FundSnapshot],
    ) -> dict[str, Any]:
        self._mark_positions(positions, funds)
        cash = self._d(state.get("cash"))
        fi_units = self._d(state.get("fixed_income_units"))
        fi_basis = self._d(state.get("fixed_income_cost_basis"))
        _, _, fi_mark = self._fixed_income_mark(state, funds)
        fi_value = fi_units * fi_mark

        gold_by_fund: dict[int, Decimal] = {}
        gold_total = ZERO
        gold_unrealized = ZERO
        for pos in positions:
            if pos.status != "OPEN":
                continue
            value = self._d(pos.market_value)
            gold_total += value
            gold_unrealized += self._d(pos.unrealized_pnl)
            fid = int(pos.current_fund_id)
            gold_by_fund[fid] = gold_by_fund.get(fid, ZERO) + value

        portfolio = cash + fi_value + gold_total
        return {
            "cash": cash,
            "fixed_income_units": fi_units,
            "fixed_income_cost_basis": fi_basis,
            "fixed_income_mark": fi_mark,
            "fixed_income_value": fi_value,
            "fixed_income_unrealized": fi_value - fi_basis,
            "gold_by_fund": gold_by_fund,
            "gold_total": gold_total,
            "gold_unrealized": gold_unrealized,
            "portfolio": portfolio,
        }

    def _record_fixed_income_sell(
        self,
        session: Session,
        *,
        cycle_id: int,
        state: dict,
        funds: Mapping[int, FundSnapshot],
        desired_net_cash: Decimal,
        reason: str,
    ) -> bool:
        if desired_net_cash <= ZERO:
            return True
        units = self._d(state.get("fixed_income_units"))
        basis = self._d(state.get("fixed_income_cost_basis"))
        if units <= ZERO:
            return False

        fi_id, snap = self._snapshot_by_symbol(funds, self.config.fixed_income_symbol)
        if fi_id is None:
            fi_id = self._instrument_id(session, self.config.fixed_income_symbol)
        if not self._sell_tradeable(snap):
            return False
        assert snap is not None
        bid = self._positive(snap.best_bid)
        mark = self._mark_price(snap)
        if bid is None:
            return False

        plan = StrategyBExecutionMath.plan_fixed_income_sell_for_net_cash(
            current_units=units,
            current_cost_basis=basis,
            desired_net_cash=desired_net_cash,
            bid_price=bid,
            sell_fee_rate=self.config.fixed_income_sell_fee_rate,
            unit_step=self.config.fixed_income_unit_step,
        )

        cash = self._d(state.get("cash")) + plan.net_proceeds
        state["cash"] = str(cash)
        state["fixed_income_units"] = str(plan.units_after)
        state["fixed_income_cost_basis"] = str(plan.cost_basis_after)
        state["realized_pnl"] = str(
            self._d(state.get("realized_pnl")) + plan.realized_pnl
        )
        state["fees_total"] = str(
            self._d(state.get("fees_total")) + plan.sell_fee
        )
        state["turnover"] = str(
            self._d(state.get("turnover")) + plan.gross_value
        )

        spread_cost = ZERO
        if mark is not None and mark > bid:
            spread_cost = (mark - bid) * plan.units_sold

        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=None,
                strategy_id=self.config.strategy_id,
                position_id=None,
                action="FIXED_INCOME_SELL",
                source_fund_id=fi_id,
                target_fund_id=None,
                units=plan.units_sold,
                source_units=plan.units_sold,
                target_units=None,
                source_bid=bid,
                target_ask=None,
                gross_value=plan.gross_value,
                sell_fee=plan.sell_fee,
                buy_fee=ZERO,
                bid_ask_cost=spread_cost,
                total_transaction_cost=plan.sell_fee + spread_cost,
                portfolio_before=None,
                portfolio_after=None,
                reason=reason,
                executed_at=self._now(),
                details={
                    "net_proceeds": str(plan.net_proceeds),
                    "realized_pnl": str(plan.realized_pnl),
                    "units_after": str(plan.units_after),
                },
            )
        )
        return True

    def _ensure_cash(
        self,
        session: Session,
        *,
        cycle_id: int,
        state: dict,
        funds: Mapping[int, FundSnapshot],
        needed_cash: Decimal,
        reason: str,
    ) -> bool:
        current_cash = self._d(state.get("cash"))
        if current_cash >= needed_cash:
            return True
        if not self.config.fixed_income_enabled:
            return False

        gap = needed_cash - current_cash
        sold = self._record_fixed_income_sell(
            session,
            cycle_id=cycle_id,
            state=state,
            funds=funds,
            desired_net_cash=gap,
            reason=reason,
        )
        return bool(sold and self._d(state.get("cash")) >= needed_cash)

    def _sweep_cash_to_fixed_income(
        self,
        session: Session,
        *,
        cycle_id: int,
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        if not self.config.fixed_income_enabled:
            return
        cash = self._d(state.get("cash"))
        budget = cash - self.config.fixed_income_keep_cash_buffer
        if budget <= self.config.fixed_income_min_sweep_cash:
            return

        fi_id, snap = self._snapshot_by_symbol(funds, self.config.fixed_income_symbol)
        if fi_id is None:
            fi_id = self._instrument_id(session, self.config.fixed_income_symbol)
        if not self._buy_tradeable(snap):
            return
        assert snap is not None
        ask = self._positive(snap.best_ask)
        mark = self._mark_price(snap)
        if ask is None:
            return

        try:
            plan = StrategyBExecutionMath.plan_fixed_income_buy(
                total_cash_budget=budget,
                ask_price=ask,
                buy_fee_rate=self.config.fixed_income_buy_fee_rate,
                unit_step=self.config.fixed_income_unit_step,
            )
        except ValueError:
            return

        old_units = self._d(state.get("fixed_income_units"))
        old_basis = self._d(state.get("fixed_income_cost_basis"))
        state["cash"] = str(cash - plan.cash_outflow)
        state["fixed_income_units"] = str(old_units + plan.units)
        state["fixed_income_cost_basis"] = str(old_basis + plan.cash_outflow)
        state["fees_total"] = str(
            self._d(state.get("fees_total")) + plan.buy_fee
        )
        state["turnover"] = str(
            self._d(state.get("turnover")) + plan.gross_value
        )

        spread_cost = ZERO
        if mark is not None and ask > mark:
            spread_cost = (ask - mark) * plan.units

        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=None,
                strategy_id=self.config.strategy_id,
                position_id=None,
                action="FIXED_INCOME_BUY",
                source_fund_id=None,
                target_fund_id=fi_id,
                units=plan.units,
                source_units=None,
                target_units=plan.units,
                source_bid=None,
                target_ask=ask,
                gross_value=plan.gross_value,
                sell_fee=ZERO,
                buy_fee=plan.buy_fee,
                bid_ask_cost=spread_cost,
                total_transaction_cost=plan.buy_fee + spread_cost,
                portfolio_before=None,
                portfolio_after=None,
                reason="SWEEP_FREE_CASH",
                executed_at=self._now(),
                details={
                    "cash_outflow": str(plan.cash_outflow),
                    "cash_after": state["cash"],
                },
            )
        )

    def _execute_exit(
        self,
        session: Session,
        *,
        cycle_id: int,
        signal: Signal,
        position: PositionCurrent,
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> bool:
        if position.status != "OPEN":
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "POSITION_ALREADY_CLOSED"
            return False
        source_id = int(position.current_fund_id)
        if signal.source_fund_id is not None and int(signal.source_fund_id) != source_id:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "STALE_CURRENT_FUND"
            return False

        snap = funds.get(source_id)
        if not self._sell_tradeable(snap):
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "SOURCE_ORDERBOOK_INVALID"
            return False
        assert snap is not None
        bid = self._positive(snap.best_bid)
        mark = self._mark_price(snap)
        if bid is None:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "SOURCE_BID_INVALID"
            return False

        metrics = self._portfolio_metrics(state, self._open_positions(session), funds)
        portfolio_before = metrics["portfolio"]
        plan = StrategyBExecutionMath.plan_full_exit(
            units=self._d(position.units),
            bid_price=bid,
            sell_fee_rate=self.config.sell_fee_rate,
            cost_basis=self._d(position.cost_basis),
        )
        state["cash"] = str(self._d(state.get("cash")) + plan.net_proceeds)
        state["realized_pnl"] = str(
            self._d(state.get("realized_pnl")) + plan.realized_pnl
        )
        state["fees_total"] = str(
            self._d(state.get("fees_total")) + plan.sell_fee
        )
        state["turnover"] = str(
            self._d(state.get("turnover")) + plan.gross_value
        )

        now = self._now()
        position.status = "CLOSED"
        position.last_changed_at = now
        position.mark_price = bid
        position.market_value = ZERO
        position.unrealized_pnl = ZERO

        session.add(
            PositionEvent(
                position_id=position.position_id,
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                event_type="POSITION_CLOSED",
                source_fund_id=source_id,
                target_fund_id=None,
                units=plan.units,
                price=bid,
                gross_value=plan.gross_value,
                fees=plan.sell_fee,
                signal_id=signal.id,
                happened_at=now,
                payload={
                    "reason": "CURRENT_FUND_SELL_THRESHOLD",
                    "realized_pnl": str(plan.realized_pnl),
                    "origin_entry_type": position.origin_entry_type,
                },
            )
        )

        spread_cost = ZERO
        if mark is not None and mark > bid:
            spread_cost = (mark - bid) * plan.units
        portfolio_after = portfolio_before - spread_cost - plan.sell_fee

        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=signal.id,
                strategy_id=self.config.strategy_id,
                position_id=position.position_id,
                action="THRESHOLD_EXIT",
                source_fund_id=source_id,
                target_fund_id=None,
                units=plan.units,
                source_units=plan.units,
                target_units=None,
                source_bid=bid,
                target_ask=None,
                gross_value=plan.gross_value,
                sell_fee=plan.sell_fee,
                buy_fee=ZERO,
                bid_ask_cost=spread_cost,
                total_transaction_cost=plan.sell_fee + spread_cost,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                reason="CURRENT_FUND_SELL_THRESHOLD",
                executed_at=now,
                details={
                    "net_exit_value": str(plan.net_proceeds),
                    "realized_pnl": str(plan.realized_pnl),
                    "sell_threshold": (signal.payload or {}).get("sell_threshold"),
                },
            )
        )
        signal.account_had_capacity = True
        signal.trade_executed = True
        signal.non_execution_reason = None
        return True

    def _rotation_candidate_plan(
        self,
        *,
        position: PositionCurrent,
        target_id: int,
        funds: Mapping[int, FundSnapshot],
    ):
        source = funds.get(int(position.current_fund_id))
        target = funds.get(int(target_id))
        if not self._sell_tradeable(source) or not self._buy_tradeable(target):
            return None
        assert source is not None and target is not None
        source_bid = self._positive(source.best_bid)
        target_ask = self._positive(target.best_ask)
        target_mark = self._mark_price(target)
        if source_bid is None or target_ask is None or target_mark is None:
            return None
        try:
            plan = StrategyBExecutionMath.plan_full_rotation(
                source_units=self._d(position.units),
                source_bid=source_bid,
                source_cost_basis=self._d(position.cost_basis),
                target_ask=target_ask,
                sell_fee_rate=self.config.sell_fee_rate,
                buy_fee_rate=self.config.buy_fee_rate,
                unit_step=self.config.gold_unit_step,
            )
        except ValueError:
            return None
        return plan, target_mark

    def _execute_rotation(
        self,
        session: Session,
        *,
        cycle_id: int,
        signal: Signal,
        position: PositionCurrent,
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> bool:
        if position.status != "OPEN":
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "POSITION_ALREADY_CLOSED"
            return False

        source_id = int(position.current_fund_id)
        if signal.source_fund_id is not None and int(signal.source_fund_id) != source_id:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "STALE_SOURCE_POSITION"
            return False

        metrics = self._portfolio_metrics(state, self._open_positions(session), funds)
        portfolio_before = metrics["portfolio"]
        candidates = list((signal.payload or {}).get("candidate_targets") or [])
        if not candidates and signal.target_fund_id is not None:
            candidates = [{"target_fund_id": int(signal.target_fund_id)}]

        blocked: list[dict[str, Any]] = []
        chosen = None
        chosen_plan = None
        chosen_target_mark = None

        for candidate in candidates:
            try:
                target_id = int(candidate["target_fund_id"])
            except Exception:
                continue
            if target_id == source_id:
                continue

            result = self._rotation_candidate_plan(
                position=position,
                target_id=target_id,
                funds=funds,
            )
            if result is None:
                blocked.append({"target_fund_id": target_id, "reason": "MARKET_INVALID"})
                continue
            plan, target_mark = result

            target_exposure = metrics["gold_by_fund"].get(target_id, ZERO)
            projected_target_value = plan.target_units * target_mark
            if not StrategyBExecutionMath.fits_rotation_fund_cap(
                portfolio_value=portfolio_before,
                target_fund_exposure_before=target_exposure,
                projected_target_market_value=projected_target_value,
                max_per_fund_fraction=self.config.max_per_fund_fraction,
            ):
                blocked.append({"target_fund_id": target_id, "reason": "FUND_CAP_REACHED"})
                continue

            chosen = candidate
            chosen_plan = plan
            chosen_target_mark = target_mark
            break

        if chosen is None or chosen_plan is None or chosen_target_mark is None:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "NO_RELATIVE_TARGET_WITH_30PCT_CAPACITY"
            payload = dict(signal.payload or {})
            payload["blocked_targets"] = blocked
            signal.payload = payload
            return False

        target_id = int(chosen["target_fund_id"])
        source = funds[source_id]
        target = funds[target_id]
        source_mark = self._mark_price(source) or chosen_plan.source_bid

        cash_before = self._d(state.get("cash"))
        state["cash"] = str(cash_before + chosen_plan.cash_remainder)
        state["realized_pnl"] = str(
            self._d(state.get("realized_pnl")) + chosen_plan.realized_pnl
        )
        fees = chosen_plan.sell_fee + chosen_plan.buy_fee
        state["fees_total"] = str(self._d(state.get("fees_total")) + fees)
        state["turnover"] = str(
            self._d(state.get("turnover"))
            + chosen_plan.gross_sell_value
            + chosen_plan.gross_buy_value
        )

        now = self._now()
        old_units = self._d(position.units)
        old_basis = self._d(position.cost_basis)
        position.current_fund_id = target_id
        position.units = chosen_plan.target_units
        position.cost_basis = chosen_plan.gross_buy_value + chosen_plan.buy_fee
        position.mark_price = chosen_target_mark
        position.market_value = chosen_plan.target_units * chosen_target_mark
        position.unrealized_pnl = position.market_value - self._d(position.cost_basis)
        position.rotations_count = int(position.rotations_count or 0) + 1
        position.last_changed_at = now

        source_spread_cost = max(
            ZERO, (source_mark - chosen_plan.source_bid) * old_units
        )
        target_spread_cost = max(
            ZERO,
            (chosen_plan.target_ask - chosen_target_mark) * chosen_plan.target_units,
        )
        spread_cost = source_spread_cost + target_spread_cost
        portfolio_after = (
            portfolio_before - fees - spread_cost
        )

        session.add(
            PositionEvent(
                position_id=position.position_id,
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                event_type="ROTATED",
                source_fund_id=source_id,
                target_fund_id=target_id,
                units=chosen_plan.target_units,
                price=chosen_plan.target_ask,
                gross_value=chosen_plan.gross_buy_value,
                fees=fees,
                signal_id=signal.id,
                happened_at=now,
                payload={
                    "source_units": str(old_units),
                    "source_cost_basis": str(old_basis),
                    "target_units": str(chosen_plan.target_units),
                    "cash_remainder": str(chosen_plan.cash_remainder),
                    "realized_pnl_on_source": str(chosen_plan.realized_pnl),
                    "origin_entry_type": position.origin_entry_type,
                    "blocked_higher_ranked_targets": blocked,
                },
            )
        )
        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=signal.id,
                strategy_id=self.config.strategy_id,
                position_id=position.position_id,
                action="RELATIVE_ROTATION",
                source_fund_id=source_id,
                target_fund_id=target_id,
                units=chosen_plan.target_units,
                source_units=chosen_plan.source_units,
                target_units=chosen_plan.target_units,
                source_bid=chosen_plan.source_bid,
                target_ask=chosen_plan.target_ask,
                gross_value=chosen_plan.gross_buy_value,
                sell_fee=chosen_plan.sell_fee,
                buy_fee=chosen_plan.buy_fee,
                bid_ask_cost=spread_cost,
                total_transaction_cost=fees + spread_cost,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                reason="RELATIVE_VALUE_OVERLAY",
                executed_at=now,
                details={
                    "gross_sell_value": str(chosen_plan.gross_sell_value),
                    "net_sell_proceeds": str(chosen_plan.net_sell_proceeds),
                    "cash_remainder": str(chosen_plan.cash_remainder),
                    "actual_target_net_edge": chosen.get("net_executable_edge"),
                    "best_market_target_fund_id": (signal.payload or {}).get(
                        "best_market_target_fund_id"
                    ),
                    "blocked_higher_ranked_targets": blocked,
                },
            )
        )

        payload = dict(signal.payload or {})
        payload["executed_target_fund_id"] = target_id
        payload["executed_target_symbol"] = target.symbol
        payload["blocked_targets_before_execution"] = blocked
        signal.payload = payload
        signal.account_had_capacity = True
        signal.trade_executed = True
        signal.non_execution_reason = None
        return True

    def _execute_entry(
        self,
        session: Session,
        *,
        cycle_id: int,
        signal: Signal,
        target_id: int,
        stage: str,
        allocation_fraction: Decimal,
        parent_position_id: Optional[int],
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> Optional[PositionCurrent]:
        target = funds.get(int(target_id))
        if not self._buy_tradeable(target):
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "TARGET_ORDERBOOK_INVALID"
            return None
        assert target is not None
        ask = self._positive(target.best_ask)
        mark = self._mark_price(target)
        if ask is None or mark is None:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "TARGET_PRICE_INVALID"
            return None

        positions = self._open_positions(session)
        metrics = self._portfolio_metrics(state, positions, funds)
        portfolio_before = metrics["portfolio"]
        if portfolio_before <= ZERO:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "PORTFOLIO_VALUE_INVALID"
            return None

        target_budget = portfolio_before * allocation_fraction
        if target_budget < self.config.min_entry_cash_irr:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "ENTRY_BELOW_MINIMUM_CASH"
            return None

        target_exposure = metrics["gold_by_fund"].get(int(target_id), ZERO)
        if not StrategyBExecutionMath.fits_entry_caps(
            portfolio_value=portfolio_before,
            total_gold_exposure=metrics["gold_total"],
            target_fund_exposure=target_exposure,
            all_in_entry_budget=target_budget,
            max_total_gold_fraction=self.config.max_total_gold_fraction,
            max_per_fund_fraction=self.config.max_per_fund_fraction,
        ):
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "TOTAL_OR_FUND_EXPOSURE_CAP"
            return None

        try:
            plan = StrategyBExecutionMath.plan_budgeted_buy(
                total_cash_budget=target_budget,
                ask_price=ask,
                buy_fee_rate=self.config.buy_fee_rate,
                unit_step=self.config.gold_unit_step,
            )
        except ValueError:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "ENTRY_BUDGET_TOO_SMALL_FOR_UNIT"
            return None

        if not self._ensure_cash(
            session,
            cycle_id=cycle_id,
            state=state,
            funds=funds,
            needed_cash=plan.cash_outflow,
            reason=f"GOLD_ENTRY:{stage}:{target.symbol}",
        ):
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "INSUFFICIENT_CASH_OR_FIXED_INCOME_LIQUIDITY"
            return None

        cash_before = self._d(state.get("cash"))
        if cash_before < plan.cash_outflow:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = "INSUFFICIENT_CASH_AFTER_LIQUIDATION"
            return None

        state["cash"] = str(cash_before - plan.cash_outflow)
        state["fees_total"] = str(
            self._d(state.get("fees_total")) + plan.buy_fee
        )
        state["turnover"] = str(
            self._d(state.get("turnover")) + plan.gross_value
        )

        now = self._now()
        market_value = plan.units * mark
        position = PositionCurrent(
            strategy_id=self.config.strategy_id,
            origin_fund_id=int(target_id),
            current_fund_id=int(target_id),
            origin_entry_type=stage,
            parent_position_id=parent_position_id,
            opened_at=now,
            last_changed_at=now,
            units=plan.units,
            cost_basis=plan.cash_outflow,
            mark_price=mark,
            market_value=market_value,
            unrealized_pnl=market_value - plan.cash_outflow,
            rotations_count=0,
            status="OPEN",
        )
        session.add(position)
        session.flush()

        spread_cost = max(ZERO, (ask - mark) * plan.units)
        portfolio_after = portfolio_before - plan.buy_fee - spread_cost
        session.add(
            PositionEvent(
                position_id=position.position_id,
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                event_type="POSITION_OPENED",
                source_fund_id=None,
                target_fund_id=int(target_id),
                units=plan.units,
                price=ask,
                gross_value=plan.gross_value,
                fees=plan.buy_fee,
                signal_id=signal.id,
                happened_at=now,
                payload={
                    "entry_stage": stage,
                    "parent_position_id": parent_position_id,
                    "target_budget": str(target_budget),
                    "cash_outflow": str(plan.cash_outflow),
                    "total_bubble": str(signal.total_bubble)
                    if signal.total_bubble is not None
                    else None,
                },
            )
        )
        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=signal.id,
                strategy_id=self.config.strategy_id,
                position_id=position.position_id,
                action=stage,
                source_fund_id=None,
                target_fund_id=int(target_id),
                units=plan.units,
                source_units=None,
                target_units=plan.units,
                source_bid=None,
                target_ask=ask,
                gross_value=plan.gross_value,
                sell_fee=ZERO,
                buy_fee=plan.buy_fee,
                bid_ask_cost=spread_cost,
                total_transaction_cost=plan.buy_fee + spread_cost,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                reason=(
                    f"BUY_{stage}_THRESHOLD_REARM"
                    if str((signal.payload or {}).get("entry_route", "")).upper() != "MA7_FALLBACK"
                    else "BUY_ENTRY_2_MA7_FALLBACK"
                ),
                executed_at=now,
                details={
                    "parent_position_id": parent_position_id,
                    "target_budget": str(target_budget),
                    "unused_budget": str(plan.unused_budget),
                },
            )
        )

        signal.account_had_capacity = True
        signal.trade_executed = True
        signal.non_execution_reason = None
        payload = dict(signal.payload or {})
        payload["executed_target_fund_id"] = int(target_id)
        payload["executed_position_id"] = position.position_id
        signal.payload = payload
        return position

    def _write_account_snapshot(
        self,
        session: Session,
        *,
        cycle_id: int,
        state: dict,
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        existing = session.scalar(
            select(AccountSnapshot).where(
                AccountSnapshot.cycle_id == cycle_id,
                AccountSnapshot.strategy_id == self.config.strategy_id,
            )
        )
        if existing is not None:
            return

        positions = self._open_positions(session)
        metrics = self._portfolio_metrics(state, positions, funds)
        portfolio = metrics["portfolio"]
        initial = self._d(state.get("initial_capital"), self.config.initial_capital_irr)
        high = self._d(state.get("high_watermark"), initial)
        if portfolio > high:
            high = portfolio
        state["high_watermark"] = str(high)

        total_return = (portfolio / initial - ONE) if initial > ZERO else ZERO
        drawdown = (portfolio / high - ONE) if high > ZERO else ZERO
        unrealized = metrics["gold_unrealized"] + metrics["fixed_income_unrealized"]
        active_funds = sorted({int(p.current_fund_id) for p in positions})

        self._save_account_state(session, state)
        session.add(
            AccountSnapshot(
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                portfolio_value=portfolio,
                cash=metrics["cash"],
                gold_exposure=metrics["gold_total"],
                fixed_income_value=metrics["fixed_income_value"],
                realized_pnl=self._d(state.get("realized_pnl")),
                unrealized_pnl=unrealized,
                total_return=total_return,
                fees_total=self._d(state.get("fees_total")),
                turnover=self._d(state.get("turnover")),
                drawdown=drawdown,
                active_positions_count=len(positions),
                active_funds=active_funds,
                captured_at=self._now(),
            )
        )

    def execute_strategy_signals(
        self,
        *,
        cycle_id: int,
        strategy_id: str,
        signal_ids: list[int],
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> None:
        if strategy_id != self.config.strategy_id:
            return

        with self.session_factory() as session:
            with session.begin():
                state = self._load_account_state(session)
                signals: list[Signal] = []
                if signal_ids:
                    signals = session.scalars(
                        select(Signal)
                        .where(
                            Signal.id.in_(signal_ids),
                            Signal.strategy_id == self.config.strategy_id,
                        )
                        .order_by(Signal.id.asc())
                    ).all()

                # Reconstruct/update both per-fund signal rearm and the account-level
                # staged-entry rearm BEFORE applying this cycle's signals.
                self._update_rearm_states(session, valuations)
                entry_state = self._apply_account_rearm(state, valuations)

                # A generated threshold signal locks that FUND even if execution later
                # fails. Signal state is independent from account execution state.
                self._lock_threshold_signal_states(session, signals)

                exit_signals = [s for s in signals if s.signal_type == "THRESHOLD_SELL"]
                rotation_signals = [s for s in signals if s.signal_type == "ROTATE_TO"]
                threshold_signals = [s for s in signals if s.signal_type == "THRESHOLD_BUY"]
                ma7_signals = [s for s in signals if s.signal_type == "MA7_FALLBACK_BUY_2"]
                supported_ids = {
                    s.id for s in exit_signals + rotation_signals + threshold_signals + ma7_signals
                }
                for s in signals:
                    if s.id not in supported_ids:
                        s.account_had_capacity = False
                        s.trade_executed = False
                        s.non_execution_reason = "UNSUPPORTED_SIGNAL_TYPE"

                # 1) EXIT first.
                for signal in exit_signals:
                    position_id = (signal.payload or {}).get("position_id")
                    try:
                        position_id = int(position_id)
                    except Exception:
                        signal.account_had_capacity = False
                        signal.trade_executed = False
                        signal.non_execution_reason = "MISSING_POSITION_ID"
                        continue
                    pos = session.get(PositionCurrent, position_id)
                    if pos is None or pos.strategy_id != self.config.strategy_id:
                        signal.account_had_capacity = False
                        signal.trade_executed = False
                        signal.non_execution_reason = "POSITION_NOT_FOUND"
                        continue
                    self._execute_exit(
                        session,
                        cycle_id=cycle_id,
                        signal=signal,
                        position=pos,
                        state=state,
                        funds=funds,
                    )

                # 2) Relative rotations for still-open tranches.
                for signal in rotation_signals:
                    position_id = (signal.payload or {}).get("position_id")
                    try:
                        position_id = int(position_id)
                    except Exception:
                        signal.account_had_capacity = False
                        signal.trade_executed = False
                        signal.non_execution_reason = "MISSING_POSITION_ID"
                        continue
                    pos = session.get(PositionCurrent, position_id)
                    if pos is None or pos.strategy_id != self.config.strategy_id:
                        signal.account_had_capacity = False
                        signal.trade_executed = False
                        signal.non_execution_reason = "POSITION_NOT_FOUND"
                        continue
                    self._execute_rotation(
                        session,
                        cycle_id=cycle_id,
                        signal=signal,
                        position=pos,
                        state=state,
                        funds=funds,
                    )

                # 3) Normal Threshold Entry N. Exactly one 10% tranche may execute
                # per cycle; candidates are ordered by lowest total bubble.
                entry_state = self._apply_account_rearm(state, valuations)
                threshold_executed = False
                if not entry_state.threshold_gate_open:
                    for s in threshold_signals:
                        s.account_had_capacity = False
                        s.trade_executed = False
                        s.non_execution_reason = "REARM_1_5PP_REQUIRED_FOR_NEXT_THRESHOLD_ENTRY"
                elif threshold_signals:
                    ordered = sorted(
                        threshold_signals,
                        key=lambda s: self._d(s.total_bubble, Decimal("999")),
                    )
                    executed_signal_id = None
                    for s in ordered:
                        target_id = s.fund_id
                        if target_id is None:
                            s.account_had_capacity = False
                            s.trade_executed = False
                            s.non_execution_reason = "MISSING_TARGET_FUND"
                            continue
                        target_id = int(target_id)
                        valuation = valuations.get(target_id)
                        if valuation is None or not valuation.valid or valuation.buy_threshold is None:
                            s.account_had_capacity = False
                            s.trade_executed = False
                            s.non_execution_reason = "TARGET_VALUATION_OR_BUY_THRESHOLD_INVALID"
                            continue

                        expected_number = entry_state.next_entry_number
                        stage = f"ENTRY_{expected_number}"
                        parent_id = entry_state.last_entry_position_id
                        pos = self._execute_entry(
                            session,
                            cycle_id=cycle_id,
                            signal=s,
                            target_id=target_id,
                            stage=stage,
                            allocation_fraction=self.config.threshold_entry_fraction,
                            parent_position_id=parent_id,
                            state=state,
                            funds=funds,
                        )
                        if pos is None:
                            continue

                        executed_signal_id = s.id
                        threshold_executed = True
                        s.signal_stage = stage
                        payload = dict(s.payload or {})
                        payload["executed_entry_number"] = expected_number
                        payload["executed_entry_route"] = "THRESHOLD_REARM"
                        s.payload = payload

                        entry_state = entry_state.after_executed_entry(
                            fund_id=target_id,
                            position_id=pos.position_id,
                            buy_threshold=valuation.buy_threshold,
                            rearm_margin=self.config.buy_rearm_fraction,
                            route="THRESHOLD_REARM",
                            executed_at=self._now().isoformat(),
                            market_date=self._market_date(common),
                        )
                        merged = entry_state.merge_into(state)
                        state.clear()
                        state.update(merged)
                        break

                    if executed_signal_id is not None:
                        for s in ordered:
                            if s.id == executed_signal_id:
                                continue
                            if s.trade_executed is None:
                                s.account_had_capacity = False
                                s.trade_executed = False
                                s.non_execution_reason = "LOWER_RANKED_THRESHOLD_ENTRY_CANDIDATE"

                # 4) MA7 is only the one-time fallback for Entry #2. If a normal
                # threshold Entry #2 just executed, or Entry #1 already rearmed,
                # the fallback is closed and cannot execute.
                entry_state = self._apply_account_rearm(state, valuations)
                for s in ma7_signals[:1]:
                    if threshold_executed:
                        s.account_had_capacity = False
                        s.trade_executed = False
                        s.non_execution_reason = "NORMAL_THRESHOLD_ENTRY_TAKES_PRIORITY_OVER_MA7"
                        continue
                    if not entry_state.ma7_fallback_allowed(self._market_date(common)):
                        s.account_had_capacity = False
                        s.trade_executed = False
                        s.non_execution_reason = (
                            entry_state.ma7_fallback_closed_reason
                            or "MA7_FALLBACK_NOT_ELIGIBLE"
                        )
                        continue

                    candidates = list(
                        (s.payload or {}).get("candidate_funds_by_total_bubble") or []
                    )
                    parent_id = entry_state.ma7_primary_position_id
                    executed = None
                    blocked: list[dict[str, Any]] = []

                    for candidate in candidates:
                        try:
                            target_id = int(candidate["fund_id"])
                        except Exception:
                            continue
                        valuation = valuations.get(target_id)
                        if valuation is None or not valuation.valid or valuation.buy_threshold is None:
                            blocked.append({
                                "target_fund_id": target_id,
                                "reason": "TARGET_VALUATION_OR_BUY_THRESHOLD_INVALID",
                            })
                            continue

                        previous_reason = s.non_execution_reason
                        pos = self._execute_entry(
                            session,
                            cycle_id=cycle_id,
                            signal=s,
                            target_id=target_id,
                            stage="ENTRY_2",
                            allocation_fraction=self.config.ma7_second_entry_fraction,
                            parent_position_id=parent_id,
                            state=state,
                            funds=funds,
                        )
                        if pos is not None:
                            executed = (pos, target_id, valuation)
                            break
                        blocked.append({
                            "target_fund_id": target_id,
                            "reason": s.non_execution_reason,
                        })
                        s.non_execution_reason = previous_reason

                    if executed is not None:
                        pos, target_id, valuation = executed
                        entry_state = entry_state.after_executed_entry(
                            fund_id=target_id,
                            position_id=pos.position_id,
                            buy_threshold=valuation.buy_threshold,
                            rearm_margin=self.config.buy_rearm_fraction,
                            route="MA7_FALLBACK",
                            executed_at=self._now().isoformat(),
                            market_date=self._market_date(common),
                        )
                        merged = entry_state.merge_into(state)
                        state.clear()
                        state.update(merged)

                        payload = dict(s.payload or {})
                        payload["blocked_candidates_before_execution"] = blocked
                        payload["executed_target_fund_id"] = target_id
                        payload["executed_position_id"] = pos.position_id
                        payload["executed_entry_number"] = 2
                        payload["executed_entry_route"] = "MA7_FALLBACK"
                        s.payload = payload
                        s.signal_stage = "ENTRY_2"
                        s.account_had_capacity = True
                        s.trade_executed = True
                        s.non_execution_reason = None
                    else:
                        s.account_had_capacity = False
                        s.trade_executed = False
                        s.non_execution_reason = "NO_MA7_ENTRY2_TARGET_WITH_CAPACITY_OR_LIQUIDITY"
                        payload = dict(s.payload or {})
                        payload["blocked_candidates"] = blocked
                        s.payload = payload

                # Keep both rearm layers durable across VPS restarts.
                self._update_rearm_states(session, valuations)
                self._apply_account_rearm(state, valuations)

                # Park non-gold capital in Afran if quote is valid.
                self._sweep_cash_to_fixed_income(
                    session,
                    cycle_id=cycle_id,
                    state=state,
                    funds=funds,
                )

                self._save_account_state(session, state)
                self._write_account_snapshot(
                    session,
                    cycle_id=cycle_id,
                    state=state,
                    funds=funds,
                )

    def mark_account_only(
        self,
        *,
        cycle_id: int,
        strategy_id: str,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> None:
        """
        17:00 close-phase mark-to-market only.
        No entry, exit, rotation, Afran sweep, or signal execution.
        """
        if strategy_id != self.config.strategy_id:
            return

        with self.session_factory() as session:
            with session.begin():
                state = self._load_account_state(session)
                self._write_account_snapshot(
                    session,
                    cycle_id=cycle_id,
                    state=state,
                    funds=funds,
                )

