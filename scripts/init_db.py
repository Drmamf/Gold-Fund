#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select

# Allow execution as: python scripts/init_db.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base, engine, db_session
from app.models import Instrument, AssetCompositionHistory
from app.jalali_utils import parse_jalali_date
import app.models  # noqa: F401 - ensures all models are registered


INSTRUMENT_CONFIG_PATH = PROJECT_ROOT / "config" / "instruments.yaml"


def jalali_to_gregorian(text: str):
    return parse_jalali_date(text)


def seed_instruments():
    from app.config_loader import load_project_config

    project_cfg = load_project_config(PROJECT_ROOT)

    with db_session() as session:
        existing = {
            r.symbol: r
            for r in session.scalars(select(Instrument)).all()
        }

        for cfg in project_cfg.instruments:
            external_ids = {
                "tsetmc_ins_code": cfg.ins_code,
                "isin": cfg.isin,
            }

            row = existing.get(cfg.symbol)
            if row is None:
                session.add(
                    Instrument(
                        symbol=cfg.symbol,
                        legal_name=cfg.legal_name,
                        instrument_type=cfg.instrument_type,
                        is_gold_fund=cfg.is_gold_fund,
                        is_anchor=cfg.is_anchor,
                        is_active=True,
                        external_ids=external_ids,
                    )
                )
            else:
                # Keep identifiers current without touching historical data.
                row.legal_name = cfg.legal_name
                row.instrument_type = cfg.instrument_type
                row.is_gold_fund = cfg.is_gold_fund
                row.is_anchor = cfg.is_anchor
                row.is_active = True
                row.external_ids = external_ids


def seed_asset_composition(csv_path: Path):
    if not csv_path.exists():
        print(f"[WARN] asset composition file not found: {csv_path}")
        return

    raw_bytes = csv_path.read_bytes()
    source_hash = hashlib.sha256(raw_bytes).hexdigest()

    with db_session() as session:
        instruments = {
            r.symbol: r
            for r in session.scalars(select(Instrument)).all()
        }

        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                symbol = row["symbol"].strip()
                inst = instruments.get(symbol)
                if not inst:
                    print(f"[WARN] unknown symbol in composition CSV: {symbol}")
                    continue

                as_of_j = row["as_of_date_jalali"].strip()
                as_of_g = jalali_to_gregorian(as_of_j)

                raw_b = float(row["original_bullion_weight"])
                raw_c = float(row["original_coin_weight"])
                norm_b = float(row["normalized_bullion_weight"])
                norm_c = float(row["normalized_coin_weight"])

                exists = session.scalar(
                    select(AssetCompositionHistory).where(
                        AssetCompositionHistory.fund_id == inst.id,
                        AssetCompositionHistory.as_of_date == as_of_g,
                    )
                )
                if exists:
                    continue

                session.add(
                    AssetCompositionHistory(
                        fund_id=inst.id,
                        report_period_end=as_of_g,
                        report_period_end_jalali=as_of_j,
                        as_of_date=as_of_g,
                        as_of_date_jalali=as_of_j,
                        raw_bullion_weight=raw_b,
                        raw_coin_weight=raw_c,
                        normalized_bullion_weight=norm_b,
                        normalized_coin_weight=norm_c,
                        source_file=row.get("source_file") or csv_path.name,
                        source_hash=source_hash,
                    )
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-composition",
        default=str(PROJECT_ROOT / "config" / "fund_asset_composition_gold_normalized.csv"),
        help="Path to normalized monthly asset-composition CSV.",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Create tables only.",
    )
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    print("[OK] PostgreSQL tables created/verified.")

    if not args.no_seed:
        seed_instruments()
        print("[OK] instruments seeded.")
        seed_asset_composition(Path(args.seed_composition))
        print("[OK] asset composition seed attempted.")

    print("[DONE] Database initialization completed.")


if __name__ == "__main__":
    main()
