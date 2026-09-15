from __future__ import annotations

import unittest
from decimal import Decimal

from app.config_loader import load_project_config
from app.providers.tsetmc_adapter import (
    TSETMCAdapter,
    TSETMCDataError,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        item = self.payloads.pop(0)
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(item)


class TSETMCAdapterTest(unittest.TestCase):
    def setUp(self):
        cfg = load_project_config(".")
        self.instrument = cfg.instrument_by_symbol["عیار"]
        self.adapter = TSETMCAdapter.from_config(cfg.market)

    def test_orderbook_uses_ask_not_mid(self):
        session = FakeSession([{
            "bestLimits": [{
                "number": 1,
                "pMeDem": 98,
                "qTitMeDem": 10,
                "zOrdMeDem": 2,
                "pMeOf": 100,
                "qTitMeOf": 20,
                "zOrdMeOf": 3,
            }]
        }])
        ob = self.adapter.fetch_order_book(session, self.instrument)
        self.assertEqual(ob.best_ask, Decimal("100"))
        self.assertEqual(ob.best_bid, Decimal("98"))

    def test_missing_ask_is_invalid_no_fallback(self):
        session = FakeSession([{
            "bestLimits": [{
                "number": 1,
                "pMeDem": 98,
                "pMeOf": 0,
            }]
        }])
        with self.assertRaises(TSETMCDataError):
            self.adapter.fetch_order_book(session, self.instrument)

    def test_nav_is_tsetmc_redemption_only(self):
        session = FakeSession([{
            "etf": {
                "pRedTran": 12345,
                "pSubTran": 12400,
                "deven": 20260815,
                "hEven": 120500,
            }
        }])
        nav = self.adapter.fetch_nav_redemption(
            session, self.instrument
        )
        self.assertEqual(nav.nav_redemption, Decimal("12345"))

    def test_tsetmc_network_is_not_capped_to_ime_retries(self):
        self.assertGreaterEqual(self.adapter.retries, 5)

    def test_price_retries_fast_502_then_succeeds(self):
        self.adapter.retry_backoff_seconds = 0
        session = FakeSession([
            FakeResponse({}, status=502),
            FakeResponse({}, status=502),
            FakeResponse({
                "closingPriceInfo": {
                    "pDrCotVal": 1000,
                    "pClosing": 1000,
                    "qTotCap": 50,
                    "qTotTran5J": 10,
                    "zTotTran": 3,
                    "dEven": 20260915,
                    "hEven": 123500,
                }
            }),
        ])
        price = self.adapter.fetch_price_activity(session, self.instrument)
        self.assertEqual(price.last_price, Decimal("1000"))
        self.assertEqual(session.calls, 3)

    def test_missing_redemption_nav_invalid(self):
        session = FakeSession([{
            "etf": {
                "pSubTran": 12400,
            }
        }])
        with self.assertRaises(TSETMCDataError):
            self.adapter.fetch_nav_redemption(
                session, self.instrument
            )


if __name__ == "__main__":
    unittest.main()
