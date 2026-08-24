from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar


T = TypeVar("T")


class ProviderDataError(RuntimeError):
    """Provider answered, but required data is absent/invalid/stale."""


@dataclass
class ProviderCallGuard:
    """
    Wrap each logical API fetch in the Shared Collector.

    If the logical fetch still fails after any provider-internal retry policy,
    this guard emits exactly one Bale alert for that failed scheduled fetch
    and re-raises so the Collector can fail closed.
    """

    notifications: Any

    def call(
        self,
        *,
        source: str,
        operation: str,
        fn: Callable[..., T],
        cycle_id: int | None = None,
        instrument_symbol: str | None = None,
        endpoint: str | None = None,
        details: dict[str, Any] | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> T:
        try:
            return fn(*args, **(kwargs or {}))
        except Exception as exc:
            self.notifications.notify_api_error(
                source=source,
                operation=operation,
                error=exc,
                cycle_id=cycle_id,
                instrument_symbol=instrument_symbol,
                endpoint=endpoint,
                details=details,
            )
            raise
