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
    def get(self, *args, **kwargs):
        return FakeResponse(self.payloads.pop(0))


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
