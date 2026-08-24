#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys

from sqlalchemy import inspect, select, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config_loader import load_project_config
from app.database import SessionLocal, engine
from app.models import AssetCompositionHistory, Instrument


def main() -> int:
    cfg = load_project_config(PROJECT_ROOT)
    errors: list[str] = []

    for key in ["DATABASE_URL", "POSTGRES_PASSWORD"]:
        value = os.getenv(key, "")
        if not value or "CHANGE_ME" in value:
            errors.append(f"{key} is missing or still CHANGE_ME")

    bale_enabled = os.getenv("BALE_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
    if bale_enabled:
        for key in ["BALE_BOT_TOKEN", "BALE_CHAT_ID"]:
            value = os.getenv(key, "")
            if not value or value == "CHANGE_ME":
                errors.append(f"{key} is missing or still CHANGE_ME")

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        errors.append(f"PostgreSQL connection failed: {exc}")

    inspector = inspect(engine)
    expected_tables = {
        "instruments", "asset_composition_history", "config_versions",
        "market_cycles", "common_market_snapshot", "fund_market_snapshot",
        "fund_valuation_snapshot", "relative_value_snapshot",
        "daily_common_summary", "daily_fund_summary", "signals",
        "transactions", "positions_current", "position_events",
        "account_snapshots", "strategy_runtime_state", "asset_report_status",
        "data_errors", "notification_log", "bot_runs",
    }
    actual = set(inspector.get_table_names())
    missing_tables = sorted(expected_tables - actual)
    if missing_tables:
        errors.append("Missing DB tables: " + ", ".join(missing_tables))

    if not missing_tables:
        with SessionLocal() as session:
            instruments = session.scalars(select(Instrument)).all()
            if len(instruments) < 11:
                errors.append(f"Expected >=11 instruments, found {len(instruments)}")
            gold = [r for r in instruments if r.is_gold_fund and r.is_active]
            if len(gold) != 10:
                errors.append(f"Expected 10 active gold funds, found {len(gold)}")
            mix_funds = set(
                session.scalars(select(AssetCompositionHistory.fund_id)).all()
            )
            missing_mix = [r.symbol for r in gold if r.id not in mix_funds]
            if missing_mix:
                errors.append("Missing asset composition for: " + ", ".join(missing_mix))

    print("[OK] strict config loaded")
    print(f"[OK] instruments in config: {len(cfg.instruments)}")
    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1
    print("[OK] database/schema/seeds/secrets preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
