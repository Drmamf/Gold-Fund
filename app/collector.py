from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import time
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

import requests

from app.config_loader import (
    InstrumentMarketConfig,
    ProjectConfig,
)
from app.contracts import CommonSnapshot, FundSnapshot
from app.providers.api_guard import ProviderCallGuard
from app.providers.ime_adapter import IMEAdapter, IMEMarketSnapshot
from app.providers.tgju_adapter import TGJUAdapter, build_adapter_from_config
from app.providers.tsetmc_adapter import (
    TSETMCAdapter,
    TSETMCFundRawSnapshot,
)


TEHRAN = ZoneInfo("Asia/Tehran")
ZERO = Decimal("0")


def _d(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


class SharedMarketCollector:
    """
    The ONLY layer allowed to call TGJU / IME / TSETMC.

    Strict executable valuation policy:
      * Fund signal/valuation price = TSETMC Best Ask ONLY.
      * IME bullion/coin price = IME Best Ask ONLY.
      * Gold-fund NAV = TSETMC pRedTran ONLY.
      * No midpoint / last / close / settlement fallback.
      * Missing required input => invalid snapshot => fail closed.

    Selling is different: Best Bid is still retained for actual sell/rotation
    execution, because that is the realizable sell price.
    """

    def __init__(
        self,
        *,
        config: ProjectConfig,
        instrument_ids: Mapping[str, int],
        notifications=None,
        session: Optional[requests.Session] = None,
    ):
        self.config = config
        self.instrument_ids = {
            str(k): int(v) for k, v in instrument_ids.items()
        }
        self.notifications = notifications
        self.session = session or requests.Session()
        self._owns_session = session is None

        self.tgju = build_adapter_from_config(config.market)
        self.ime = IMEAdapter.from_config(config.market)
        self.tsetmc = TSETMCAdapter.from_config(config.market)
        self.guard = (
            ProviderCallGuard(notifications)
            if notifications is not None
            else None
        )
        self.fund_sleep = float(
            config.market["data_sources"]["tsetmc"].get(
                "request_sleep_seconds", 0.0
            )
        )

        required_symbols = {
            row.symbol for row in config.instruments
        }
        missing = sorted(
            required_symbols.difference(self.instrument_ids)
        )
        if missing:
            raise ValueError(
                "instrument_ids missing DB IDs for: "
                + ", ".join(missing)
            )

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _warn(
        self,
        *,
        source: str,
        operation: str,
        error: Exception | str,
        cycle_id: int | None,
        instrument_symbol: str | None = None,
        endpoint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.notifications is None:
            return
        self.notifications.notify_api_error(
            source=source,
            operation=operation,
            error=error,
            cycle_id=cycle_id,
            instrument_symbol=instrument_symbol,
            endpoint=endpoint,
            details=details,
        )

    def _guarded(
        self,
        *,
        source: str,
        operation: str,
        fn,
        cycle_id: int | None,
        instrument_symbol: str | None = None,
        endpoint: str | None = None,
    ):
        if self.guard is None:
            return fn()
        return self.guard.call(
            source=source,
            operation=operation,
            fn=fn,
            cycle_id=cycle_id,
            instrument_symbol=instrument_symbol,
            endpoint=endpoint,
        )

    def _collect_tgju(self, cycle_id: int | None) -> dict[str, Any]:
        # TGJU adapter has internal retry/fail-closed behavior and returns
        # api_ok=False rather than raising.
        result = self.tgju.fetch_market_snapshot(self.session)

        if not result.get("api_ok"):
            self._warn(
                source="TGJU",
                operation="fetch_market_snapshot",
                error=result.get("api_error") or "TGJU API failure",
                cycle_id=cycle_id,
                endpoint=self.tgju.endpoint,
            )
            return result

        usd = result.get("usd") or {}
        ounce = result.get("gold_ounce") or {}

        if not usd.get("usable"):
            self._warn(
                source="TGJU",
                operation="validate_usd_irr",
                error=usd.get("error") or "USD quote unusable",
                cycle_id=cycle_id,
                endpoint=self.tgju.endpoint,
                details={"quote": usd},
            )
        if not ounce.get("usable"):
            self._warn(
                source="TGJU",
                operation="validate_gold_ounce",
                error=ounce.get("error") or "Ounce quote unusable",
                cycle_id=cycle_id,
                endpoint=self.tgju.endpoint,
                details={"quote": ounce},
            )
        return result

    def _collect_ime(
        self,
        cycle_id: int | None,
    ) -> IMEMarketSnapshot | None:
        endpoint = self.ime.endpoint
        try:
            snap = self._guarded(
                source="IME",
                operation="fetch_live_market",
                fn=lambda: self.ime.fetch_market_snapshot(self.session),
                cycle_id=cycle_id,
                endpoint=endpoint,
            )
        except Exception:
            return None

        if snap.bullion is None:
            self._warn(
                source="IME",
                operation="validate_GoldBar",
                error="GoldBar contract not found in CDCLiveMarket response",
                cycle_id=cycle_id,
                instrument_symbol="GoldBar",
                endpoint=endpoint,
            )
        elif not snap.bullion.valuation_valid:
            self._warn(
                source="IME",
                operation="validate_GoldBar_best_ask",
                error="GoldBar Best Ask is missing/invalid; no fallback allowed",
                cycle_id=cycle_id,
                instrument_symbol="GoldBar",
                endpoint=endpoint,
            )

        if snap.coin is None:
            self._warn(
                source="IME",
                operation="validate_GoldCoin",
                error="GoldCoin contract not found in CDCLiveMarket response",
                cycle_id=cycle_id,
                instrument_symbol="GoldCoin",
                endpoint=endpoint,
            )
        elif not snap.coin.valuation_valid:
            self._warn(
                source="IME",
                operation="validate_GoldCoin_best_ask",
                error="GoldCoin Best Ask is missing/invalid; no fallback allowed",
                cycle_id=cycle_id,
                instrument_symbol="GoldCoin",
                endpoint=endpoint,
            )

        return snap

    def _collect_tsetmc_instrument(
        self,
        instrument: InstrumentMarketConfig,
        cycle_id: int | None,
    ) -> TSETMCFundRawSnapshot:
        errors: dict[str, str] = {}

        price = None
        price_url = self.tsetmc.price_url.format(
            ins_code=instrument.ins_code
        )
        try:
            price = self._guarded(
                source="TSETMC",
                operation="fetch_price_activity",
                fn=lambda: self.tsetmc.fetch_price_activity(
                    self.session, instrument
                ),
                cycle_id=cycle_id,
                instrument_symbol=instrument.symbol,
                endpoint=price_url,
            )
        except Exception as exc:
            errors["price_activity"] = str(exc)

        nav = None
        if instrument.requires_nav_redemption:
            nav_url = self.tsetmc.nav_url.format(
                ins_code=instrument.ins_code
            )
            try:
                nav = self._guarded(
                    source="TSETMC",
                    operation="fetch_nav_redemption",
                    fn=lambda: self.tsetmc.fetch_nav_redemption(
                        self.session, instrument
                    ),
                    cycle_id=cycle_id,
                    instrument_symbol=instrument.symbol,
                    endpoint=nav_url,
                )
            except Exception as exc:
                errors["nav"] = str(exc)

        order_book = None
        order_url = self.tsetmc.order_book_url.format(
            ins_code=instrument.ins_code
        )
        try:
            order_book = self._guarded(
                source="TSETMC",
                operation="fetch_order_book",
                fn=lambda: self.tsetmc.fetch_order_book(
                    self.session, instrument
                ),
                cycle_id=cycle_id,
                instrument_symbol=instrument.symbol,
                endpoint=order_url,
            )
        except Exception as exc:
            errors["order_book"] = str(exc)

        return TSETMCFundRawSnapshot(
            instrument=instrument,
            fetched_at=datetime.now(TEHRAN),
            price=price,
            nav=nav,
            order_book=order_book,
            errors=errors,
        )

    def collect(
        self,
        *,
        cycle_id: int | None = None,
    ) -> tuple[CommonSnapshot, Mapping[int, FundSnapshot]]:
        collected_at = datetime.now(TEHRAN)

        # Common providers are called once per cycle.
        tgju = self._collect_tgju(cycle_id)
        ime = self._collect_ime(cycle_id)

        usd_row = tgju.get("usd") or {}
        ounce_row = tgju.get("gold_ounce") or {}

        usd = (
            _d(usd_row.get("price"))
            if usd_row.get("usable")
            else None
        )
        ounce = (
            _d(ounce_row.get("price"))
            if ounce_row.get("usable")
            else None
        )
        bullion_ask = (
            ime.bullion.valuation_price
            if ime and ime.bullion and ime.bullion.valuation_valid
            else None
        )
        coin_ask = (
            ime.coin.valuation_price
            if ime and ime.coin and ime.coin.valuation_valid
            else None
        )

        common_usable = bool(
            usd is not None
            and usd > ZERO
            and ounce is not None
            and ounce > ZERO
            and bullion_ask is not None
            and bullion_ask > ZERO
            and coin_ask is not None
            and coin_ask > ZERO
        )

        common = CommonSnapshot(
            collected_at=collected_at,
            usd_irr=usd,
            ounce_usd=ounce,
            ime_bullion_price=bullion_ask,
            ime_coin_price=coin_ask,
            bullion_bubble=None,
            coin_bubble=None,
            valuation_inputs_usable=common_usable,
            raw={
                "tgju": tgju,
                "ime": ime.raw_payload() if ime else None,
                "strict_pricing_policy": {
                    "fund": "BEST_ASK_ONLY",
                    "ime": "BEST_ASK_ONLY",
                    "nav": "TSETMC_REDEMPTION_ONLY",
                    "fallback": False,
                },
            },
        )

        snapshots: dict[int, FundSnapshot] = {}

        for index, instrument in enumerate(self.config.instruments):
            raw = self._collect_tsetmc_instrument(
                instrument, cycle_id
            )
            fund_id = self.instrument_ids[instrument.symbol]

            price = raw.price
            nav = raw.nav
            ob = raw.order_book

            snapshots[fund_id] = FundSnapshot(
                fund_id=fund_id,
                symbol=instrument.symbol,
                # Kept only for audit/account reports. NOT a valuation fallback.
                close_price=(
                    price.close_price
                    if price and price.close_price is not None
                    else Decimal("0")
                ),
                nav_redemption=(
                    nav.nav_redemption
                    if nav is not None
                    else Decimal("0")
                ),
                best_bid=(
                    ob.best_bid
                    if ob and ob.best_bid is not None
                    else Decimal("0")
                ),
                best_ask=(
                    ob.best_ask
                    if ob is not None
                    else Decimal("0")
                ),
                trade_value=(
                    price.trade_value
                    if price and price.trade_value is not None
                    else Decimal("0")
                ),
                trade_count=(
                    int(price.trade_count)
                    if price and price.trade_count is not None
                    else 0
                ),
                # For gold funds this means valid for ASK-based valuation/signals.
                # Best Bid can still be absent; sell execution then fails closed.
                data_valid=raw.valuation_valid,
                signal_price=raw.signal_price,
                raw=raw.raw_payload(),
            )

            if self.fund_sleep > 0 and index < len(self.config.instruments) - 1:
                time.sleep(self.fund_sleep)

        return common, snapshots
