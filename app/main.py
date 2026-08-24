from __future__ import annotations

import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import socket
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.collector import SharedMarketCollector
from app.asset_report_monitor import AssetCompositionReportMonitor
from app.config_loader import load_project_config
from app.daily_aggregator import PostgresDailyAggregator
from app.database import Base, SessionLocal, engine
from app.execution.router import StrategyExecutorRouter
from app.execution.strategy_a_executor import StrategyAExecutor
from app.execution.strategy_b_executor import StrategyBExecutor
from app.models import BotRun, ConfigVersion, Instrument
from app.notifications.bale_client import BaleBotClient
from app.notifications.service import BaleNotificationCoordinator
from app.pipeline import UnifiedTradingPipeline
from app.relative_value_engine import SharedRelativeValueEngine
from app.repository import PostgresRepository
from app.scheduler import MarketSchedule, TradingScheduler
from app.strategies.strategy_a_relative_buy_hold import RelativeBuyHoldStrategy
from app.strategies.strategy_b_threshold_10_10 import Threshold1010RelativeStrategy
from app.valuation_engine import SharedValuationEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _configure_logging() -> logging.Logger:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "wallex_gold_bot.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    return logging.getLogger("wallex_gold")


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another Wallex Gold bot process is already running.") from exc
        self.handle.write(str(os.getpid()))
        self.handle.flush()

    def release(self) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


class BotRunManager:
    def __init__(self, instance_id: str, version: str):
        self.instance_id = instance_id
        self.version = version
        self.run_id: int | None = None

    def start(self) -> None:
        with SessionLocal() as session:
            with session.begin():
                row = BotRun(
                    instance_id=self.instance_id,
                    host_name=socket.gethostname(),
                    process_id=os.getpid(),
                    status="RUNNING",
                    version=self.version,
                    last_heartbeat_at=datetime.now(timezone.utc),
                    details={"project_root": str(PROJECT_ROOT)},
                )
                session.add(row)
                session.flush()
                self.run_id = int(row.id)

    def heartbeat(self) -> None:
        if self.run_id is None:
            return
        try:
            with SessionLocal() as session:
                with session.begin():
                    row = session.get(BotRun, self.run_id)
                    if row is not None:
                        row.last_heartbeat_at = datetime.now(timezone.utc)
        except Exception:
            logging.getLogger("wallex_gold").exception("BotRun heartbeat failed")

    def stop(self, status: str = "STOPPED") -> None:
        if self.run_id is None:
            return
        try:
            with SessionLocal() as session:
                with session.begin():
                    row = session.get(BotRun, self.run_id)
                    if row is not None:
                        row.last_heartbeat_at = datetime.now(timezone.utc)
                        row.stopped_at = datetime.now(timezone.utc)
                        row.status = status
        except Exception:
            logging.getLogger("wallex_gold").exception("BotRun stop update failed")


def _seed_database() -> None:
    # Fresh VPS deployment becomes self-starting; no manual table creation step.
    Base.metadata.create_all(bind=engine)
    from scripts.init_db import seed_asset_composition, seed_instruments

    seed_instruments()
    seed_asset_composition(
        PROJECT_ROOT / "config" / "fund_asset_composition_gold_normalized.csv"
    )


def _instrument_ids() -> dict[str, int]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Instrument).where(Instrument.is_active.is_(True))
        ).all()
        out = {row.symbol: int(row.id) for row in rows}
    required = {
        "عیار", "کهربا", "مثقال", "گوهر", "گنج", "آلتون",
        "زر", "لیان", "رز ترنج", "زروان", "آفران",
    }
    missing = required.difference(out)
    if missing:
        raise RuntimeError("Missing seeded instruments: " + ", ".join(sorted(missing)))
    return out


def _record_config_version() -> int:
    files = [
        "app.yaml",
        "market_config.yaml",
        "relative_value.yaml",
        "strategy_a.yaml",
        "strategy_b.yaml",
        "instruments.yaml",
        "fund_asset_composition_gold_normalized.csv",
        "fund_asset_composition_report_schedule.yaml",
    ]
    snapshot: dict[str, str] = {}
    h = hashlib.sha256()
    for name in files:
        path = PROJECT_ROOT / "config" / name
        content = path.read_bytes()
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        snapshot[name] = hashlib.sha256(content).hexdigest()
    digest = h.hexdigest()

    with SessionLocal() as session:
        with session.begin():
            existing = session.scalar(
                select(ConfigVersion)
                .where(
                    ConfigVersion.config_scope == "FULL_RUNTIME",
                    ConfigVersion.version_hash == digest,
                )
                .order_by(ConfigVersion.id.desc())
                .limit(1)
            )
            if existing is not None:
                return int(existing.id)
            row = ConfigVersion(
                config_scope="FULL_RUNTIME",
                version_hash=digest,
                snapshot=snapshot,
                source_files={name: str(PROJECT_ROOT / "config" / name) for name in files},
            )
            session.add(row)
            session.flush()
            return int(row.id)


