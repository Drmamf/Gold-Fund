from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.pipeline import UnifiedTradingPipeline
from app.contracts import ValuationBatch


class FakeRepo:
    def __init__(self):
        self.started = []
    def start_cycle(self, **kwargs):
        self.started.append(kwargs)
        return len(self.started)
    def store_raw_market(self, *args): pass
    def store_valuations(self, *args): pass
    def store_relative(self, *args): pass
    def load_strategy_state(self, strategy_id, *, market_date): return {}
    def store_signals(self, cycle_id, signals):
        return [], list(signals)
    def complete_cycle(self, cycle_id): pass
    def fail_cycle(self, cycle_id, exc): raise AssertionError(exc)


class FakeCollector:
    def collect(self, *, cycle_id=None): return object(), {}


class FakeValuation:
    def calculate(self, common, funds, trade_date): return ValuationBatch(common=common, funds={})


class FakeRelative:
    def calculate(self, common, funds, valuations): return {}


class FakeStrategy:
    strategy_id = "X"
    def __init__(self): self.calls = 0
    def generate_signals(self, **kwargs):
        self.calls += 1
        return []


class FakeExecutor:
    def __init__(self):
        self.execute_calls = 0
        self.mark_calls = 0
    def execute_strategy_signals(self, **kwargs):
        self.execute_calls += 1
    def mark_strategy_account(self, **kwargs):
        self.mark_calls += 1


class FakeNotifications:
    def __init__(self): self.calls = []
    def notify_signals(self, **kwargs):
        self.calls.append(kwargs)


class FakeDaily:
    def upsert_current_day(self, trade_date): pass


class PipelinePhaseTest(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()
        self.strategy = FakeStrategy()
        self.executor = FakeExecutor()
        self.notifications = FakeNotifications()
        self.pipe = UnifiedTradingPipeline(
            collector=FakeCollector(),
            valuation_engine=FakeValuation(),
            relative_engine=FakeRelative(),
            repository=self.repo,
            executor=self.executor,
            daily_aggregator=FakeDaily(),
            strategies=[self.strategy],
            notifications=self.notifications,
        )
        self.dt = datetime(2026, 8, 15, 12, 3, tzinfo=ZoneInfo("Asia/Tehran"))

    def test_warmup_never_calls_strategy_or_executor(self):
        self.pipe.run_warmup(trade_date=self.dt.date(), scheduled_for=self.dt)
        self.assertEqual(self.strategy.calls, 0)
        self.assertEqual(self.executor.execute_calls, 0)
        self.assertEqual(self.executor.mark_calls, 0)
        self.assertEqual(self.repo.started[-1]["cycle_type"], "WARMUP")

    def test_active_calls_strategy_and_execution(self):
        dt = self.dt.replace(hour=12, minute=5)
        self.pipe.run_active_cycle(trade_date=dt.date(), scheduled_for=dt)
        self.assertEqual(self.strategy.calls, 1)
        self.assertEqual(self.executor.execute_calls, 1)
        self.assertEqual(self.executor.mark_calls, 0)
        self.assertEqual(self.repo.started[-1]["cycle_type"], "ACTIVE")
        self.assertEqual(len(self.notifications.calls), 1)
        self.assertEqual(
            self.notifications.calls[0]["strategy_id"],
            self.strategy.strategy_id,
        )
        self.assertEqual(self.notifications.calls[0]["generated_signals"], [])

    def test_close_marks_account_without_signal_generation(self):
        dt = self.dt.replace(hour=17, minute=0)
        self.pipe.run_close(trade_date=dt.date(), scheduled_for=dt)
        self.assertEqual(self.strategy.calls, 0)
        self.assertEqual(self.executor.execute_calls, 0)
        self.assertEqual(self.executor.mark_calls, 1)
        self.assertEqual(self.repo.started[-1]["cycle_type"], "CLOSE")


if __name__ == "__main__":
    unittest.main()
