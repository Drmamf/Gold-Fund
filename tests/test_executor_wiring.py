from __future__ import annotations

import os
import unittest

# create_engine is lazy; this avoids requiring a live PostgreSQL DB merely
# to verify class wiring/importability.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.execution.strategy_a_executor import StrategyAExecutor
from app.execution.strategy_b_executor import StrategyBExecutor


class ExecutorWiringTest(unittest.TestCase):
    def test_strategy_a_executor_has_required_methods(self):
        self.assertTrue(callable(getattr(StrategyAExecutor, "execute_strategy_signals", None)))
        self.assertTrue(callable(getattr(StrategyAExecutor, "mark_account_only", None)))

    def test_strategy_b_executor_has_required_methods(self):
        self.assertTrue(callable(getattr(StrategyBExecutor, "execute_strategy_signals", None)))
        self.assertTrue(callable(getattr(StrategyBExecutor, "mark_account_only", None)))


if __name__ == "__main__":
    unittest.main()
