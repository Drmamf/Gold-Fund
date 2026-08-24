from __future__ import annotations

import unittest
from decimal import Decimal

from app.config_loader import load_project_config
from app.providers.ime_adapter import IMEAdapter


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
    def get(self, *args, **kwargs):
        return FakeResponse(self.payload)


class IMEAdapterTest(unittest.TestCase):
    def test_valuation_price_is_best_ask_only(self):
        cfg = load_project_config(".")
        adapter = IMEAdapter.from_config(cfg.market)
        session = FakeSession([
            {
                "ContractCode": "GoldBar",
                "AskPrice1": 1000,
                "BidPrice1": 980,
                "LastTradedPrice": 990,
                "LastSettlementPrice": 995,
            },
            {
                "ContractCode": "GoldCoin",
                "AskPrice1": 2000,
                "BidPrice1": 1970,
                "LastTradedPrice": 1980,
                "LastSettlementPrice": 1990,
            },
        ])
        snap = adapter.fetch_market_snapshot(session)
        self.assertEqual(
            snap.bullion.valuation_price, Decimal("1000")
        )
        self.assertEqual(
            snap.coin.valuation_price, Decimal("2000")
        )
        self.assertTrue(snap.valuation_inputs_usable)


if __name__ == "__main__":
    unittest.main()
