from __future__ import annotations

import unittest

from app.providers.api_guard import ProviderCallGuard


class FakeNotifications:
    def __init__(self):
        self.calls = []
    def notify_api_error(self, **kwargs):
        self.calls.append(kwargs)


class ProviderGuardTest(unittest.TestCase):
    def test_failed_call_alerts_and_reraises(self):
        n = FakeNotifications()
        guard = ProviderCallGuard(n)

        def boom():
            raise RuntimeError("network down")

        with self.assertRaises(RuntimeError):
            guard.call(
                source="IME",
                operation="fetch_coin",
                fn=boom,
                instrument_symbol="COIN",
            )

        self.assertEqual(len(n.calls), 1)
        self.assertEqual(n.calls[0]["source"], "IME")
        self.assertEqual(n.calls[0]["operation"], "fetch_coin")


if __name__ == "__main__":
    unittest.main()
