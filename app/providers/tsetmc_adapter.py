from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from app.config_loader import InstrumentMarketConfig


TEHRAN = ZoneInfo("Asia/Tehran")
ZERO = Decimal("0")


class TSETMCError(RuntimeError):
    pass


class TSETMCDataError(TSETMCError):
    pass


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        if value == "":
            return None
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _positive(value: Any) -> Optional[Decimal]:
    result = _decimal(value)
    return result if result is not None and result > ZERO else None


def _integer(value: Any) -> Optional[int]:
    result = _decimal(value)
    return int(result) if result is not None else None


def _deven_heven(deven: Any, heven: Any) -> Optional[datetime]:
    try:
        d = str(int(Decimal(str(deven))))
        h = str(int(Decimal(str(heven)))).zfill(6)
        if len(d) != 8:
            return None
        dt = datetime.strptime(
            f"{d} {h[:2]}:{h[2:4]}:{h[4:6]}",
            "%Y%m%d %H:%M:%S",
        )
        return dt.replace(tzinfo=TEHRAN)
    except Exception:
        return None


@dataclass(frozen=True)
class TSETMCPriceActivity:
    last_price: Optional[Decimal]
    close_price: Optional[Decimal]
    trade_value: Optional[Decimal]
    trade_volume: Optional[Decimal]
    trade_count: Optional[int]
    update_time: Optional[datetime]
    raw: dict[str, Any]


@dataclass(frozen=True)
class TSETMCNav:
    nav_redemption: Decimal
    nav_issuance: Optional[Decimal]
    update_time: Optional[datetime]
    raw: dict[str, Any]


@dataclass(frozen=True)
class TSETMCOrderBook:
    best_bid: Optional[Decimal]
    best_bid_volume: Optional[Decimal]
    best_bid_count: Optional[int]
    best_ask: Decimal
    best_ask_volume: Optional[Decimal]
    best_ask_count: Optional[int]
    raw: dict[str, Any]


@dataclass(frozen=True)
class TSETMCFundRawSnapshot:
    instrument: InstrumentMarketConfig
    fetched_at: datetime
    price: Optional[TSETMCPriceActivity]
    nav: Optional[TSETMCNav]
    order_book: Optional[TSETMCOrderBook]
    errors: dict[str, str]

    @property
    def signal_price(self) -> Optional[Decimal]:
        # STRICT PROJECT RULE: only cheapest current seller is valid.
        return self.order_book.best_ask if self.order_book else None

    @property
    def buy_exec_price(self) -> Optional[Decimal]:
        return self.signal_price

    @property
    def sell_exec_price(self) -> Optional[Decimal]:
        return self.order_book.best_bid if self.order_book else None

    @property
    def valuation_valid(self) -> bool:
        if self.signal_price is None:
            return False
        if self.instrument.requires_nav_redemption and self.nav is None:
            return False
        if self.instrument.is_gold_fund:
            if self.price is None:
                return False
            if self.price.trade_value is None or self.price.trade_value <= ZERO:
                return False
            if int(self.price.trade_count or 0) <= 0:
                return False
        return True

    def raw_payload(self) -> dict[str, Any]:
        return {
            "instrument": asdict(self.instrument),
            "fetched_at": self.fetched_at.isoformat(),
            "price": asdict(self.price) if self.price else None,
            "nav": asdict(self.nav) if self.nav else None,
            "order_book": asdict(self.order_book) if self.order_book else None,
            "errors": dict(self.errors),
            "strict_policy": {
                "signal_price_source": "BEST_ASK_ONLY",
                "nav_source": (
                    "TSETMC_pRedTran"
                    if self.instrument.requires_nav_redemption
                    else "NOT_REQUIRED"
                ),
                "fallback_allowed": False,
            },
        }


