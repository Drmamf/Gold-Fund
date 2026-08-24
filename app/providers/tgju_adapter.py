# -*- coding: utf-8 -*-
"""
TGJU Market Data Adapter
------------------------
Single-request provider for:
- Free-market USD/IRR: current.price_dollar_rl
- Gold ounce USD/oz:   current.ons

Designed for VPS/live trading:
- No hard-coded rev query parameter.
- Strict schema validation.
- Canonical output units.
- Independent freshness checks.
- Fail-closed: invalid/stale data is never marked usable for signals.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Iterable
import time
import requests


TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

WEEKDAY_NAME_TO_PYTHON = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


@dataclass
class Quote:
    instrument: str
    source_key: str
    source: str
    price: Optional[float]
    unit: str
    source_timestamp: Optional[str]
    fetched_at: str
    age_seconds: Optional[float]
    fresh: bool
    valid: bool
    usable: bool
    error: Optional[str]
    freshness_status: str = "UNKNOWN"
    effective_max_age_seconds: Optional[int] = None
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now_tehran() -> datetime:
    return datetime.now(TEHRAN_TZ)


def _parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        if value == "":
            return None
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_tgju_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=TEHRAN_TZ)
    except ValueError:
        return None


class TGJUAdapter:
    def __init__(
        self,
        endpoint: str,
        usd_key: str = "price_dollar_rl",
        ounce_key: str = "ons",
        usd_max_age_seconds: int = 21600,
        ounce_max_age_seconds: int = 900,
        ounce_closed_market_max_age_seconds: int = 259200,
        ounce_carry_forward_weekdays: Iterable[int] = (5, 6),
        ounce_carry_forward_dates: Iterable[str] = (),
        timeout_seconds: int = 15,
        retries: int = 3,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
    ):
        self.endpoint = endpoint
        self.usd_key = usd_key
        self.ounce_key = ounce_key
        self.usd_max_age_seconds = int(usd_max_age_seconds)
        self.ounce_max_age_seconds = int(ounce_max_age_seconds)
        self.ounce_closed_market_max_age_seconds = int(
            ounce_closed_market_max_age_seconds
        )
        self.ounce_carry_forward_weekdays = frozenset(
            int(v) for v in ounce_carry_forward_weekdays
        )
        self.ounce_carry_forward_dates = frozenset(
            str(v) for v in ounce_carry_forward_dates
        )
        self.timeout_seconds = int(timeout_seconds)
        self.retries = int(retries)
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "Origin": "https://www.tgju.org",
            "Referer": "https://www.tgju.org/",
            "User-Agent": user_agent,
        }

    def _fetch_json(self, session: requests.Session) -> Dict[str, Any]:
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                response = session.get(
                    self.endpoint,
                    params={"_": str(time.time_ns())},
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("TGJU response root is not a JSON object")
                return payload
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
        raise RuntimeError(f"TGJU fetch failed after {self.retries} attempts: {last_error}")

    def _build_quote(
        self,
        current: Dict[str, Any],
        key: str,
        instrument: str,
        unit: str,
        max_age_seconds: int,
        fetched_at_dt: datetime,
        closed_market_max_age_seconds: Optional[int] = None,
        carry_forward_weekdays: Optional[Iterable[int]] = None,
        carry_forward_dates: Optional[Iterable[str]] = None,
    ) -> Quote:
        row = current.get(key)
        fetched_at = fetched_at_dt.isoformat()

        if not isinstance(row, dict):
            return Quote(
                instrument=instrument,
                source_key=key,
                source="TGJU",
                price=None,
                unit=unit,
                source_timestamp=None,
                fetched_at=fetched_at,
                age_seconds=None,
                fresh=False,
                valid=False,
                usable=False,
                error=f"MISSING_OR_INVALID_KEY: current.{key}",
                raw=None,
            )

        price = _parse_number(row.get("p"))
        source_dt = _parse_tgju_timestamp(row.get("ts"))

        age_seconds = None
        if source_dt is not None:
            age_seconds = max(
                0.0,
                (fetched_at_dt - source_dt.astimezone(TEHRAN_TZ)).total_seconds(),
            )

        valid = price is not None and source_dt is not None

        effective_max_age_seconds = int(max_age_seconds)
        freshness_status = "INVALID"

        normal_fresh = bool(
            valid
            and age_seconds is not None
            and age_seconds <= max_age_seconds
        )

        carry_weekdays = frozenset(
            int(v) for v in (carry_forward_weekdays or ())
        )
        carry_dates = frozenset(
            str(v) for v in (carry_forward_dates or ())
        )
        local_date_text = fetched_at_dt.astimezone(TEHRAN_TZ).date().isoformat()
        carry_forward_window = bool(
            fetched_at_dt.astimezone(TEHRAN_TZ).weekday() in carry_weekdays
            or local_date_text in carry_dates
        )

        carry_forward_fresh = bool(
            valid
            and not normal_fresh
            and carry_forward_window
            and closed_market_max_age_seconds is not None
            and age_seconds is not None
            and age_seconds <= int(closed_market_max_age_seconds)
        )

        fresh = bool(normal_fresh or carry_forward_fresh)
        if normal_fresh:
            freshness_status = "LIVE_FRESH"
        elif carry_forward_fresh:
            freshness_status = "MARKET_CLOSED_CARRY_FORWARD"
            effective_max_age_seconds = int(closed_market_max_age_seconds)

        error = None
        if price is None:
            error = f"INVALID_PRICE: current.{key}.p={row.get('p')!r}"
        elif source_dt is None:
            error = f"INVALID_TIMESTAMP: current.{key}.ts={row.get('ts')!r}"
        elif not fresh:
            error = (
                f"STALE_PRICE: age={age_seconds:.1f}s "
                f"> effective_max_age={effective_max_age_seconds}s"
            )

        return Quote(
            instrument=instrument,
            source_key=key,
            source="TGJU",
            price=price,
            unit=unit,
            source_timestamp=source_dt.isoformat() if source_dt else None,
            fetched_at=fetched_at,
            age_seconds=age_seconds,
            fresh=fresh,
            valid=valid,
            usable=bool(valid and fresh),
            error=error,
            freshness_status=freshness_status,
            effective_max_age_seconds=effective_max_age_seconds,
            raw=row,
        )

    def fetch_market_snapshot(
        self,
        session: Optional[requests.Session] = None,
    ) -> Dict[str, Any]:
        owns_session = session is None
        session = session or requests.Session()
        fetched_at_dt = _now_tehran()

        try:
            payload = self._fetch_json(session)
            current = payload.get("current")
            if not isinstance(current, dict):
                raise RuntimeError("TGJU schema error: 'current' object is missing")

            usd = self._build_quote(
                current=current,
                key=self.usd_key,
                instrument="USD_IRR_FREE",
                unit="IRR_PER_USD",
                max_age_seconds=self.usd_max_age_seconds,
                fetched_at_dt=fetched_at_dt,
            )

            ounce = self._build_quote(
                current=current,
                key=self.ounce_key,
                instrument="XAU_USD_OUNCE",
                unit="USD_PER_TROY_OUNCE",
                max_age_seconds=self.ounce_max_age_seconds,
                fetched_at_dt=fetched_at_dt,
                closed_market_max_age_seconds=(
                    self.ounce_closed_market_max_age_seconds
                ),
                carry_forward_weekdays=(
                    self.ounce_carry_forward_weekdays
                ),
                carry_forward_dates=self.ounce_carry_forward_dates,
            )

            global_gold_ounce_irr = (
                ounce.price * usd.price
                if ounce.usable and usd.usable
                else None
            )

            return {
                "provider": "TGJU",
                "endpoint": self.endpoint,
                "fetched_at": fetched_at_dt.isoformat(),
                "api_ok": True,
                "api_error": None,
                "usd": usd.to_dict(),
                "gold_ounce": ounce.to_dict(),
                "global_gold_ounce_irr": global_gold_ounce_irr,
                "valuation_inputs_usable": bool(usd.usable and ounce.usable),
            }

        except Exception as exc:
            return {
                "provider": "TGJU",
                "endpoint": self.endpoint,
                "fetched_at": fetched_at_dt.isoformat(),
                "api_ok": False,
                "api_error": str(exc),
                "usd": None,
                "gold_ounce": None,
                "global_gold_ounce_irr": None,
                "valuation_inputs_usable": False,
            }
        finally:
            if owns_session:
                session.close()


def build_adapter_from_config(config: Dict[str, Any]) -> TGJUAdapter:
    tgju = config["data_sources"]["tgju"]
    usd = tgju["usd"]
    ounce = tgju["gold_ounce"]
    network = config.get("network", {})

    return TGJUAdapter(
        endpoint=tgju["endpoint"],
        usd_key=usd.get("key", "price_dollar_rl"),
        ounce_key=ounce.get("key", "ons"),
        usd_max_age_seconds=usd.get("max_age_seconds", 21600),
        ounce_max_age_seconds=ounce.get("max_age_seconds", 900),
        ounce_closed_market_max_age_seconds=ounce.get(
            "closed_market_max_age_seconds", 259200
        ),
        ounce_carry_forward_weekdays=(
            WEEKDAY_NAME_TO_PYTHON[name]
            for name in ounce.get(
                "carry_forward_weekdays", ["Saturday", "Sunday"]
            )
        ),
        ounce_carry_forward_dates=ounce.get(
            "carry_forward_dates", []
        ),
        timeout_seconds=network.get("timeout_seconds", 15),
        retries=network.get("retries", 3),
    )
