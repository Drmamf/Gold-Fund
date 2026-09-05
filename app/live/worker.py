from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app.database import Base, SessionLocal, engine
from app.live.karamad_client import KaramadClient
from app.live.policy import notification_ok
from app.live.sizing import is_whitelisted, live_buy_budget_rial, qty_for_budget, toman_to_rial
from app.live.store import LiveStore
from app.scheduler import MarketSchedule


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("wallex_gold.live")
STRATEGY_A = "RELATIVE_BUY_HOLD"


def _configure_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "karamad_live_a.log", encoding="utf-8"),
        ],
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _maybe_bale(text: str) -> None:
    if os.getenv("BALE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        from app.notifications.bale_client import BaleBotClient

        BaleBotClient.from_env().send_message(text)
    except Exception:
        logger.exception("Live Bale notify failed")


class LiveStrategyAWorker:
    def __init__(self):
        self.store = LiveStore(SessionLocal)
        with (PROJECT_ROOT / "config" / "strategy_a_live.yaml").open("r", encoding="utf-8") as fh:
            self.cfg = yaml.safe_load(fh) or {}
        self.schedule = MarketSchedule.from_yaml(PROJECT_ROOT / "config" / "app.yaml")
        self.whitelist = list(self.cfg.get("whitelist") or [])
        self.anchor = str(self.cfg.get("anchor_symbol") or "عیار")
        capital = self.cfg.get("capital") or {}
        self.cap_rial = toman_to_rial(capital.get("max_toman", 50_000_000))
        if os.getenv("KARAMAD_MAX_TOMAN"):
            self.cap_rial = toman_to_rial(os.getenv("KARAMAD_MAX_TOMAN"))
        execution = self.cfg.get("execution") or {}
        self.confirm_seconds = float(
            os.getenv("KARAMAD_CONFIRM_SECONDS", execution.get("confirm_seconds", 2))
        )
        self.poll_seconds = float(execution.get("poll_seconds", 20))
        self.enabled = _env_bool("KARAMAD_LIVE_ENABLED", True)
        self.dry_run = _env_bool("KARAMAD_DRY_RUN", True)
        self.kill_path = PROJECT_ROOT / "runtime_state" / "LIVE_A_KILL"
        broker = self.cfg.get("broker") or {}
        self.client = KaramadClient(
            username=os.getenv("KARAMAD_USERNAME", "").strip(),
            password=os.getenv("KARAMAD_PASSWORD", "").strip(),
            login_url=broker.get("login_url", "https://karamad.ephoenix.ir/auth/login"),
            dashboard_url=broker.get(
                "dashboard_url", "https://karamad.ephoenix.ir/dashboard/premium/stock"
            ),
            artifact_dir=PROJECT_ROOT / "logs" / "live",
            user_data_dir=PROJECT_ROOT / "runtime_state" / "chrome-profile",
            confirm_seconds=self.confirm_seconds,
            headless=_env_bool("KARAMAD_HEADLESS", False),
        )
        self._stop = False

    def request_stop(self, *_args) -> None:
        self._stop = True

    def _kill_switch(self) -> bool:
        return self.kill_path.exists()

    def _in_active_window(self, now: datetime) -> bool:
        local = now.astimezone(self.schedule.timezone)
        if not self.schedule.is_working_day(local.date()):
            return False
        start = datetime.combine(local.date(), self.schedule.active_start, tzinfo=self.schedule.timezone)
        end = datetime.combine(local.date(), self.schedule.active_end, tzinfo=self.schedule.timezone)
        return start <= local < end

    def _should_keep_session(self, now: datetime) -> bool:
        local = now.astimezone(self.schedule.timezone)
        if not self.schedule.is_working_day(local.date()):
            return False
        open_status = datetime.combine(
            local.date(), self.schedule.open_status_time, tzinfo=self.schedule.timezone
        )
        close = datetime.combine(
            local.date(), self.schedule.active_end, tzinfo=self.schedule.timezone
        )
        return open_status <= local <= close

    def run_forever(self) -> None:
        Base.metadata.create_all(bind=engine)
        self.store.ensure_state()
        logger.info(
            "Live Strategy A worker started | dry_run=%s | cap_rial=%s | enabled=%s",
            self.dry_run,
            self.cap_rial,
            self.enabled,
        )
        if not self.client.username or not self.client.password:
            raise RuntimeError("KARAMAD_USERNAME / KARAMAD_PASSWORD are required in .env")

        while not self._stop:
            try:
                self._tick()
            except Exception:
                logger.exception("Live tick failed; paper bot is independent")
                try:
                    self.client.save_debug("tick_error")
                except Exception:
                    pass
                try:
                    self.client.close()
                except Exception:
                    pass
            time.sleep(self.poll_seconds)

        self.client.close()

    def _tick(self) -> None:
        now = datetime.now(self.schedule.timezone)
        if not self.enabled or self._kill_switch():
            return
        if not self._should_keep_session(now):
            if self.client.driver is not None:
                self.client.close()
            return

        self.client.ensure_dashboard()
        if not self._in_active_window(now):
            return

        state = self.store.get_state()
        if state["frozen"]:
            logger.warning("Live account frozen: %s", state["freeze_reason"])
            return

        if not state["current_symbol"] or state["current_units"] <= 0:
            self._maybe_bootstrap()
            return

        for item in self.store.pending_rotations():
            self._execute_rotation(item)
            break

    def _maybe_bootstrap(self) -> None:
        today = datetime.now(self.schedule.timezone).date().isoformat()
        intent = f"BOOTSTRAP:{today}"
        order_id = self.store.claim_intent(
            intent,
            action="BOOTSTRAP_BUY",
            target_symbol=self.anchor,
            dry_run=self.dry_run,
        )
        if order_id is None:
            return
        try:
            balances = self.client.read_balances()
            power = int(balances.get("قدرت خرید سهام") or 0)
            budget = live_buy_budget_rial(buying_power_rial=power, cap_rial=self.cap_rial)
            quotes = self.store.latest_active_quotes()
            self.client.select_symbol(self.anchor)
            high, _low = self.client.read_threshold_prices()
            ask = int(high)
            if quotes.get(self.anchor, {}).get("best_ask"):
                ask = int(Decimal(str(quotes[self.anchor]["best_ask"])))
            qty = qty_for_budget(budget_rial=budget, price_rial=ask)
            if qty <= 0:
                self.store.update_order(
                    order_id,
                    status="SKIPPED",
                    error_message="BUDGET_TOO_SMALL",
                    details={"buying_power": power, "budget": str(budget), "ask": ask},
                )
                return
            if not is_whitelisted(self.anchor, self.whitelist):
                self.store.update_order(order_id, status="REJECTED", error_message="SYMBOL_NOT_WHITELISTED")
                return
            label, notif = self.client.place_limit(
                symbol=self.anchor,
                side="buy",
                price=ask,
                quantity=int(qty),
                actually_click=not self.dry_run,
            )
            ok, reason = (True, "DRY_RUN") if self.dry_run else notification_ok(notif)
            self.store.update_order(
                order_id,
                status="DRY_RUN" if self.dry_run else ("FILLED" if ok else "FAILED"),
                price=Decimal(ask),
                quantity=qty,
                notional_rial=qty * Decimal(ask),
                broker_notification=notif,
                error_message=None if ok else reason,
                details={"button": label, "buying_power": power, "budget": str(budget)},
            )
            if ok:
                if not self.dry_run:
                    self.store.set_state(
                        current_symbol=self.anchor,
                        current_units=qty,
                        last_signal_id=None,
                    )
                _maybe_bale(
                    f"Live A {'DRY-RUN ' if self.dry_run else ''}خرید اولیه {self.anchor}\n"
                    f"تعداد {qty} | قیمت {ask:,} ریال | بودجه {int(budget):,} ریال"
                )
            else:
                self.client.save_debug("bootstrap_failed")
                _maybe_bale(f"Live A خرید اولیه ناموفق: {reason}")
        except Exception as exc:
            logger.exception("bootstrap failed")
            self.store.update_order(order_id, status="FAILED", error_message=str(exc))
            self.client.save_debug("bootstrap_error")

    def _execute_rotation(self, item: dict) -> None:
        signal_id = int(item["signal_id"])
        cycle_id = int(item["cycle_id"])
        intent = f"SIGNAL:{signal_id}"
        source_id = item.get("source_fund_id")
        target_id = item.get("target_fund_id")
        source_symbol = self.store.symbol_for_fund_id(int(source_id)) if source_id else None
        target_symbol = self.store.symbol_for_fund_id(int(target_id)) if target_id else None
        order_id = self.store.claim_intent(
            intent,
            signal_id=signal_id,
            cycle_id=cycle_id,
            action="ROTATE",
            source_symbol=source_symbol,
            target_symbol=target_symbol,
            dry_run=self.dry_run,
        )
        if order_id is None:
            return
        state = self.store.get_state()
        try:
            if not source_symbol or not target_symbol:
                self.store.update_order(order_id, status="REJECTED", error_message="MISSING_SYMBOL")
                return
            if not is_whitelisted(source_symbol, self.whitelist) or not is_whitelisted(
                target_symbol, self.whitelist
            ):
                self.store.update_order(order_id, status="REJECTED", error_message="SYMBOL_NOT_WHITELISTED")
                return
            if state["current_symbol"] != source_symbol:
                self.store.update_order(
                    order_id,
                    status="SKIPPED",
                    error_message="LIVE_HOLDING_MISMATCH",
                    details={
                        "live_symbol": state["current_symbol"],
                        "signal_source": source_symbol,
                    },
                )
                _maybe_bale(
                    f"Live A سیگنال Rotation نادیده گرفته شد: "
                    f"هلدینگ زنده {state['current_symbol']} ≠ {source_symbol}"
                )
                return
            quotes = self.store.quotes_for_cycle(cycle_id)
            source_bid = quotes.get(source_symbol, {}).get("best_bid")
            target_ask = quotes.get(target_symbol, {}).get("best_ask")
            if source_bid is None or target_ask is None:
                self.store.update_order(order_id, status="SKIPPED", error_message="QUOTE_MISSING")
                return
            units = int(state["current_units"])
            if units <= 0:
                self.store.update_order(order_id, status="SKIPPED", error_message="NO_LIVE_UNITS")
                return

            sell_label, sell_notif = self.client.place_limit(
                symbol=source_symbol,
                side="sell",
                price=int(Decimal(str(source_bid))),
                quantity=units,
                actually_click=not self.dry_run,
            )
            sell_ok, sell_reason = (True, "DRY_RUN") if self.dry_run else notification_ok(sell_notif)
            if not sell_ok:
                self.store.update_order(
                    order_id,
                    status="FAILED",
                    broker_notification=sell_notif,
                    error_message=f"SELL_FAILED:{sell_reason}",
                    details={"sell_button": sell_label},
                )
                self.client.save_debug(f"sell_failed_{signal_id}")
                _maybe_bale(f"Live A فروش {source_symbol} ناموفق: {sell_reason}")
                return

            if not self.dry_run:
                self.store.set_state(current_symbol=None, current_units=Decimal("0"))

            balances = self.client.read_balances()
            power = int(balances.get("قدرت خرید سهام") or 0)
            buy_qty = qty_for_budget(budget_rial=power, price_rial=int(Decimal(str(target_ask))))
            if buy_qty <= 0:
                self.store.set_state(
                    frozen=True,
                    freeze_reason="SELL_OK_BUY_QTY_ZERO",
                    current_symbol=None,
                    current_units=Decimal("0"),
                )
                self.store.update_order(
                    order_id,
                    status="PARTIAL",
                    broker_notification=sell_notif,
                    error_message="BUY_QTY_ZERO_AFTER_SELL",
                )
                _maybe_bale("Live A فروش شد ولی قدرت خرید برای مقصد کافی نیست. حساب فریز شد.")
                return

            buy_label, buy_notif = self.client.place_limit(
                symbol=target_symbol,
                side="buy",
                price=int(Decimal(str(target_ask))),
                quantity=int(buy_qty),
                actually_click=not self.dry_run,
            )
            buy_ok, buy_reason = (True, "DRY_RUN") if self.dry_run else notification_ok(buy_notif)
            if not buy_ok:
                self.store.set_state(
                    frozen=True,
                    freeze_reason=f"SELL_OK_BUY_FAILED:{buy_reason}",
                    current_symbol=None,
                    current_units=Decimal("0"),
                )
                self.store.update_order(
                    order_id,
                    status="PARTIAL",
                    broker_notification=buy_notif,
                    error_message=f"BUY_FAILED:{buy_reason}",
                    details={"sell_ok": True, "buy_button": buy_label},
                )
                self.client.save_debug(f"buy_failed_{signal_id}")
                _maybe_bale(
                    f"Live A فروش {source_symbol} موفق، خرید {target_symbol} ناموفق. فریز شد.\n{buy_reason}"
                )
                return

            if not self.dry_run:
                self.store.set_state(
                    current_symbol=target_symbol,
                    current_units=Decimal(int(buy_qty)),
                    last_signal_id=signal_id,
                    frozen=False,
                    freeze_reason=None,
                )
            self.store.update_order(
                order_id,
                status="DRY_RUN" if self.dry_run else "FILLED",
                price=Decimal(str(target_ask)),
                quantity=Decimal(int(buy_qty)),
                broker_notification=buy_notif,
                details={
                    "sell_qty": units,
                    "sell_price": str(source_bid),
                    "buy_qty": int(buy_qty),
                    "buy_price": str(target_ask),
                    "sell_button": sell_label,
                    "buy_button": buy_label,
                },
            )
            _maybe_bale(
                f"Live A {'DRY-RUN ' if self.dry_run else ''}Rotation {source_symbol} → {target_symbol}\n"
                f"فروش {units} @ {int(Decimal(str(source_bid))):,} | "
                f"خرید {int(buy_qty)} @ {int(Decimal(str(target_ask))):,}"
            )
        except Exception as exc:
            logger.exception("rotation failed signal=%s", signal_id)
            self.store.update_order(order_id, status="FAILED", error_message=str(exc))
            self.client.save_debug(f"rotate_error_{signal_id}")


def main() -> int:
    _configure_logging()
    worker = LiveStrategyAWorker()
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