class TSETMCAdapter:
    def __init__(
        self,
        *,
        price_url: str,
        nav_url: str,
        order_book_url: str,
        timeout_seconds: float = 15,
        retries: int = 3,
        retry_backoff_seconds: float = 1.2,
        headers: Optional[dict[str, str]] = None,
    ):
        self.price_url = price_url
        self.nav_url = nav_url
        self.order_book_url = order_book_url
        self.timeout_seconds = float(timeout_seconds)
        self.retries = int(retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.headers = headers or {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://tsetmc.com",
            "Referer": "https://tsetmc.com/",
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TSETMCAdapter":
        src = config["data_sources"]["tsetmc"]
        net = config.get("network", {})
        return cls(
            price_url=src["price_url"],
            nav_url=src["nav_url"],
            order_book_url=src["order_book_url"],
            timeout_seconds=net.get("timeout_seconds", 15),
            retries=net.get("retries", 3),
            retry_backoff_seconds=net.get("retry_backoff_seconds", 1.2),
        )

    def _get_json(
        self,
        session: requests.Session,
        url: str,
        operation: str,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = session.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_backoff_seconds * attempt)
        raise TSETMCError(
            f"{operation} failed after {self.retries} attempts: {last_error}"
        )

    def fetch_price_activity(
        self,
        session: requests.Session,
        instrument: InstrumentMarketConfig,
    ) -> TSETMCPriceActivity:
        url = self.price_url.format(ins_code=instrument.ins_code)
        data = self._get_json(
            session, url, f"TSETMC_PRICE:{instrument.symbol}"
        )
        if not isinstance(data, dict):
            raise TSETMCDataError("ClosingPrice response root is not an object.")

        info = data.get("closingPriceInfo", data)
        if not isinstance(info, dict):
            raise TSETMCDataError("closingPriceInfo is missing/invalid.")

        return TSETMCPriceActivity(
            last_price=_positive(info.get("pDrCotVal")),
            close_price=_positive(info.get("pClosing")),
            trade_value=_positive(info.get("qTotCap")),
            trade_volume=_positive(info.get("qTotTran5J")),
            trade_count=_integer(info.get("zTotTran")),
            update_time=_deven_heven(
                info.get("dEven") or info.get("deven"),
                info.get("hEven"),
            ),
            raw=info,
        )

    def fetch_nav_redemption(
        self,
        session: requests.Session,
        instrument: InstrumentMarketConfig,
    ) -> TSETMCNav:
        url = self.nav_url.format(ins_code=instrument.ins_code)
        data = self._get_json(
            session, url, f"TSETMC_NAV:{instrument.symbol}"
        )
        if not isinstance(data, dict):
            raise TSETMCDataError("ETF NAV response root is not an object.")

        etf = data.get("etf", data)
        if not isinstance(etf, dict):
            raise TSETMCDataError("etf NAV object is missing/invalid.")

        redemption = _positive(etf.get("pRedTran"))
        if redemption is None:
            # STRICT: there is no alternate NAV source/fallback.
            raise TSETMCDataError(
                f"Invalid/missing TSETMC redemption NAV pRedTran for "
                f"{instrument.symbol}."
            )

        return TSETMCNav(
            nav_redemption=redemption,
            nav_issuance=_positive(etf.get("pSubTran")),
            update_time=_deven_heven(
                etf.get("deven"),
                etf.get("hEven"),
            ),
            raw=etf,
        )

    def fetch_order_book(
        self,
        session: requests.Session,
        instrument: InstrumentMarketConfig,
    ) -> TSETMCOrderBook:
        url = self.order_book_url.format(ins_code=instrument.ins_code)
        data = self._get_json(
            session, url, f"TSETMC_ORDER_BOOK:{instrument.symbol}"
        )
        if not isinstance(data, dict):
            raise TSETMCDataError("BestLimits response root is not an object.")

        rows = data.get("bestLimits")
        if not isinstance(rows, list) or not rows:
            raise TSETMCDataError(
                f"bestLimits is empty/invalid for {instrument.symbol}."
            )

        level1 = next(
            (row for row in rows if _integer(row.get("number")) == 1),
            rows[0],
        )
        if not isinstance(level1, dict):
            raise TSETMCDataError("BestLimits level 1 is invalid.")

        best_ask = _positive(level1.get("pMeOf"))
        if best_ask is None:
            # STRICT: no midpoint/last/close fallback.
            raise TSETMCDataError(
                f"Best Ask is missing/invalid for {instrument.symbol}."
            )

        return TSETMCOrderBook(
            best_bid=_positive(level1.get("pMeDem")),
            best_bid_volume=_positive(level1.get("qTitMeDem")),
            best_bid_count=_integer(level1.get("zOrdMeDem")),
            best_ask=best_ask,
            best_ask_volume=_positive(level1.get("qTitMeOf")),
            best_ask_count=_integer(level1.get("zOrdMeOf")),
            raw={"level1": level1, "bestLimits": rows},
        )
