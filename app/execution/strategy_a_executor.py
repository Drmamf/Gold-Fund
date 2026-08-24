from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import CommonSnapshot, FundSnapshot, FundValuation
from app.database import SessionLocal
from app.execution.strategy_a_math import StrategyAExecutionMath
from app.models import (
    AccountSnapshot,
    PositionCurrent,
    PositionEvent,
    Signal,
    StrategyRuntimeState,
    Transaction,
)
from app.strategies.strategy_a_relative_buy_hold import StrategyAConfig


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class StrategyAExecutionConfig:
    strategy_id: str
    anchor_symbol: str
    initial_capital_irr: Decimal
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    unit_step: Decimal

    @classmethod
    def from_yaml(
        cls,
        strategy_path: str | Path,
        relative_path: str | Path,
    ) -> "StrategyAExecutionConfig":
        strategy = StrategyAConfig.from_yaml(strategy_path)

        with Path(relative_path).open("r", encoding="utf-8") as fh:
            rel = (yaml.safe_load(fh) or {})["relative_value"]

        costs = rel["execution_costs"]
        buy_fee = Decimal(str(costs["buy_fee_rate"]))
        sell_fee = Decimal(str(costs["sell_fee_rate"]))

        with Path(strategy_path).open("r", encoding="utf-8") as fh:
            raw_strategy = yaml.safe_load(fh) or {}
        unit_step = Decimal(str(raw_strategy.get("execution", {}).get("unit_step", 1)))

        if strategy.initial_capital_irr <= ZERO:
            raise ValueError("initial_capital_irr must be positive.")
        if unit_step <= ZERO:
            raise ValueError("unit_step must be positive.")
        if not (ZERO <= buy_fee < ONE and ZERO <= sell_fee < ONE):
            raise ValueError("Fee rates must be in [0, 1).")

        return cls(
            strategy_id=strategy.strategy_id,
            anchor_symbol=strategy.anchor_symbol,
            initial_capital_irr=strategy.initial_capital_irr,
            buy_fee_rate=buy_fee,
            sell_fee_rate=sell_fee,
            unit_step=unit_step,
        )




