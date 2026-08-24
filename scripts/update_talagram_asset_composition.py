#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.jalali_utils import gregorian_to_jalali, jalali_date_text
from app.models import AssetCompositionHistory, Instrument
from app.notifications.bale_client import BaleBotClient

SOURCE_URL = "https://talagram.org/box-assets"
SOURCE_LABEL = "talagram.org/box-assets"
TEHRAN = ZoneInfo("Asia/Tehran")

TARGET_SYMBOLS = (
    "عیار",
    "کهربا",
    "مثقال",
    "گوهر",
    "گنج",
    "آلتون",
    "زر",
    "لیان",
    "رز ترنج",
    "زروان",
)
TARGET_SET = set(TARGET_SYMBOLS)

CSV_PATH = PROJECT_ROOT / "config" / "fund_asset_composition_gold_normalized.csv"
BACKUP_DIR = PROJECT_ROOT / "runtime_state" / "composition_backups"

CSV_FIELDS = (
    "symbol",
    "legal_fund_name",
    "as_of_date_jalali",
    "original_bullion_weight",
    "original_coin_weight",
    "gold_basis_total",
    "normalized_bullion_weight",
    "normalized_coin_weight",
    "source_file",
    "normalization_rule",
)


class DataPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.data_page: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.data_page is not None:
            return
        for key, value in attrs:
            if key == "data-page" and value:
                self.data_page = value
                return


def _decimal(value: Any, field: str, symbol: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{symbol}: invalid {field}={value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"{symbol}: non-finite {field}={value!r}")
    return d


def _fmt(d: Decimal) -> str:
    # Keep exact useful precision without scientific notation or float noise.
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fetch_products(timeout: float = 20.0) -> tuple[list[dict[str, Any]], str]:
    response = requests.get(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WallexGoldCompositionUpdater/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Cache-Control": "no-cache",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.text.strip():
        raise RuntimeError("Talagram returned an empty HTML body")

    parser = DataPageParser()
    parser.feed(response.text)
    if not parser.data_page:
        raise RuntimeError("Talagram HTML does not contain Inertia data-page")

    page = json.loads(parser.data_page, parse_float=Decimal)
    if page.get("component") != "BoxAssets":
        raise RuntimeError(
            f"Unexpected Talagram component: {page.get('component')!r}"
        )

    products = (page.get("props") or {}).get("products")
    if not isinstance(products, list):
        raise RuntimeError("Talagram props.products is missing or is not a list")

    # Hash only the Inertia page payload, not response headers/cookies.
    source_hash = hashlib.sha256(
        parser.data_page.encode("utf-8")
    ).hexdigest()
    return products, source_hash


def build_rows(products: list[dict[str, Any]]) -> dict[str, dict[str, Decimal]]:
    matched: dict[str, dict[str, Decimal]] = {}

    for product in products:
        if not isinstance(product, dict):
            continue
        symbol = str(product.get("name") or "").strip()
        if symbol not in TARGET_SET:
            continue
        if symbol in matched:
            raise ValueError(f"Duplicate Talagram product for {symbol}")

        coin = _decimal(product.get("coin_ratio"), "coin_ratio", symbol)
        bullion = _decimal(product.get("shemsh_ratio"), "shemsh_ratio", symbol)
        silver = _decimal(product.get("silver_ratio", 0), "silver_ratio", symbol)
        bank = _decimal(product.get("bank_ratio", 0), "bank_ratio", symbol)
        other = _decimal(product.get("other_ratio", 0), "other_ratio", symbol)

        for field, value in (
            ("coin_ratio", coin),
            ("shemsh_ratio", bullion),
            ("silver_ratio", silver),
            ("bank_ratio", bank),
            ("other_ratio", other),
        ):
            if value < 0 or value > Decimal("1.05"):
                raise ValueError(f"{symbol}: out-of-range {field}={value}")

        total_assets = coin + bullion + silver + bank + other
        if not (Decimal("0.95") <= total_assets <= Decimal("1.05")):
            raise ValueError(
                f"{symbol}: asset ratios sum to {total_assets}, expected about 1"
            )

        gold_basis = bullion + coin
        if gold_basis <= 0:
            raise ValueError(f"{symbol}: bullion+coin must be positive")

        norm_bullion = bullion / gold_basis
        norm_coin = coin / gold_basis
        if abs((norm_bullion + norm_coin) - Decimal("1")) > Decimal("0.0000001"):
            raise ValueError(f"{symbol}: normalized gold weights do not sum to 1")

        matched[symbol] = {
            "bullion": bullion,
            "coin": coin,
            "silver": silver,
            "bank": bank,
            "other": other,
            "gold_basis": gold_basis,
            "norm_bullion": norm_bullion,
            "norm_coin": norm_coin,
        }

    missing = TARGET_SET.difference(matched)
    extra_count = len(matched) - len(TARGET_SET)
    if missing or extra_count:
        raise ValueError(
            "Talagram fund validation failed: "
            f"found={len(matched)}/10, missing={sorted(missing)}"
        )

    return matched


def load_instruments() -> dict[str, Instrument]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Instrument).where(
                Instrument.symbol.in_(TARGET_SYMBOLS),
                Instrument.is_gold_fund.is_(True),
                Instrument.is_active.is_(True),
            )
        ).all()
        out = {row.symbol: row for row in rows}
        missing = TARGET_SET.difference(out)
        if missing:
            raise RuntimeError(
                "Active gold instruments missing from PostgreSQL: "
                + ", ".join(sorted(missing))
            )
        # Detached values remain accessible because SessionLocal expire_on_commit=False.
        return out


