from __future__ import annotations

import csv
from datetime import date, datetime, time
from decimal import Decimal
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import zipfile
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, and_, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import Instrument, MarketCycle, Signal


STRATEGY_A = "RELATIVE_BUY_HOLD"
STRATEGY_B = "THRESHOLD_10_10_RELATIVE"


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str
        )
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


class CSVExporter:
    def __init__(
        self,
        engine: Engine,
        *,
        output_dir: str | Path,
        timezone: str = "Asia/Tehran",
    ):
        self.engine = engine
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tz = ZoneInfo(timezone)

    def _day_bounds(self, trade_date: date):
        start = datetime.combine(
            trade_date, time.min, tzinfo=self.tz
        )
        end = datetime.combine(
            trade_date, time.max, tzinfo=self.tz
        )
        return start, end

    def export_daily_signals(self, trade_date: date) -> Path:
        start, end = self._day_bounds(trade_date)
        out = self.output_dir / (
            f"wallex_gold_signals_{trade_date.isoformat()}.csv"
        )

        with Session(self.engine) as session:
            instrument_rows = session.execute(
                select(Instrument.id, Instrument.symbol)
            ).all()
            symbols = {int(i): s for i, s in instrument_rows}

            signals = session.scalars(
                select(Signal)
                .where(
                    Signal.generated_at >= start,
                    Signal.generated_at <= end,
                )
                .order_by(Signal.generated_at, Signal.id)
            ).all()

            fields = [
                "signal_id",
                "generated_at",
                "strategy_id",
                "engine",
                "signal_type",
                "signal_stage",
                "fund",
                "source_fund",
                "target_fund",
                "nominal_bubble",
                "intrinsic_bubble",
                "total_bubble",
                "relative_score",
                "gross_edge",
                "spread_cost",
                "fee_cost",
                "net_executable_edge",
                "account_had_capacity",
                "trade_executed",
                "non_execution_reason",
                "payload",
            ]

            with out.open(
                "w", encoding="utf-8-sig", newline=""
            ) as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()

                for s in signals:
                    writer.writerow({
                        "signal_id": s.id,
                        "generated_at": _serialize(s.generated_at),
                        "strategy_id": s.strategy_id,
                        "engine": s.engine,
                        "signal_type": s.signal_type,
                        "signal_stage": s.signal_stage,
                        "fund": symbols.get(s.fund_id, ""),
                        "source_fund": symbols.get(s.source_fund_id, ""),
                        "target_fund": symbols.get(s.target_fund_id, ""),
                        "nominal_bubble": _serialize(s.nominal_bubble),
                        "intrinsic_bubble": _serialize(s.intrinsic_bubble),
                        "total_bubble": _serialize(s.total_bubble),
                        "relative_score": _serialize(s.relative_score),
                        "gross_edge": _serialize(s.gross_edge),
                        "spread_cost": _serialize(s.spread_cost),
                        "fee_cost": _serialize(s.fee_cost),
                        "net_executable_edge": _serialize(s.net_executable_edge),
                        "account_had_capacity": _serialize(s.account_had_capacity),
                        "trade_executed": _serialize(s.trade_executed),
                        "non_execution_reason": s.non_execution_reason or "",
                        "payload": _serialize(s.payload),
                    })

        return out

    def daily_signal_counts(self, trade_date: date) -> dict[str, int]:
        start, end = self._day_bounds(trade_date)
        with Session(self.engine) as session:
            rows = session.execute(
                select(Signal.strategy_id)
                .where(
                    Signal.generated_at >= start,
                    Signal.generated_at <= end,
                )
            ).all()

        counts = {STRATEGY_A: 0, STRATEGY_B: 0}
        for (strategy_id,) in rows:
            counts[strategy_id] = counts.get(strategy_id, 0) + 1
        return counts

    def export_full_database_zip(self, trade_date: date) -> tuple[Path, int]:
        """
        One ZIP, one CSV per PostgreSQL table. This preserves each table's
        schema instead of flattening unrelated tables into one unusable CSV.
        """
        inspector = inspect(self.engine)
        table_names = sorted(inspector.get_table_names())

        work_dir = Path(
            tempfile.mkdtemp(prefix="wallex_gold_db_backup_")
        )
        try:
            metadata = MetaData()
            metadata.reflect(bind=self.engine, only=table_names)

            with self.engine.connect() as conn:
                for table_name in table_names:
                    table = metadata.tables[table_name]
                    csv_path = work_dir / f"{table_name}.csv"

                    result = conn.execute(select(table))
                    headers = list(result.keys())

                    with csv_path.open(
                        "w", encoding="utf-8-sig", newline=""
                    ) as fh:
                        writer = csv.writer(fh)
                        writer.writerow(headers)
                        for row in result.mappings():
                            writer.writerow(
                                [_serialize(row[h]) for h in headers]
                            )

            zip_path = self.output_dir / (
                f"wallex_gold_database_backup_"
                f"{trade_date.isoformat()}.zip"
            )
            with zipfile.ZipFile(
                zip_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as zf:
                for csv_path in sorted(work_dir.glob("*.csv")):
                    zf.write(csv_path, arcname=csv_path.name)

            return zip_path, len(table_names)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