class StrategyAExecutor:
    """
    PostgreSQL-backed paper-account executor for Strategy A.

    Guarantees:
      * exactly one logical position for Strategy A;
      * bootstrap buys Ayyar with 100% of initial capital (minus fees/rounding);
      * every successful rotation keeps the same position_id;
      * sells at source best bid, buys target at best ask;
      * signals are updated with execution outcome;
      * account snapshot is written on EVERY cycle, even on HOLD;
      * runtime state is persisted for safe VPS restarts.
    """

    def __init__(
        self,
        config: StrategyAExecutionConfig,
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
    ) -> "StrategyAExecutor":
        return cls(
            StrategyAExecutionConfig.from_yaml(strategy_path, relative_path),
            session_factory=session_factory,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _positive(value) -> Optional[Decimal]:
        if value is None:
            return None
        d = Decimal(str(value))
        return d if d > ZERO else None

    @classmethod
    def _mark_price(cls, snap: FundSnapshot) -> Optional[Decimal]:
        # Holdings are marked to executable liquidation Bid when available;
        # current Ask is the only fallback. Never midpoint/last/close.
        bid = cls._positive(snap.best_bid)
        if bid is not None:
            return bid
        return cls._positive(snap.best_ask)

    def _find_anchor(self, funds: Mapping[int, FundSnapshot]) -> tuple[int, FundSnapshot]:
        for fund_id, snap in funds.items():
            if snap.symbol == self.config.anchor_symbol:
                return int(fund_id), snap
        raise RuntimeError(f"Anchor {self.config.anchor_symbol!r} is missing.")

    def _open_position(self, session: Session) -> Optional[PositionCurrent]:
        rows = session.scalars(
            select(PositionCurrent).where(
                PositionCurrent.strategy_id == self.config.strategy_id,
                PositionCurrent.status == "OPEN",
            )
        ).all()
        if len(rows) > 1:
            raise RuntimeError("Strategy A invariant violated: more than one OPEN position.")
        return rows[0] if rows else None

    def _latest_account(self, session: Session) -> Optional[AccountSnapshot]:
        return session.scalar(
            select(AccountSnapshot)
            .where(AccountSnapshot.strategy_id == self.config.strategy_id)
            .order_by(AccountSnapshot.captured_at.desc(), AccountSnapshot.id.desc())
            .limit(1)
        )

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
                "realized_pnl": "0",
                "fees_total": "0",
                "turnover": "0",
                "high_watermark": str(self.config.initial_capital_irr),
                "rotation_count": 0,
                "current_position_id": None,
                "current_fund_id": None,
                "last_rotation_at": None,
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
            row = StrategyRuntimeState(
                strategy_id=self.config.strategy_id,
                scope_key="GLOBAL",
                state_key="account",
                state_value=state,
            )
            session.add(row)
            # SessionLocal uses autoflush=False.
            # Make the new unique runtime-state row visible immediately
            # inside this same transaction.
            session.flush()
        else:
            row.state_value = state
            row.updated_at = self._now()

    def _bootstrap(
        self,
        session: Session,
        *,
        cycle_id: int,
        funds: Mapping[int, FundSnapshot],
    ) -> PositionCurrent:
        anchor_id, anchor = self._find_anchor(funds)
        bid = self._positive(anchor.best_bid)
        ask = self._positive(anchor.best_ask)
        mark = self._mark_price(anchor)
        trade_value = self._positive(anchor.trade_value)
        trade_count = int(anchor.trade_count or 0)

        if (
            not anchor.data_valid
            or ask is None
            or (bid is not None and ask < bid)
            or mark is None
            or trade_value is None
            or trade_count <= 0
        ):
            raise RuntimeError("STRATEGY_A_BOOTSTRAP_ANCHOR_MARKET_INVALID")

        plan = StrategyAExecutionMath.plan_buy(
            available_cash=self.config.initial_capital_irr,
            ask_price=ask,
            buy_fee_rate=self.config.buy_fee_rate,
            unit_step=self.config.unit_step,
        )

        now = self._now()
        market_value = plan.units * mark
        cost_basis = plan.gross_value + plan.buy_fee

        position = PositionCurrent(
            strategy_id=self.config.strategy_id,
            origin_fund_id=anchor_id,
            current_fund_id=anchor_id,
            origin_entry_type="ACCOUNT_INITIALIZATION",
            opened_at=now,
            last_changed_at=now,
            units=plan.units,
            cost_basis=cost_basis,
            mark_price=mark,
            market_value=market_value,
            unrealized_pnl=market_value - cost_basis,
            rotations_count=0,
            status="OPEN",
        )
        session.add(position)
        session.flush()

        session.add(
            PositionEvent(
                position_id=position.position_id,
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                event_type="POSITION_OPENED",
                source_fund_id=None,
                target_fund_id=anchor_id,
                units=plan.units,
                price=ask,
                gross_value=plan.gross_value,
                fees=plan.buy_fee,
                signal_id=None,
                happened_at=now,
                payload={
                    "reason": "ACCOUNT_INITIALIZATION",
                    "cash_after": str(plan.cash_after),
                },
            )
        )

        bootstrap_spread_cost = max(ZERO, (ask - mark) * plan.units)

        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=None,
                strategy_id=self.config.strategy_id,
                position_id=position.position_id,
                action="ACCOUNT_INITIALIZATION_BUY",
                source_fund_id=None,
                target_fund_id=anchor_id,
                units=plan.units,
                source_units=None,
                target_units=plan.units,
                source_bid=None,
                target_ask=ask,
                gross_value=plan.gross_value,
                sell_fee=ZERO,
                buy_fee=plan.buy_fee,
                bid_ask_cost=bootstrap_spread_cost,
                total_transaction_cost=plan.buy_fee + bootstrap_spread_cost,
                portfolio_before=self.config.initial_capital_irr,
                portfolio_after=plan.cash_after + market_value,
                reason="ACCOUNT_INITIALIZATION",
                executed_at=now,
                details={"cash_after": str(plan.cash_after)},
            )
        )

        state = self._load_account_state(session)
        state.update(
            {
                "cash": str(plan.cash_after),
                "fees_total": str(plan.buy_fee),
                "turnover": str(plan.gross_value),
                "current_position_id": position.position_id,
                "current_fund_id": anchor_id,
                "rotation_count": 0,
            }
        )
        self._save_account_state(session, state)
        return position

    def _execute_rotation(
        self,
        session: Session,
        *,
        cycle_id: int,
        signal: Signal,
        position: PositionCurrent,
        funds: Mapping[int, FundSnapshot],
    ) -> bool:
        source_id = int(position.current_fund_id)
        target_id = signal.target_fund_id

        if signal.source_fund_id != source_id:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "STALE_SOURCE_POSITION"
            return False

        if target_id is None or int(target_id) == source_id:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "INVALID_TARGET"
            return False

        source = funds.get(source_id)
        target = funds.get(int(target_id))
        if source is None or target is None:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "MARKET_SNAPSHOT_MISSING"
            return False
        if not source.data_valid or not target.data_valid:
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "MARKET_DATA_INVALID"
            return False

        source_bid = self._positive(source.best_bid)
        source_ask = self._positive(source.best_ask)
        target_bid = self._positive(target.best_bid)
        target_ask = self._positive(target.best_ask)
        source_mark = self._mark_price(source)
        target_mark = self._mark_price(target)
        if (
            source_bid is None
            or source_ask is None
            or source_ask < source_bid
            or target_ask is None
            or (target_bid is not None and target_ask < target_bid)
            or source_mark is None
            or target_mark is None
        ):
            signal.account_had_capacity = True
            signal.trade_executed = False
            signal.non_execution_reason = "ORDERBOOK_INVALID"
            return False

        state = self._load_account_state(session)
        cash_before = Decimal(str(state.get("cash", "0")))
        source_cost_basis = Decimal(str(position.cost_basis or "0"))
        portfolio_before = cash_before + (Decimal(str(position.units)) * source_mark)

        try:
            plan = StrategyAExecutionMath.plan_rotation(
                source_units=Decimal(str(position.units)),
                source_bid=source_bid,
                source_cost_basis=source_cost_basis,
                starting_cash=cash_before,
                target_ask=target_ask,
                sell_fee_rate=self.config.sell_fee_rate,
                buy_fee_rate=self.config.buy_fee_rate,
                unit_step=self.config.unit_step,
            )
        except ValueError as exc:
            signal.account_had_capacity = False
            signal.trade_executed = False
            signal.non_execution_reason = str(exc)
            return False

        now = self._now()
        new_cost_basis = plan.gross_buy_value + plan.buy_fee
        new_market_value = plan.target_units * target_mark
        portfolio_after = plan.cash_after + new_market_value

        position.current_fund_id = int(target_id)
        position.last_changed_at = now
        position.units = plan.target_units
        position.cost_basis = new_cost_basis
        position.mark_price = target_mark
        position.market_value = new_market_value
        position.unrealized_pnl = new_market_value - new_cost_basis
        position.rotations_count = int(position.rotations_count or 0) + 1

        total_fees_this_rotation = plan.sell_fee + plan.buy_fee
        source_spread_cost = max(ZERO, source_mark - source_bid) * plan.source_units
        target_spread_cost = max(ZERO, target_ask - target_mark) * plan.target_units
        bid_ask_cost = source_spread_cost + target_spread_cost

        session.add(
            PositionEvent(
                position_id=position.position_id,
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                event_type="ROTATED",
                source_fund_id=source_id,
                target_fund_id=int(target_id),
                units=plan.target_units,
                price=target_ask,
                gross_value=plan.gross_buy_value,
                fees=total_fees_this_rotation,
                signal_id=signal.id,
                happened_at=now,
                payload={
                    "source_units": str(plan.source_units),
                    "gross_sell_value": str(plan.gross_sell_value),
                    "cash_after_sell": str(plan.cash_after_sell),
                    "target_units": str(plan.target_units),
                    "cash_after": str(plan.cash_after),
                    "realized_pnl_on_source": str(plan.realized_pnl),
                },
            )
        )

        session.add(
            Transaction(
                cycle_id=cycle_id,
                signal_id=signal.id,
                strategy_id=self.config.strategy_id,
                position_id=position.position_id,
                action="ROTATE",
                source_fund_id=source_id,
                target_fund_id=int(target_id),
                units=plan.target_units,
                source_units=plan.source_units,
                target_units=plan.target_units,
                source_bid=source_bid,
                target_ask=target_ask,
                gross_value=plan.gross_buy_value,
                sell_fee=plan.sell_fee,
                buy_fee=plan.buy_fee,
                bid_ask_cost=bid_ask_cost,
                total_transaction_cost=total_fees_this_rotation + bid_ask_cost,
                portfolio_before=portfolio_before,
                portfolio_after=portfolio_after,
                reason="RELATIVE_VALUE_ROTATION",
                executed_at=now,
                details={
                    "gross_sell_value": str(plan.gross_sell_value),
                    "source_units": str(plan.source_units),
                    "target_units": str(plan.target_units),
                    "cash_before": str(cash_before),
                    "cash_after": str(plan.cash_after),
                    "net_edge_at_signal": (
                        str(signal.net_executable_edge)
                        if signal.net_executable_edge is not None
                        else None
                    ),
                },
            )
        )

        signal.account_had_capacity = True
        signal.trade_executed = True
        signal.non_execution_reason = None

        cumulative_realized = Decimal(str(state.get("realized_pnl", "0"))) + plan.realized_pnl
        cumulative_fees = Decimal(str(state.get("fees_total", "0"))) + total_fees_this_rotation
        cumulative_turnover = Decimal(str(state.get("turnover", "0"))) + plan.gross_sell_value + plan.gross_buy_value

        state.update(
            {
                "cash": str(plan.cash_after),
                "realized_pnl": str(cumulative_realized),
                "fees_total": str(cumulative_fees),
                "turnover": str(cumulative_turnover),
                "current_position_id": position.position_id,
                "current_fund_id": int(target_id),
                "rotation_count": int(position.rotations_count),
                "last_rotation_at": now.isoformat(),
            }
        )
        self._save_account_state(session, state)
        return True

    def _mark_position(
        self,
        position: PositionCurrent,
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        snap = funds.get(int(position.current_fund_id))
        if snap is None:
            return
        mark = self._mark_price(snap)
        if mark is None:
            return
        units = Decimal(str(position.units))
        market_value = units * mark
        position.mark_price = mark
        position.market_value = market_value
        position.unrealized_pnl = market_value - Decimal(str(position.cost_basis or "0"))

    def _write_account_snapshot(
        self,
        session: Session,
        *,
        cycle_id: int,
        position: PositionCurrent,
        funds: Mapping[int, FundSnapshot],
    ) -> None:
        # Avoid duplicate account snapshots if execution method is retried for same cycle.
        existing = session.scalar(
            select(AccountSnapshot).where(
                AccountSnapshot.cycle_id == cycle_id,
                AccountSnapshot.strategy_id == self.config.strategy_id,
            )
        )
        if existing is not None:
            return

        self._mark_position(position, funds)
        state = self._load_account_state(session)

        cash = Decimal(str(state.get("cash", "0")))
        market_value = Decimal(str(position.market_value or "0"))
        portfolio = cash + market_value
        initial = Decimal(str(state.get("initial_capital", self.config.initial_capital_irr)))
        realized = Decimal(str(state.get("realized_pnl", "0")))
        fees_total = Decimal(str(state.get("fees_total", "0")))
        turnover = Decimal(str(state.get("turnover", "0")))
        high_watermark = Decimal(str(state.get("high_watermark", initial)))

        if portfolio > high_watermark:
            high_watermark = portfolio
        drawdown = (portfolio / high_watermark - ONE) if high_watermark > ZERO else ZERO
        total_return = (portfolio / initial - ONE) if initial > ZERO else ZERO

        state["high_watermark"] = str(high_watermark)
        state["current_position_id"] = position.position_id
        state["current_fund_id"] = int(position.current_fund_id)
        self._save_account_state(session, state)

        session.add(
            AccountSnapshot(
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                portfolio_value=portfolio,
                cash=cash,
                gold_exposure=market_value,
                fixed_income_value=ZERO,
                realized_pnl=realized,
                unrealized_pnl=Decimal(str(position.unrealized_pnl or "0")),
                total_return=total_return,
                fees_total=fees_total,
                turnover=turnover,
                drawdown=drawdown,
                active_positions_count=1,
                active_funds=[int(position.current_fund_id)],
                captured_at=self._now(),
            )
        )

    def _write_uninitialized_account_snapshot(
        self,
        session: Session,
        *,
        cycle_id: int,
    ) -> None:
        existing = session.scalar(
            select(AccountSnapshot).where(
                AccountSnapshot.cycle_id == cycle_id,
                AccountSnapshot.strategy_id == self.config.strategy_id,
            )
        )
        if existing is not None:
            return

        state = self._load_account_state(session)
        initial = Decimal(str(state.get("initial_capital", self.config.initial_capital_irr)))
        cash = Decimal(str(state.get("cash", initial)))
        high_watermark = Decimal(str(state.get("high_watermark", initial)))

        session.add(
            AccountSnapshot(
                cycle_id=cycle_id,
                strategy_id=self.config.strategy_id,
                portfolio_value=cash,
                cash=cash,
                gold_exposure=ZERO,
                fixed_income_value=ZERO,
                realized_pnl=ZERO,
                unrealized_pnl=ZERO,
                total_return=(cash / initial - ONE) if initial > ZERO else ZERO,
                fees_total=Decimal(str(state.get("fees_total", "0"))),
                turnover=Decimal(str(state.get("turnover", "0"))),
                drawdown=(cash / high_watermark - ONE) if high_watermark > ZERO else ZERO,
                active_positions_count=0,
                active_funds=[],
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
                position = self._open_position(session)

                # Bootstrap once. It is intentionally not tied to a market signal.
                if position is None:
                    try:
                        position = self._bootstrap(
                            session,
                            cycle_id=cycle_id,
                            funds=funds,
                        )
                    except (RuntimeError, ValueError):
                        # Initialization is deferred until a valid Ayyar order book exists.
                        # Do not crash the whole shared pipeline because Strategy A is not
                        # initialized yet; preserve the account as cash for this cycle.
                        self._write_uninitialized_account_snapshot(
                            session, cycle_id=cycle_id
                        )
                        return

                # Strategy A should emit at most one rotation per cycle.
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

                executed_one = False
                for signal in signals:
                    if signal.signal_type != "ROTATE_TO":
                        signal.account_had_capacity = True
                        signal.trade_executed = False
                        signal.non_execution_reason = "UNSUPPORTED_SIGNAL_TYPE"
                        continue

                    if executed_one:
                        signal.account_had_capacity = True
                        signal.trade_executed = False
                        signal.non_execution_reason = "MULTIPLE_STRATEGY_A_SIGNALS_SAME_CYCLE"
                        continue

                    executed_one = self._execute_rotation(
                        session,
                        cycle_id=cycle_id,
                        signal=signal,
                        position=position,
                        funds=funds,
                    )

                # HOLD cycles are still recorded in account history.
                self._write_account_snapshot(
                    session,
                    cycle_id=cycle_id,
                    position=position,
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
        Never bootstraps, never emits/executes a trade.
        """
        if strategy_id != self.config.strategy_id:
            return

        with self.session_factory() as session:
            with session.begin():
                position = self._open_position(session)
                if position is None:
                    self._write_uninitialized_account_snapshot(
                        session, cycle_id=cycle_id
                    )
                    return

                self._write_account_snapshot(
                    session,
                    cycle_id=cycle_id,
                    position=position,
                    funds=funds,
                )

def load_strategy_a_runtime_state(session: Session, strategy_id: str = "RELATIVE_BUY_HOLD") -> dict:
    """
    State loader compatible with UnifiedTradingPipeline's repository contract.
    It derives the current holding from positions_current, then merges the
    persisted account state. This makes VPS restart recovery deterministic.
    """
    position = session.scalar(
        select(PositionCurrent)
        .where(
            PositionCurrent.strategy_id == strategy_id,
            PositionCurrent.status == "OPEN",
        )
        .order_by(PositionCurrent.position_id.asc())
        .limit(1)
    )
    row = session.scalar(
        select(StrategyRuntimeState).where(
            StrategyRuntimeState.strategy_id == strategy_id,
            StrategyRuntimeState.scope_key == "GLOBAL",
            StrategyRuntimeState.state_key == "account",
        )
    )

    state = dict(row.state_value or {}) if row else {}
    if position is not None:
        state.update(
            {
                "current_position_id": position.position_id,
                "current_fund_id": int(position.current_fund_id),
                "entry_time": position.opened_at.isoformat(),
                "units": str(position.units),
                "rotation_count": int(position.rotations_count or 0),
            }
        )
    return state
