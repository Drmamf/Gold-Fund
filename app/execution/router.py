from __future__ import annotations

from typing import Mapping

from app.contracts import CommonSnapshot, FundSnapshot, FundValuation


class StrategyExecutorRouter:
    """Shared pipeline adapter that dispatches execution by strategy_id."""

    def __init__(self, executors: Mapping[str, object]):
        self.executors = dict(executors)

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
        executor = self.executors.get(strategy_id)
        if executor is None:
            raise RuntimeError(f"No executor registered for strategy_id={strategy_id!r}")

        executor.execute_strategy_signals(
            cycle_id=cycle_id,
            strategy_id=strategy_id,
            signal_ids=signal_ids,
            common=common,
            funds=funds,
            valuations=valuations,
        )

    def mark_strategy_account(
        self,
        *,
        cycle_id: int,
        strategy_id: str,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
    ) -> None:
        executor = self.executors.get(strategy_id)
        if executor is None:
            raise RuntimeError(f"No executor registered for strategy_id={strategy_id!r}")

        marker = getattr(executor, "mark_account_only", None)
        if marker is None:
            raise RuntimeError(
                f"Executor {type(executor).__name__} does not implement mark_account_only()."
            )

        marker(
            cycle_id=cycle_id,
            strategy_id=strategy_id,
            common=common,
            funds=funds,
            valuations=valuations,
        )

