from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.live.sizing import toman_to_rial
from app.models import (
    FundMarketSnapshot,
    Instrument,
    LiveAccountState,
    LiveOrder,
    MarketCycle,
    Signal,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEHRAN = ZoneInfo("Asia/Tehran")
STRATEGY_A = "RELATIVE_BUY_HOLD"
ENV_PATH = PROJECT_ROOT / ".env"
YAML_PATH = PROJECT_ROOT / "config" / "strategy_a_live.yaml"
KILL_PATH = PROJECT_ROOT / "runtime_state" / "LIVE_A_KILL"
TOMAN_PER_RIAL = Decimal("0.1")


def _reload_env() -> None:
    load_dotenv(ENV_PATH, override=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def upsert_env(updates: dict[str, str]) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for key, value in updates.items():
        line = f"{key}={value}"
        if re.search(rf"^{re.escape(key)}=", text, re.M):
            text = re.sub(rf"^{re.escape(key)}=.*$", lambda _m, ln=line: ln, text, flags=re.M)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    _reload_env()


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _toman(rial: Any) -> int:
    return int((_dec(rial) * TOMAN_PER_RIAL).to_integral_value())


def _load_live_yaml() -> dict[str, Any]:
    with YAML_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def capital_max_toman() -> int:
    _reload_env()
    if os.getenv("KARAMAD_MAX_TOMAN"):
        return int(Decimal(os.getenv("KARAMAD_MAX_TOMAN")))
    cfg = _load_live_yaml()
    return int(Decimal(str((cfg.get("capital") or {}).get("max_toman") or 50_000_000)))


def set_capital_max_toman(toman: int) -> int:
    if toman < 1:
        raise ValueError("سرمایه باید بزرگ‌تر از صفر باشد")
    if toman > 5_000_000_000:
        raise ValueError("سقف سرمایه از حد مجاز بیشتر است")
    raw = YAML_PATH.read_text(encoding="utf-8")
    if re.search(r"^(\s*max_toman:\s*)\d+", raw, re.M):
        raw = re.sub(r"^(\s*max_toman:\s*)\d+", rf"\g<1>{int(toman)}", raw, count=1, flags=re.M)
        YAML_PATH.write_text(raw, encoding="utf-8")
    else:
        cfg = _load_live_yaml()
        cfg.setdefault("capital", {})
        cfg["capital"]["max_toman"] = int(toman)
        with YAML_PATH.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    upsert_env({"KARAMAD_MAX_TOMAN": str(int(toman))})
    return int(toman)


def live_enabled() -> bool:
    _reload_env()
    if KILL_PATH.exists():
        return False
    return _env_bool("KARAMAD_LIVE_ENABLED", True)


def set_live_enabled(enabled: bool) -> bool:
    KILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        if KILL_PATH.exists():
            KILL_PATH.unlink()
        upsert_env({"KARAMAD_LIVE_ENABLED": "true"})
    else:
        KILL_PATH.write_text("stopped-from-dashboard\n", encoding="utf-8")
        upsert_env({"KARAMAD_LIVE_ENABLED": "false"})
    return live_enabled()


def broker_public() -> dict[str, Any]:
    _reload_env()
    username = (os.getenv("KARAMAD_USERNAME") or "").strip()
    password = (os.getenv("KARAMAD_PASSWORD") or "").strip()
    return {
        "national_id": username,
        "password_set": bool(password),
        "password_masked": ("•" * min(10, max(4, len(password)))) if password else "",
    }


def set_broker(*, national_id: Optional[str] = None, password: Optional[str] = None) -> dict[str, Any]:
    updates: dict[str, str] = {}
    if national_id is not None:
        national_id = national_id.strip()
        if not national_id:
            raise ValueError("کد ملی / نام کاربری کارآمد خالی است")
        updates["KARAMAD_USERNAME"] = national_id
    if password is not None:
        if not password.strip():
            raise ValueError("رمز عبور خالی است")
        updates["KARAMAD_PASSWORD"] = password
    if not updates:
        raise ValueError("چیزی برای ذخیره نبود")
    upsert_env(updates)
    return broker_public()


def _symbol_map(session: Session) -> dict[int, str]:
    rows = session.execute(select(Instrument.id, Instrument.symbol)).all()
    return {int(i): str(s) for i, s in rows}


def _latest_cycle(session: Session) -> Optional[MarketCycle]:
    return session.scalar(
        select(MarketCycle)
        .where(MarketCycle.cycle_type == "ACTIVE", MarketCycle.status == "COMPLETED")
        .order_by(desc(MarketCycle.id))
        .limit(1)
    )


def _quotes(session: Session, cycle_id: int) -> dict[str, dict[str, Any]]:
    rows = session.execute(
        select(Instrument.symbol, FundMarketSnapshot)
        .join(Instrument, Instrument.id == FundMarketSnapshot.fund_id)
        .where(FundMarketSnapshot.cycle_id == cycle_id)
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for symbol, snap in rows:
        out[str(symbol)] = {
            "best_bid": int(_dec(snap.best_bid)) if snap.best_bid is not None else None,
            "best_ask": int(_dec(snap.best_ask)) if snap.best_ask is not None else None,
            "data_valid": bool(snap.data_valid),
        }
    return out


def _state_row(session: Session) -> LiveAccountState:
    row = session.get(LiveAccountState, 1)
    if row is None:
        row = LiveAccountState(id=1, current_units=Decimal("0"), frozen=False, details={})
        session.add(row)
        session.flush()
    return row


def portfolio_and_pnl(session: Session) -> dict[str, Any]:
    row = _state_row(session)
    cycle = _latest_cycle(session)
    quotes = _quotes(session, int(cycle.id)) if cycle is not None else {}
    symbol = row.current_symbol
    units = _dec(row.current_units)
    details = dict(row.details or {})
    quote = quotes.get(symbol or "", {})
    bid = quote.get("best_bid")
    ask = quote.get("best_ask")
    cost_rial = _dec(details.get("cost_rial") or 0)
    market_rial = units * _dec(bid) if bid is not None and units > 0 else Decimal("0")
    pnl_rial = (market_rial - cost_rial) if cost_rial > 0 and units > 0 else Decimal("0")
    holdings = []
    if symbol and units > 0:
        holdings.append(
            {
                "symbol": symbol,
                "units": float(units),
                "weight_pct": 100.0,
                "best_bid_rial": bid,
                "best_ask_rial": ask,
                "market_toman": _toman(market_rial),
                "cost_toman": _toman(cost_rial) if cost_rial > 0 else None,
            }
        )
    else:
        holdings.append(
            {
                "symbol": "نقد",
                "units": 0,
                "weight_pct": 100.0,
                "best_bid_rial": None,
                "best_ask_rial": None,
                "market_toman": capital_max_toman(),
                "cost_toman": capital_max_toman(),
            }
        )
    return {
        "current_symbol": symbol,
        "current_units": float(units),
        "frozen": bool(row.frozen),
        "freeze_reason": row.freeze_reason,
        "holdings": holdings,
        "market_toman": _toman(market_rial) if units > 0 else 0,
        "cost_toman": _toman(cost_rial) if cost_rial > 0 else None,
        "pnl_toman": _toman(pnl_rial) if cost_rial > 0 and units > 0 else 0,
        "pnl_pct": float((pnl_rial / cost_rial) * 100) if cost_rial > 0 and units > 0 else 0.0,
        "mark_source": "best_bid",
        "quote_cycle_id": int(cycle.id) if cycle is not None else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def active_signals(session: Session) -> list[dict[str, Any]]:
    symbols = _symbol_map(session)
    latest = _latest_cycle(session)
    claimed = {
        int(i)
        for (i,) in session.execute(
            select(LiveOrder.signal_id).where(LiveOrder.signal_id.is_not(None))
        ).all()
        if i is not None
    }
    today = datetime.now(TEHRAN).date()
    rows = session.execute(
        select(Signal)
        .where(Signal.strategy_id == STRATEGY_A)
        .order_by(Signal.id.desc())
        .limit(20)
    ).scalars().all()
    out = []
    for sig in rows:
        generated = sig.generated_at.astimezone(TEHRAN) if sig.generated_at else None
        same_day = generated.date() == today if generated else False
        out.append(
            {
                "id": int(sig.id),
                "type": sig.signal_type,
                "source": symbols.get(int(sig.source_fund_id)) if sig.source_fund_id else None,
                "target": symbols.get(int(sig.target_fund_id)) if sig.target_fund_id else None,
                "edge_pp": float(_dec(sig.net_executable_edge) * 100) if sig.net_executable_edge is not None else None,
                "generated_at": sig.generated_at.isoformat() if sig.generated_at else None,
                "pending_live": (
                    sig.signal_type == "ROTATE_TO"
                    and int(sig.id) not in claimed
                    and same_day
                    and latest is not None
                    and int(sig.cycle_id) == int(latest.id)
                ),
                "same_session": same_day,
            }
        )
    return out


def order_history(session: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = session.execute(
        select(LiveOrder).order_by(desc(LiveOrder.created_at)).limit(limit)
    ).scalars().all()
    out = []
    for row in rows:
        out.append(
            {
                "id": int(row.id),
                "intent_key": row.intent_key,
                "action": row.action,
                "status": row.status,
                "source_symbol": row.source_symbol,
                "target_symbol": row.target_symbol,
                "price_rial": int(_dec(row.price)) if row.price is not None else None,
                "quantity": float(_dec(row.quantity)) if row.quantity is not None else None,
                "notional_toman": _toman(row.notional_rial) if row.notional_rial is not None else None,
                "dry_run": bool(row.dry_run),
                "error_message": row.error_message,
                "broker_notification": row.broker_notification,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out


def overview(session: Session) -> dict[str, Any]:
    _reload_env()
    port = portfolio_and_pnl(session)
    return {
        "live_enabled": live_enabled(),
        "dry_run": _env_bool("KARAMAD_DRY_RUN", True),
        "kill_switch": KILL_PATH.exists(),
        "capital_toman": capital_max_toman(),
        "capital_rial": int(toman_to_rial(capital_max_toman())),
        "broker": broker_public(),
        "portfolio": port,
        "pnl_toman": port["pnl_toman"],
        "signals": active_signals(session),
        "clock": datetime.now(TEHRAN).isoformat(),
        "strategy": "RELATIVE_BUY_HOLD",
        "account": "karamad",
    }