def stage_csv(
    data: dict[str, dict[str, Decimal]],
    instruments: dict[str, Instrument],
    as_of_date: date,
) -> Path:
    jy, jm, jd = gregorian_to_jalali(as_of_date)
    jalali = jalali_date_text(jy, jm, jd)

    tmp = CSV_PATH.with_name(CSV_PATH.name + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for symbol in TARGET_SYMBOLS:
            row = data[symbol]
            inst = instruments[symbol]
            writer.writerow(
                {
                    "symbol": symbol,
                    "legal_fund_name": inst.legal_name or symbol,
                    "as_of_date_jalali": jalali,
                    "original_bullion_weight": _fmt(row["bullion"]),
                    "original_coin_weight": _fmt(row["coin"]),
                    "gold_basis_total": _fmt(row["gold_basis"]),
                    "normalized_bullion_weight": _fmt(row["norm_bullion"]),
                    "normalized_coin_weight": _fmt(row["norm_coin"]),
                    "source_file": SOURCE_URL,
                    "normalization_rule": (
                        "bullion/(bullion+coin); coin/(bullion+coin)"
                    ),
                }
            )
        fh.flush()
        os.fsync(fh.fileno())

    # Re-read before any DB change. This catches accidental malformed output.
    with tmp.open("r", encoding="utf-8-sig", newline="") as fh:
        check = list(csv.DictReader(fh))
    if len(check) != 10 or {r["symbol"] for r in check} != TARGET_SET:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Staged CSV validation failed")
    return tmp


def upsert_postgres(
    data: dict[str, dict[str, Decimal]],
    instruments: dict[str, Instrument],
    as_of_date: date,
    source_hash: str,
) -> tuple[int, int]:
    jy, jm, jd = gregorian_to_jalali(as_of_date)
    jalali = jalali_date_text(jy, jm, jd)
    fund_ids = [int(instruments[s].id) for s in TARGET_SYMBOLS]
    inserted = 0
    updated = 0

    with SessionLocal() as session:
        with session.begin():
            existing_rows = session.scalars(
                select(AssetCompositionHistory).where(
                    AssetCompositionHistory.as_of_date == as_of_date,
                    AssetCompositionHistory.fund_id.in_(fund_ids),
                )
            ).all()
            existing = {int(row.fund_id): row for row in existing_rows}

            now_utc = datetime.now(timezone.utc)
            for symbol in TARGET_SYMBOLS:
                inst = instruments[symbol]
                values = data[symbol]
                row = existing.get(int(inst.id))
                if row is None:
                    row = AssetCompositionHistory(
                        fund_id=int(inst.id),
                        report_period_end=as_of_date,
                        report_period_end_jalali=jalali,
                        as_of_date=as_of_date,
                        as_of_date_jalali=jalali,
                        raw_bullion_weight=values["bullion"],
                        raw_coin_weight=values["coin"],
                        normalized_bullion_weight=values["norm_bullion"],
                        normalized_coin_weight=values["norm_coin"],
                        source_file=SOURCE_LABEL,
                        source_hash=source_hash,
                        loaded_at=now_utc,
                    )
                    session.add(row)
                    inserted += 1
                else:
                    row.report_period_end = as_of_date
                    row.report_period_end_jalali = jalali
                    row.as_of_date_jalali = jalali
                    row.raw_bullion_weight = values["bullion"]
                    row.raw_coin_weight = values["coin"]
                    row.normalized_bullion_weight = values["norm_bullion"]
                    row.normalized_coin_weight = values["norm_coin"]
                    row.source_file = SOURCE_LABEL
                    row.source_hash = source_hash
                    row.loaded_at = now_utc
                    updated += 1

    return inserted, updated


def activate_csv(staged: Path, as_of_date: date) -> Path | None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if CSV_PATH.exists():
        stamp = datetime.now(TEHRAN).strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"fund_asset_composition_{stamp}.csv"
        shutil.copy2(CSV_PATH, backup)

    os.replace(staged, CSV_PATH)
    return backup


def notify_bale(text: str) -> None:
    try:
        BaleBotClient.from_env().send_message(text)
    except Exception as exc:
        print(f"[WARN] Bale notification failed: {exc}", file=sys.stderr)


def print_rows(data: dict[str, dict[str, Decimal]]) -> None:
    print("symbol | raw bullion | raw coin | normalized bullion | normalized coin")
    for symbol in TARGET_SYMBOLS:
        r = data[symbol]
        print(
            f"{symbol} | {_fmt(r['bullion'])} | {_fmt(r['coin'])} | "
            f"{_fmt(r['norm_bullion'])} | {_fmt(r['norm_coin'])}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Talagram gold-fund composition and update the external CSV + PostgreSQL."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write PostgreSQL and CSV. Without this flag the script is dry-run only.",
    )
    parser.add_argument(
        "--notify-success",
        action="store_true",
        help="Send a compact Bale success message after an applied update.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Talagram HTTP timeout in seconds.",
    )
    args = parser.parse_args()

    now = datetime.now(TEHRAN)
    as_of_date = now.date()
    staged: Path | None = None

    try:
        products, source_hash = fetch_products(timeout=args.timeout)
        data = build_rows(products)
        instruments = load_instruments()
        print(f"[OK] Talagram BoxAssets: products={len(products)}, target_funds=10/10")
        print_rows(data)

        if not args.apply:
            print("[DRY-RUN] No PostgreSQL or CSV changes were made.")
            return 0

        staged = stage_csv(data, instruments, as_of_date)
        inserted, updated = upsert_postgres(
            data,
            instruments,
            as_of_date,
            source_hash,
        )
        backup = activate_csv(staged, as_of_date)
        staged = None

        jy, jm, jd = gregorian_to_jalali(as_of_date)
        jalali = jalali_date_text(jy, jm, jd)
        print(
            f"[APPLIED] date={as_of_date} ({jalali}) "
            f"db_inserted={inserted} db_updated={updated} "
            f"csv={CSV_PATH} backup={backup or '-'}"
        )

        if args.notify_success:
            notify_bale(
                "✅ بروزرسانی روزانه ترکیب دارایی انجام شد\n"
                f"منبع: Talagram / BoxAssets\n"
                "صندوق‌های معتبر: 10/10\n"
                f"تاریخ موثر: {jalali}\n"
                f"DB: {inserted} درج / {updated} بروزرسانی\n"
                "Warm-up بعدی از آخرین نسخه PostgreSQL استفاده می‌کند."
            )
        return 0

    except Exception as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        print(f"[ERROR] Talagram composition update failed: {exc}", file=sys.stderr)
        notify_bale(
            "⚠️ خطای بروزرسانی ترکیب دارایی\n"
            "منبع: Talagram / BoxAssets\n"
            f"زمان: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"خطا: {type(exc).__name__}: {exc}\n"
            "اقدام: ترکیب قبلی برای محاسبات حفظ می‌شود."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
