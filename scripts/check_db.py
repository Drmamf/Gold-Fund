#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import engine


EXPECTED_TABLES = {
    "instruments",
    "asset_composition_history",
    "config_versions",
    "market_cycles",
    "common_market_snapshot",
    "fund_market_snapshot",
    "fund_valuation_snapshot",
    "relative_value_snapshot",
    "daily_common_summary",
    "daily_fund_summary",
    "signals",
    "transactions",
    "positions_current",
    "position_events",
    "account_snapshots",
    "strategy_runtime_state",
    "asset_report_status",
    "data_errors",
    "notification_log",
    "bot_runs",
}


def main():
    inspector = inspect(engine)
    found = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - found
    extra = found - EXPECTED_TABLES

    print(f"Expected: {len(EXPECTED_TABLES)}")
    print(f"Found:    {len(found)}")

    if missing:
        print("MISSING:")
        for name in sorted(missing):
            print(" -", name)
        raise SystemExit(1)

    print("[OK] All 20 expected tables exist.")

    if extra:
        print("Extra tables:", ", ".join(sorted(extra)))

    with engine.connect() as conn:
        version = conn.execute(text("select version()")).scalar_one()
        print("PostgreSQL:", version)


if __name__ == "__main__":
    main()
