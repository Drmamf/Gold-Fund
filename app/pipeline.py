from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Protocol, Mapping, Sequence

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
    StrategySignal,
    ValuationBatch,
)
from app.strategies.base import StrategyBase


class Collector(Protocol):
    def collect(
        self, *, cycle_id: int | None = None
    ) -> tuple[CommonSnapshot, Mapping[int, FundSnapshot]]:
        ...


class ValuationEngine(Protocol):
    def calculate(
        self,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        trade_date: date,
    ) -> ValuationBatch:
        ...


class RelativeValueEngine(Protocol):
    def calculate(
        self,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> Mapping[int, RelativeValueRow]:
        ...


class RepositoryLayer(Protocol):
    def start_cycle(
        self,
        *,
        market_date: date,
        cycle_type: str,
        scheduled_for: datetime,
        market_is_open: bool,
    ) -> int:
        ...

    def store_raw_market(self, cycle_id: int, common, funds) -> None:
        ...

    def store_valuations(self, cycle_id: int, common, valuations) -> None:
        ...

    def store_relative(self, cycle_id: int, relative_rows) -> None:
        ...

    def load_strategy_state(
        self, strategy_id: str, *, market_date: date
    ) -> dict:
        ...

    def store_signals(
        self,
        cycle_id: int,
        signals: Sequence[StrategySignal],
    ) -> tuple[list[int], list[StrategySignal]]:
        ...

    def complete_cycle(self, cycle_id: int) -> None:
        ...

    def fail_cycle(self, cycle_id: int, exc: Exception) -> None:
        ...


class Executor(Protocol):
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
        ...

    def mark_strategy_account(
        self,
        *,
        cycle_id: int,
        strategy_id: str,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> None:
        ...



class SignalNotifier(Protocol):
    def notify_signals(
        self,
        *,
        cycle_id: int,
        signals: Sequence[StrategySignal],
        funds: Mapping[int, FundSnapshot],
        at: datetime | None = None,
    ) -> None:
        ...


class DailyAggregator(Protocol):
    def upsert_current_day(self, trade_date: date) -> None:
        ...


@dataclass
class UnifiedTradingPipeline:
    collector: Collector
    valuation_engine: ValuationEngine
    relative_engine: RelativeValueEngine
    repository: RepositoryLayer
    executor: Executor
    daily_aggregator: DailyAggregator
    strategies: Sequence[StrategyBase]
    notifications: Optional[SignalNotifier] = None

    def _run_shared_core(
        self,
        *,
        trade_date: date,
        cycle_type: str,
        scheduled_for: datetime,
        market_is_open: bool,
    ):
        cycle_id = self.repository.start_cycle(
            market_date=trade_date,
            cycle_type=cycle_type,
            scheduled_for=scheduled_for,
            market_is_open=market_is_open,
        )

        try:
            common, funds = self.collector.collect(cycle_id=cycle_id)
            self.repository.store_raw_market(cycle_id, common, funds)

            valuation_batch = self.valuation_engine.calculate(
                common, funds, trade_date
            )
            common = valuation_batch.common
            valuations = valuation_batch.funds
            self.repository.store_valuations(cycle_id, common, valuations)

            relative_rows = self.relative_engine.calculate(
                common, funds, valuations
            )
            self.repository.store_relative(cycle_id, relative_rows)

            return cycle_id, common, funds, valuations, relative_rows

        except Exception as exc:
            self.repository.fail_cycle(cycle_id, exc)
            raise

    def run_warmup(
        self,
        *,
        trade_date: date,
        scheduled_for: datetime,
    ) -> int:
        cycle_id, common, funds, valuations, relative_rows = self._run_shared_core(
            trade_date=trade_date,
            cycle_type="WARMUP",
            scheduled_for=scheduled_for,
            market_is_open=True,
        )
        try:
            self.daily_aggregator.upsert_current_day(trade_date)
            self.repository.complete_cycle(cycle_id)
            return cycle_id
        except Exception as exc:
            self.repository.fail_cycle(cycle_id, exc)
            raise

    def run_active_cycle(
        self,
        *,
        trade_date: date,
        scheduled_for: datetime,
    ) -> int:
        cycle_id, common, funds, valuations, relative_rows = self._run_shared_core(
            trade_date=trade_date,
            cycle_type="ACTIVE",
            scheduled_for=scheduled_for,
            market_is_open=True,
        )

        try:
            for strategy in self.strategies:
                runtime_state = self.repository.load_strategy_state(
                    strategy.strategy_id, market_date=trade_date
                )

                signals = strategy.generate_signals(
                    common=common,
                    funds=funds,
                    valuations=valuations,
                    relative_rows=relative_rows,
                    runtime_state=runtime_state,
                )

                signal_ids, stored_signals = self.repository.store_signals(
                    cycle_id, signals
                )

                # Bale reports only persisted market SIGNALs.
                # Deduplicated signals are not notified.
                if self.notifications is not None and stored_signals:
                    self.notifications.notify_signals(
                        cycle_id=cycle_id,
                        signals=stored_signals,
                        funds=funds,
                        at=scheduled_for,
                    )

                self.executor.execute_strategy_signals(
                    cycle_id=cycle_id,
                    strategy_id=strategy.strategy_id,
                    signal_ids=signal_ids,
                    common=common,
                    funds=funds,
                    valuations=valuations,
                )

            self.daily_aggregator.upsert_current_day(trade_date)
            self.repository.complete_cycle(cycle_id)
            return cycle_id

        except Exception as exc:
            self.repository.fail_cycle(cycle_id, exc)
            raise

    def run_close(
        self,
        *,
        trade_date: date,
        scheduled_for: datetime,
    ) -> int:
        cycle_id, common, funds, valuations, relative_rows = self._run_shared_core(
            trade_date=trade_date,
            cycle_type="CLOSE",
            scheduled_for=scheduled_for,
            market_is_open=False,
        )

        try:
            for strategy in self.strategies:
                self.executor.mark_strategy_account(
                    cycle_id=cycle_id,
                    strategy_id=strategy.strategy_id,
                    common=common,
                    funds=funds,
                    valuations=valuations,
                )

            self.daily_aggregator.upsert_current_day(trade_date)
            self.repository.complete_cycle(cycle_id)
            return cycle_id

        except Exception as exc:
            self.repository.fail_cycle(cycle_id, exc)
            raise

    def run_cycle(
        self,
        trade_date: date,
        scheduled_for: datetime,
    ) -> int:
        return self.run_active_cycle(
            trade_date=trade_date,
            scheduled_for=scheduled_for,
        )
