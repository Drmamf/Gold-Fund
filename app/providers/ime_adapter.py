from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests


TEHRAN = ZoneInfo("Asia/Tehran")
ZERO = Decimal("0")


class IMEError(RuntimeError):
    pass


class IMEDataError(IMEError):
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


@dataclass(frozen=True)
class IMEContractSnapshot:
    contract_code: str
    description: Optional[str]
    best_ask: Optional[Decimal]
    best_ask_volume: Optional[Decimal]
    best_bid: Optional[Decimal]
    best_bid_volume: Optional[Decimal]
    last_price: Optional[Decimal]
    settlement_price: Optional[Decimal]
    trade_value: Optional[Decimal]
    trade_volume: Optional[Decimal]
    trade_count: Optional[int]
    last_trade_time: Optional[str]
    update_time: Optional[str]
    raw: dict[str, Any]

    @property
    def valuation_price(self) -> Optional[Decimal]:
        # STRICT PROJECT RULE: only cheapest current seller is valid.
        return self.best_ask

    @property
    def valuation_valid(self) -> bool:
        return self.best_ask is not None and self.best_ask > ZERO


@dataclass(frozen=True)
class IMEMarketSnapshot:
    fetched_at: datetime
    bullion: IMEContractSnapshot | None
    coin: IMEContractSnapshot | None
    raw: list[dict[str, Any]]

    @property
    def valuation_inputs_usable(self) -> bool:
        return bool(
            self.bullion
            and self.bullion.valuation_valid
            and self.coin
            and self.coin.valuation_valid
        )

    def raw_payload(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "bullion": asdict(self.bullion) if self.bullion else None,
            "coin": asdict(self.coin) if self.coin else None,
            "raw": self.raw,
            "strict_policy": {
                "valuation_price_source": "BEST_ASK_ONLY",
                "fallback_allowed": False,
            },
        }


class IMEAdapter:
    def __init__(
        self,
        *,
        endpoint: str,
        bullion_contract_code: str = "GoldBar",
        coin_contract_code: str = "GoldCoin",
        timeout_seconds: float = 15,
        retries: int = 3,
        retry_backoff_seconds: float = 1.2,
        headers: Optional[dict[str, str]] = None,
    ):
        self.endpoint = endpoint
        self.bullion_contract_code = bullion_contract_code
        self.coin_contract_code = coin_contract_code
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
            "Origin": "https://cdn.ime.co.ir",
            "Referer": "https://cdn.ime.co.ir/",
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "IMEAdapter":
        src = config["data_sources"]["ime"]
        net = config.get("network", {})
        return cls(
            endpoint=src["live_market_url"],
            bullion_contract_code=src.get(
                "bullion_contract_code", "GoldBar"
            ),
            coin_contract_code=src.get("coin_contract_code", "GoldCoin"),
            timeout_seconds=net.get("timeout_seconds", 15),
            retries=net.get("retries", 3),
            retry_backoff_seconds=net.get("retry_backoff_seconds", 1.2),
        )

    def _fetch_rows(self, session: requests.Session) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = session.get(
                    self.endpoint,
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise IMEDataError(
                        "IME CDCLiveMarket response root is not a list."
                    )
                return [
                    row for row in payload if isinstance(row, dict)
                ]
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_backoff_seconds * attempt)

        raise IMEError(
            f"IME live market fetch failed after {self.retries} attempts: "
            f"{last_error}"
        )

    @staticmethod
    def _normalize(row: dict[str, Any]) -> IMEContractSnapshot:
        return IMEContractSnapshot(
            contract_code=str(row.get("ContractCode") or "").strip(),
            description=(
                str(row.get("ContractDescription")).strip()
                if row.get("ContractDescription")
                else None
            ),
            best_ask=_positive(row.get("AskPrice1")),
            best_ask_volume=_positive(row.get("AskVolume1")),
            best_bid=_positive(row.get("BidPrice1")),
            best_bid_volume=_positive(row.get("BidVolume1")),
            last_price=_positive(row.get("LastTradedPrice")),
            settlement_price=_positive(row.get("LastSettlementPrice")),
            trade_value=_positive(row.get("TradesValue")),
            trade_volume=_positive(row.get("TradesVolume")),
            trade_count=_integer(row.get("TradesCount")),
            last_trade_time=(
                str(row.get("LastTradedPriceTime"))
                if row.get("LastTradedPriceTime") is not None
                else None
            ),
            update_time=(
                str(row.get("LastUpdate"))
                if row.get("LastUpdate") is not None
                else None
            ),
            raw=row,
        )

    def fetch_market_snapshot(
        self,
        session: requests.Session,
    ) -> IMEMarketSnapshot:
        rows = self._fetch_rows(session)

        by_code = {
            str(row.get("ContractCode") or "").strip(): row
            for row in rows
        }
        bullion_raw = by_code.get(self.bullion_contract_code)
        coin_raw = by_code.get(self.coin_contract_code)

        return IMEMarketSnapshot(
            fetched_at=datetime.now(TEHRAN),
            bullion=(
                self._normalize(bullion_raw) if bullion_raw else None
            ),
            coin=self._normalize(coin_raw) if coin_raw else None,
            raw=rows,
        )
