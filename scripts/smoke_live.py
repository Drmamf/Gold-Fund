#!/usr/bin/env python3
"""Read-only live provider smoke test: no signal generation, no account execution."""
from __future__ import annotations

from pathlib import Path
import sys
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.collector import SharedMarketCollector
from app.config_loader import load_project_config
from app.database import SessionLocal
from app.models import Instrument
from app.valuation_engine import SharedValuationEngine


def main() -> int:
    cfg = load_project_config(PROJECT_ROOT)
    with SessionLocal() as session:
        ids = {r.symbol: int(r.id) for r in session.scalars(select(Instrument)).all()}

    collector = SharedMarketCollector(config=cfg, instrument_ids=ids, notifications=None)
    try:
        common, funds = collector.collect(cycle_id=None)
        valuation = SharedValuationEngine.from_yaml(
            PROJECT_ROOT / "config" / "market_config.yaml",
            PROJECT_ROOT / "config" / "strategy_b.yaml",
            session_factory=SessionLocal,
        ).calculate(common, funds, common.collected_at.date())
    finally:
        collector.close()

    print("COMMON usable:", valuation.common.valuation_inputs_usable)
    print("USD/IRR:", valuation.common.usd_irr)
    print("Ounce:", valuation.common.ounce_usd)
    print("IME GoldBar Best Ask:", valuation.common.ime_bullion_price)
    print("IME GoldCoin Best Ask:", valuation.common.ime_coin_price)
    valid = 0
    for fid, snap in funds.items():
        val = valuation.funds.get(fid)
        if snap.symbol == "آفران":
            continue
        print(
            snap.symbol,
            "ask=", snap.best_ask,
            "nav=", snap.nav_redemption,
            "valid=", bool(val and val.valid),
            "total_bubble=", val.total_bubble if val else None,
        )
        valid += int(bool(val and val.valid))
    print(f"VALID GOLD FUND VALUATIONS: {valid}/10")
    return 0 if valuation.common.valuation_inputs_usable and valid == 10 else 2


if __name__ == "__main__":
    raise SystemExit(main())
