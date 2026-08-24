from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Optional


ZERO = Decimal("0")


def _d(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


@dataclass(frozen=True)
class StrategyBEntryState:
    """
    Account-level staged-entry state for Strategy B.

    Normal path:
        Entry N executes -> gate closes -> the executed fund must rearm by
        +1.50pp above its BUY threshold -> gate opens -> Entry N+1 may execute.

    Exception:
        Only while exactly one entry has executed, if that first entry has NOT
        rearmed, the MA7 trade-value rule may create Entry #2 on a later
        trading day. MA7 is never used for Entry #3, #4, ...
    """

    entry_count: int = 0
    threshold_gate_open: bool = True

    rearm_reference_fund_id: Optional[int] = None
    rearm_reference_buy_threshold: Optional[Decimal] = None
    rearm_threshold: Optional[Decimal] = None
    last_rearm_achieved_at: Optional[str] = None

    last_entry_position_id: Optional[int] = None
    last_entry_fund_id: Optional[int] = None
    last_entry_number: Optional[int] = None
    last_entry_route: Optional[str] = None
    last_entry_at: Optional[str] = None
    last_entry_market_date: Optional[date] = None

    ma7_fallback_eligible: bool = False
    ma7_fallback_consumed: bool = False
    ma7_fallback_since_date: Optional[date] = None
    ma7_primary_position_id: Optional[int] = None
    ma7_primary_fund_id: Optional[int] = None
    ma7_fallback_closed_reason: Optional[str] = None

    @classmethod
    def from_mapping(cls, state: Mapping[str, Any] | None) -> "StrategyBEntryState":
        s = dict(state or {})
        count = int(s.get("entry_count", 0) or 0)
        default_gate = count == 0
        return cls(
            entry_count=count,
            threshold_gate_open=bool(s.get("threshold_gate_open", default_gate)),
            rearm_reference_fund_id=(
                int(s["rearm_reference_fund_id"])
                if s.get("rearm_reference_fund_id") is not None else None
            ),
            rearm_reference_buy_threshold=_d(s.get("rearm_reference_buy_threshold")),
            rearm_threshold=_d(s.get("rearm_threshold")),
            last_rearm_achieved_at=s.get("last_rearm_achieved_at"),
            last_entry_position_id=(
                int(s["last_entry_position_id"])
                if s.get("last_entry_position_id") is not None else None
            ),
            last_entry_fund_id=(
                int(s["last_entry_fund_id"])
                if s.get("last_entry_fund_id") is not None else None
            ),
            last_entry_number=(
                int(s["last_entry_number"])
                if s.get("last_entry_number") is not None else None
            ),
            last_entry_route=s.get("last_entry_route"),
            last_entry_at=s.get("last_entry_at"),
            last_entry_market_date=_date(s.get("last_entry_market_date")),
            ma7_fallback_eligible=bool(s.get("ma7_fallback_eligible", False)),
            ma7_fallback_consumed=bool(s.get("ma7_fallback_consumed", False)),
            ma7_fallback_since_date=_date(s.get("ma7_fallback_since_date")),
            ma7_primary_position_id=(
                int(s["ma7_primary_position_id"])
                if s.get("ma7_primary_position_id") is not None else None
            ),
            ma7_primary_fund_id=(
                int(s["ma7_primary_fund_id"])
                if s.get("ma7_primary_fund_id") is not None else None
            ),
            ma7_fallback_closed_reason=s.get("ma7_fallback_closed_reason"),
        )

    @property
    def next_entry_number(self) -> int:
        return self.entry_count + 1

    def preview_rearm(
        self,
        *,
        current_total_bubble: Optional[Decimal],
        achieved_at: str,
    ) -> "StrategyBEntryState":
        """Pure preview/apply of the account-level +1.50pp rearm gate."""
        if self.threshold_gate_open:
            return self
        if self.rearm_threshold is None or current_total_bubble is None:
            return self
        if current_total_bubble < self.rearm_threshold:
            return self

        updates = dict(
            threshold_gate_open=True,
            last_rearm_achieved_at=achieved_at,
        )
        # The MA7 exception exists only if Entry #1 never got its rearm.
        if self.entry_count == 1 and self.ma7_fallback_eligible:
            updates.update(
                ma7_fallback_eligible=False,
                ma7_fallback_closed_reason="REARM_ACHIEVED_BEFORE_MA7_ENTRY2",
            )
        return replace(self, **updates)

    def after_executed_entry(
        self,
        *,
        fund_id: int,
        position_id: int,
        buy_threshold: Decimal,
        rearm_margin: Decimal,
        route: str,
        executed_at: str,
        market_date: date,
    ) -> "StrategyBEntryState":
        next_number = self.entry_count + 1
        route = str(route).upper()

        updates = dict(
            entry_count=next_number,
            threshold_gate_open=False,
            rearm_reference_fund_id=int(fund_id),
            rearm_reference_buy_threshold=Decimal(str(buy_threshold)),
            rearm_threshold=Decimal(str(buy_threshold)) + Decimal(str(rearm_margin)),
            last_entry_position_id=int(position_id),
            last_entry_fund_id=int(fund_id),
            last_entry_number=next_number,
            last_entry_route=route,
            last_entry_at=executed_at,
            last_entry_market_date=market_date,
            last_rearm_achieved_at=None,
        )

        if next_number == 1:
            updates.update(
                ma7_fallback_eligible=True,
                ma7_fallback_consumed=False,
                ma7_fallback_since_date=market_date,
                ma7_primary_position_id=int(position_id),
                ma7_primary_fund_id=int(fund_id),
                ma7_fallback_closed_reason=None,
            )
        elif next_number >= 2:
            updates.update(
                ma7_fallback_eligible=False,
                ma7_fallback_consumed=(route == "MA7_FALLBACK"),
                ma7_fallback_since_date=None,
                ma7_primary_position_id=None,
                ma7_primary_fund_id=None,
                ma7_fallback_closed_reason=(
                    "MA7_ENTRY2_EXECUTED"
                    if route == "MA7_FALLBACK"
                    else "ENTRY2_OR_LATER_EXECUTED_VIA_THRESHOLD"
                ),
            )

        return replace(self, **updates)

    def ma7_fallback_allowed(self, market_date: date) -> bool:
        if self.entry_count != 1:
            return False
        if self.threshold_gate_open:
            return False
        if not self.ma7_fallback_eligible or self.ma7_fallback_consumed:
            return False
        if self.ma7_fallback_since_date is None:
            return False
        # The bot runs only on trading sessions; therefore a later observed
        # market date is a later trading day, including across holidays/weekends.
        return market_date > self.ma7_fallback_since_date

    def merge_into(self, state: Mapping[str, Any] | None) -> dict[str, Any]:
        out = dict(state or {})
        out.update(
            {
                "entry_count": self.entry_count,
                "threshold_gate_open": self.threshold_gate_open,
                "rearm_reference_fund_id": self.rearm_reference_fund_id,
                "rearm_reference_buy_threshold": (
                    str(self.rearm_reference_buy_threshold)
                    if self.rearm_reference_buy_threshold is not None else None
                ),
                "rearm_threshold": (
                    str(self.rearm_threshold) if self.rearm_threshold is not None else None
                ),
                "last_rearm_achieved_at": self.last_rearm_achieved_at,
                "last_entry_position_id": self.last_entry_position_id,
                "last_entry_fund_id": self.last_entry_fund_id,
                "last_entry_number": self.last_entry_number,
                "last_entry_route": self.last_entry_route,
                "last_entry_at": self.last_entry_at,
                "last_entry_market_date": (
                    self.last_entry_market_date.isoformat()
                    if self.last_entry_market_date else None
                ),
                "ma7_fallback_eligible": self.ma7_fallback_eligible,
                "ma7_fallback_consumed": self.ma7_fallback_consumed,
                "ma7_fallback_since_date": (
                    self.ma7_fallback_since_date.isoformat()
                    if self.ma7_fallback_since_date else None
                ),
                "ma7_primary_position_id": self.ma7_primary_position_id,
                "ma7_primary_fund_id": self.ma7_primary_fund_id,
                "ma7_fallback_closed_reason": self.ma7_fallback_closed_reason,
            }
        )
        # Remove superseded v1 pending-second-entry keys from persisted state.
        for key in (
            "pending_second_entry",
            "pending_second_entry_since",
            "pending_primary_position_id",
            "pending_primary_fund_id",
        ):
            out.pop(key, None)
        return out