def _build_notifications(config):
    enabled = os.getenv("BALE_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }
    if not enabled:
        return None
    client = BaleBotClient.from_env()
    export_dir = config.app.get("paths", {}).get(
        "export_dir", "./output/exports"
    )
    export_path = Path(export_dir)
    if not export_path.is_absolute():
        export_path = PROJECT_ROOT / export_path
    return BaleNotificationCoordinator(
        engine=engine,
        client=client,
        output_dir=export_path,
        timezone=config.app.get("app", {}).get("timezone", "Asia/Tehran"),
    )


def build_runtime():
    config = load_project_config(PROJECT_ROOT)
    _seed_database()
    config_version_id = _record_config_version()
    instrument_ids = _instrument_ids()
    notifications = _build_notifications(config)

    collector = SharedMarketCollector(
        config=config,
        instrument_ids=instrument_ids,
        notifications=notifications,
    )
    valuation = SharedValuationEngine.from_yaml(
        PROJECT_ROOT / "config" / "market_config.yaml",
        PROJECT_ROOT / "config" / "strategy_b.yaml",
        session_factory=SessionLocal,
    )
    relative = SharedRelativeValueEngine.from_yaml(
        PROJECT_ROOT / "config" / "relative_value.yaml"
    )
    strategy_a = RelativeBuyHoldStrategy.from_yaml(
        PROJECT_ROOT / "config" / "strategy_a.yaml"
    )
    strategy_b = Threshold1010RelativeStrategy.from_yaml(
        PROJECT_ROOT / "config" / "strategy_b.yaml"
    )

    executor_a = StrategyAExecutor.from_yaml(
        PROJECT_ROOT / "config" / "strategy_a.yaml",
        PROJECT_ROOT / "config" / "relative_value.yaml",
        session_factory=SessionLocal,
    )
    executor_b = StrategyBExecutor.from_yaml(
        PROJECT_ROOT / "config" / "strategy_b.yaml",
        PROJECT_ROOT / "config" / "relative_value.yaml",
        session_factory=SessionLocal,
    )
    executor = StrategyExecutorRouter(
        {
            strategy_a.strategy_id: executor_a,
            strategy_b.strategy_id: executor_b,
        }
    )

    repository = PostgresRepository(
        session_factory=SessionLocal,
        strategy_b_lookback_days=strategy_b.ma7_lookback_days,
        config_version_id=config_version_id,
    )
    aggregator = PostgresDailyAggregator(session_factory=SessionLocal)

    pipeline = UnifiedTradingPipeline(
        collector=collector,
        valuation_engine=valuation,
        relative_engine=relative,
        repository=repository,
        executor=executor,
        daily_aggregator=aggregator,
        strategies=[strategy_a, strategy_b],
        notifications=notifications,
    )
    schedule = MarketSchedule.from_yaml(PROJECT_ROOT / "config" / "app.yaml")
    maintenance = AssetCompositionReportMonitor(
        schedule_path=PROJECT_ROOT / "config" / "fund_asset_composition_report_schedule.yaml",
        notifications=notifications,
        session_factory=SessionLocal,
    )
    return config, collector, pipeline, schedule, notifications, maintenance


def main() -> int:
    logger = _configure_logging()
    lock = SingleInstanceLock(PROJECT_ROOT / "runtime_state" / "wallex_gold.lock")
    lock.acquire()

    instance_id = os.getenv("BOT_INSTANCE_ID", "vps-main")
    version = os.getenv("BOT_VERSION", "v5-deploy-ready")
    run = BotRunManager(instance_id, version)
    collector = None
    stopping = {"requested": False}

    def _signal_handler(signum, frame):
        stopping["requested"] = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        config, collector, pipeline, schedule, notifications, maintenance = build_runtime()
        run.start()
        logger.info("Wallex Gold bot started | instance=%s | version=%s", instance_id, version)
        logger.info(
            "Schedule: Sat-Wed 12:00 status, 12:03 warmup, 12:05-17:59 active, 18:00 close, Wed 18:30 backup"
        )

        scheduler = TradingScheduler(
            schedule,
            pipeline,
            notifications=notifications,
            maintenance=maintenance,
            heartbeat_fn=run.heartbeat,
        )
        scheduler.run_forever()
        return 0
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        run.stop("STOPPED")
        return 0
    except Exception:
        logger.exception("Fatal startup/runtime error")
        run.stop("FAILED")
        return 1
    finally:
        if collector is not None:
            collector.close()
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
