from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Sequence

from app.contracts import (
    CommonSnapshot,
    FundSnapshot,
    FundValuation,
    RelativeValueRow,
    StrategySignal,
)


class StrategyBase(ABC):
    """
    Strategies never fetch market data and never write directly to PostgreSQL.
    They consume the already-persisted common/valuation/relative context and
    emit account-independent signals.
    """

    strategy_id: str

    @abstractmethod
    def generate_signals(
        self,
        *,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        valuations: Mapping[int, FundValuation],
        relative_rows: Mapping[int, RelativeValueRow],
        runtime_state: dict,
    ) -> Sequence[StrategySignal]:
        raise NotImplementedError
