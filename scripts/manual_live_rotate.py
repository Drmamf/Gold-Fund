#!/usr/bin/env python3
"""One-shot Live A rotate on Karamad. Stop karamad-live-a first. Does not touch paper."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.database import SessionLocal  # noqa: E402
from app.live.karamad_client import KaramadClient  # noqa: E402
from app.live.notify import notify_ops  # noqa: E402
from app.live.policy import order_rejected  # noqa: E402
from app.live.sizing import (  # noqa: E402
    meets_etf_min_notional,
    qty_for_budget,
    rotation_buy_budget_rial,
    toman_to_rial,
)
from app.live.store import LiveStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wallex_gold.live.manual")


def _client() -> KaramadClient:
    with (PROJECT_ROOT / "config" / "strategy_a_live.yaml").open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    broker = cfg.get("broker") or {}
    execution = cfg.get("execution") or {}
    return KaramadClient(
        username=os.getenv("KARAMAD_USERNAME", "").strip(),
        password=os.getenv("KARAMAD_PASSWORD", "").strip(),
        login_url=broker.get("login_url", "https://karamad.ephoenix.ir/auth/login"),
        dashboard_url=broker.get(
            "dashboard_url", "https://karamad.ephoenix.ir/dashboard/premium/stock"
        ),
        artifact_dir=PROJECT_ROOT / "logs" / "live",
        user_data_dir=PROJECT_ROOT / "runtime_state" / "chrome-profile",
        confirm_seconds=float(execution.get("confirm_seconds", 2)),
        headless=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="گوهر")
    parser.add_argument("--target", default="گنج")
    parser.add_argument("--qty", type=int, default=0, help="0 = read sellable from Karamad")
    parser.add_argument("--buy-only", action="store_true")
    parser.add_argument("--budget-rial", type=int, default=0, help="with --buy-only, size the ticket from this sleeve remainder")
    args = parser.parse_args()

    store = LiveStore(SessionLocal)
    client = _client()
    if args.buy_only:
        intent = f"MANUAL_TOPUP:{datetime.now(timezone.utc).date().isoformat()}"
    else:
        intent = f"MANUAL_SYNC:{datetime.now(timezone.utc).date().isoformat()}"
    order_id = store.claim_intent(
        intent,
        action="MANUAL_SYNC",
        source_symbol=None if args.buy_only else args.source,
        target_symbol=args.target,
        dry_run=False,
    )
    if order_id is None:
        logger.error("intent already filled: %s", intent)
        return 2
    try:
        client.ensure_dashboard()
        quotes = store.latest_active_quotes()
        source_bid = int(Decimal(str((quotes.get(args.source) or {}).get("best_bid") or 0)))
        target_ask = int(Decimal(str((quotes.get(args.target) or {}).get("best_ask") or 0)))
        if target_ask <= 0:
            raise RuntimeError(f"QUOTE_MISSING ask={target_ask}")
        if args.buy_only:
            client.select_symbol(args.target)
            have = int(client.read_sellable_qty() or 0)
            extra = args.qty
            if extra <= 0 and args.budget_rial > 0:
                extra = int(qty_for_budget(budget_rial=args.budget_rial, price_rial=target_ask))
            if extra <= 0:
                raise RuntimeError("NO_BUY_QTY")
            if not meets_etf_min_notional(qty=extra, price_rial=target_ask):
                raise RuntimeError(f"BUY_BELOW_ETF_MIN extra={extra} ask={target_ask}")
            logger.info("top-up buy %s have=%s extra=%s @ %s", args.target, have, extra, target_ask)
            buy_label, buy_notif = client.place_limit(
                symbol=args.target,
                side="buy",
                price=target_ask,
                quantity=int(extra),
                actually_click=True,
            )
            if order_rejected(buy_notif):
                raise RuntimeError(f"BUY_REJECTED:{buy_notif}")
            filled = client.wait_until_bought(
                args.target, min_qty=max(have + 1, int((have + extra) * 0.8)), timeout=120
            )
            if not filled:
                raise RuntimeError(f"BUY_NOT_FILLED:{buy_notif}")
            store.set_state(
                current_symbol=args.target,
                current_units=Decimal(int(filled)),
                frozen=False,
                freeze_reason=None,
                details={
                    "cost_rial": int(Decimal(int(filled)) * Decimal(target_ask)),
                    "entry_price": int(target_ask),
                    "manual_topup": True,
                },
            )
            store.update_order(
                order_id,
                status="FILLED",
                price=Decimal(target_ask),
                quantity=Decimal(int(filled)),
                broker_notification=buy_notif,
                details={
                    "kind": "manual_topup",
                    "had": have,
                    "extra": extra,
                    "buy_button": buy_label,
                },
            )
            logger.info("top-up done %s now x%s", args.target, filled)
            return 0
        if source_bid <= 0:
            raise RuntimeError(f"QUOTE_MISSING bid={source_bid}")

        client.select_symbol(args.source)
        qty = args.qty or int(client.read_sellable_qty() or 0)
        if qty <= 0:
            raise RuntimeError("NO_SELLABLE_QTY")
        source_bid = client.clamp_price(source_bid)
        sell_notional = Decimal(qty) * Decimal(source_bid)
        logger.info("selling %s x%s @ %s", args.source, qty, source_bid)
        sell_label, sell_notif = client.place_limit(
            symbol=args.source, side="sell", price=source_bid, quantity=qty, actually_click=True
        )
        if order_rejected(sell_notif):
            raise RuntimeError(f"SELL_REJECTED:{sell_notif}")
        if not client.wait_until_sold(args.source, timeout=120):
            raise RuntimeError(f"SELL_NOT_FILLED:{sell_notif}")

        balances = client.read_balances()
        power = int(balances.get("قدرت خرید سهام") or 0)
        budget = rotation_buy_budget_rial(buying_power_rial=power, sell_notional_rial=sell_notional)
        if not meets_etf_min_notional(
            qty=qty_for_budget(budget_rial=budget, price_rial=target_ask),
            price_rial=target_ask,
        ):
            logger.info("waiting for proceeds power=%s budget=%s", power, budget)
            import time

            time.sleep(8)
            balances = client.read_balances()
            power = int(balances.get("قدرت خرید سهام") or 0)
            budget = rotation_buy_budget_rial(
                buying_power_rial=power, sell_notional_rial=sell_notional
            )
        buy_qty = qty_for_budget(budget_rial=budget, price_rial=target_ask)
        if not meets_etf_min_notional(qty=buy_qty, price_rial=target_ask):
            raise RuntimeError(f"BUY_BELOW_ETF_MIN power={power} budget={budget} ask={target_ask}")

        logger.info("buying %s x%s @ quote %s (clamp happens after symbol select)", args.target, int(buy_qty), target_ask)
        buy_label, buy_notif = client.place_limit(
            symbol=args.target,
            side="buy",
            price=target_ask,
            quantity=int(buy_qty),
            actually_click=True,
        )
        if order_rejected(buy_notif):
            store.set_state(
                frozen=True,
                freeze_reason=f"MANUAL_SELL_OK_BUY_FAILED:{buy_notif}",
                current_symbol=None,
                current_units=Decimal("0"),
            )
            raise RuntimeError(f"BUY_REJECTED:{buy_notif}")
        filled = client.wait_until_bought(args.target, min_qty=max(1, int(buy_qty * Decimal("0.8"))), timeout=120)
        if not filled:
            store.set_state(
                frozen=True,
                freeze_reason="MANUAL_SELL_OK_BUY_NOT_FILLED",
                current_symbol=None,
                current_units=Decimal("0"),
            )
            raise RuntimeError(f"BUY_NOT_FILLED:{buy_notif}")

        store.set_state(
            current_symbol=args.target,
            current_units=Decimal(int(filled)),
            frozen=False,
            freeze_reason=None,
            details={
                "cost_rial": int(Decimal(int(filled)) * Decimal(target_ask)),
                "entry_price": int(target_ask),
                "manual_sync": True,
            },
        )
        store.update_order(
            order_id,
            status="FILLED",
            price=Decimal(target_ask),
            quantity=Decimal(int(filled)),
            broker_notification=buy_notif,
            details={
                "kind": "manual_catchup",
                "sell_qty": qty,
                "sell_price": source_bid,
                "buy_qty": int(filled),
                "buy_price": target_ask,
                "sell_button": sell_label,
                "buy_button": buy_label,
                "power_after_sell": power,
            },
        )
        logger.info("manual sync done %s -> %s x%s", args.source, args.target, filled)
        return 0
    except Exception as exc:
        logger.exception("manual sync failed")
        try:
            client.save_debug("manual_sync_failed")
        except Exception:
            pass
        store.update_order(order_id, status="FAILED", error_message=str(exc))
        notify_ops(f"Live A اصلاح دستی ناموفق: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
