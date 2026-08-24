# Strategy B — Final staged-entry logic

## Core rule

Strategy B separates **signal generation** from **execution**.

Normal staged entries are 10% of current portfolio value and may continue as:

```text
ENTRY_1 -> +1.50pp REARM -> fresh BUY -> ENTRY_2
ENTRY_2 -> +1.50pp REARM -> fresh BUY -> ENTRY_3
ENTRY_3 -> +1.50pp REARM -> fresh BUY -> ENTRY_4
...
```

The +1.50pp rearm threshold is:

```text
fund_buy_threshold + 1.50 percentage points
```

After an entry executes, the account-level threshold gate closes and the
**executed fund** becomes the rearm reference. The next normal staged entry is
allowed only after that reference fund reaches its rearm threshold.

## MA7 is only an Entry #2 fallback

Immediately after Entry #1, if its rearm has NOT happened, the bot keeps one
special fallback open. On a later trading day:

```text
current cumulative trade value of all 10 gold funds
>
mean of the final total trade values of previous 7 complete trading days
```

then a single Entry #2 may execute in the valid account-feasible fund with the
lowest current Total Bubble. It does not need a fresh BUY signal.

If Entry #1 reaches +1.50pp rearm before this MA7 fallback executes, the MA7
fallback is permanently closed. MA7 never creates Entry #3, #4, etc.

After an MA7 Entry #2, Entry #3 again requires the normal +1.50pp rearm path.

## Decision priority

```text
1. EXIT (current fund sell threshold)
2. Relative Rotation (>= 0.50pp net executable edge)
3. Normal Threshold Entry N
4. MA7 Fallback Entry #2
```

## Risk/execution

- each entry: 10% current portfolio value
- max gold exposure: 100%
- max current exposure per fund: 30%
- no partial entry
- free capital parks in Afran
- rotation preserves position_id
- after rotation, exit uses the CURRENT fund's sell threshold
- if best relative target violates 30% cap, executor tries next-best target

## Relevant files

- `app/strategies/strategy_b_threshold_10_10.py`
- `app/strategies/strategy_b_entry_state.py`
- `app/execution/strategy_b_executor.py`
- `app/execution/strategy_b_math.py`
- `app/state/strategy_b_runtime.py`
- `config/strategy_b.yaml`
