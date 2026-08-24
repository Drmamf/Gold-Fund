from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.execution.strategy_a_executor import StrategyAExecutor
from app.execution.strategy_b_executor import StrategyBExecutor


class FakeSessionWithAutoflushOff:
    """Mimics the relevant SessionLocal behavior: pending rows are invisible
    to SELECT until an explicit flush() occurs because autoflush=False.
    """

    def __init__(self):
        self.pending = []
        self.flushed = False
        self.flush_calls = 0

    def scalar(self, _stmt):
        if self.flushed and self.pending:
            return self.pending[0]
        return None

    def add(self, row):
        self.pending.append(row)

    def flush(self):
        self.flushed = True
        self.flush_calls += 1


class RuntimeStateAutoflushRegressionTest(unittest.TestCase):
    def _exercise(self, executor_cls, strategy_id):
        executor = executor_cls.__new__(executor_cls)
        executor.config = SimpleNamespace(strategy_id=strategy_id)
        session = FakeSessionWithAutoflushOff()

        executor._save_account_state(session, {"cash": "100"})
        executor._save_account_state(session, {"cash": "90"})

        self.assertEqual(len(session.pending), 1)
        self.assertGreaterEqual(session.flush_calls, 1)
        self.assertEqual(session.pending[0].state_value["cash"], "90")

    def test_strategy_a_does_not_queue_duplicate_global_account_state(self):
        self._exercise(StrategyAExecutor, "RELATIVE_BUY_HOLD")

    def test_strategy_b_does_not_queue_duplicate_global_account_state(self):
        self._exercise(StrategyBExecutor, "THRESHOLD_10_10_RELATIVE")


if __name__ == "__main__":
    unittest.main()
