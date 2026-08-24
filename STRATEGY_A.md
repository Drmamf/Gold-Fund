# Strategy A — RELATIVE_BUY_HOLD

## Policy

- Initial paper capital: `1,000,000,000 IRR`
- Initial holding: `100% Ayyar` at the first valid two-sided order book
- Exactly one logical open position
- No threshold entry/exit, MA7, rearm, Afran, or 30% fund cap
- Direct fund-to-fund rotations are allowed
- Rotation trigger: shared Relative Value `net_executable_edge >= 0.50pp`
- Sell at source `best_bid`
- Buy at target `best_ask`
- Buy/sell fees are read from `config/relative_value.yaml`
- Remaining cash caused by integer unit rounding is allowed

## Lifecycle

```text
Uninitialized account
        |
        | first valid Ayyar order book
        v
ACCOUNT_INITIALIZATION_BUY
        |
        v
Position #1: Ayyar
        |
        | net edge >= 0.50pp
        v
ROTATE Ayyar -> Fund B
        |
        v
Position #1: Fund B
        |
        | net edge >= 0.50pp
        v
ROTATE Fund B -> Fund C
```

The same `position_id` is kept across rotations. `rotations_count` increments.

## Fail-safe

A new rotation is not generated when common valuation inputs are unusable,
source/target valuation is invalid, or the shared Relative Value row is not executable.

Bootstrap is deferred (not fatal to the shared bot) when Ayyar does not have a valid
two-sided order book. During that cycle, the Strategy A account remains in cash.

## Database writes

Every valid bootstrap/rotation writes:

- `transactions`
- `position_events`
- `positions_current`
- `strategy_runtime_state`
- `account_snapshots`

Every cycle, including HOLD cycles, writes one `account_snapshots` row.

Signals are expected to be inserted into `signals` before the executor is called.
The executor then fills:

- `account_had_capacity`
- `trade_executed`
- `non_execution_reason`

## Main files

- `app/strategies/strategy_a_relative_buy_hold.py` — signal/policy engine
- `app/execution/strategy_a_math.py` — pure all-in sizing math
- `app/execution/strategy_a_executor.py` — PostgreSQL paper-account execution
- `app/execution/router.py` — pipeline executor dispatcher
- `config/strategy_a.yaml` — Strategy A settings
- `tests/test_strategy_a.py` — unit tests

## Wiring example

```python
from app.strategies.strategy_a_relative_buy_hold import RelativeBuyHoldStrategy
from app.execution.strategy_a_executor import StrategyAExecutor
from app.execution.router import StrategyExecutorRouter

strategy_a = RelativeBuyHoldStrategy.from_yaml("config/strategy_a.yaml")
executor_a = StrategyAExecutor.from_yaml(
    "config/strategy_a.yaml",
    "config/relative_value.yaml",
)

executor_router = StrategyExecutorRouter({
    strategy_a.strategy_id: executor_a,
})
```

The shared `UnifiedTradingPipeline` receives `strategy_a` in its strategy list and
`executor_router` as its executor.

## Existing PostgreSQL database

If tables were created before this Strategy A version, run:

```bash
psql "$DATABASE_URL" -f scripts/migrate_strategy_a_v1.sql
```

If the database has not been initialized yet, normal `scripts/init_db.py` is sufficient.
