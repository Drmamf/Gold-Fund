from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class InstrumentMarketConfig:
    symbol: str
    legal_name: str
    instrument_type: str
    is_gold_fund: bool
    is_anchor: bool
    ins_code: str
    isin: str | None
    requires_nav_redemption: bool


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    app: dict[str, Any]
    market: dict[str, Any]
    strategy_a: dict[str, Any]
    strategy_b: dict[str, Any]
    relative_value: dict[str, Any]
    instruments: tuple[InstrumentMarketConfig, ...]

    @property
    def instrument_by_symbol(self) -> Mapping[str, InstrumentMarketConfig]:
        return {row.symbol: row for row in self.instruments}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def _validate_strict_market_policy(market: dict[str, Any]) -> None:
    policy = market.get("market_data_policy", {})
    required = {
        "fund_valuation_price_source": "BEST_ASK_ONLY",
        "ime_valuation_price_source": "BEST_ASK_ONLY",
        "nav_source": "TSETMC_REDEMPTION_ONLY",
        "allow_price_fallback": False,
        "allow_nav_fallback": False,
    }
    for key, expected in required.items():
        actual = policy.get(key)
        if actual != expected:
            raise ValueError(
                f"Strict market policy violation: {key}={actual!r}; "
                f"expected {expected!r}."
            )


def _load_instruments(path: Path) -> tuple[InstrumentMarketConfig, ...]:
    payload = _load_yaml(path)
    rows = payload.get("instruments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("config/instruments.yaml has no instruments.")

    result: list[InstrumentMarketConfig] = []
    seen_symbols: set[str] = set()
    seen_codes: set[str] = set()

    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Invalid instrument row.")
        tsetmc = raw.get("tsetmc") or {}
        symbol = str(raw.get("symbol") or "").strip()
        ins_code = str(tsetmc.get("ins_code") or "").strip()

        if not symbol or not ins_code:
            raise ValueError(f"Instrument requires symbol + TSETMC ins_code: {raw!r}")
        if symbol in seen_symbols:
            raise ValueError(f"Duplicate symbol: {symbol}")
        if ins_code in seen_codes:
            raise ValueError(f"Duplicate TSETMC ins_code: {ins_code}")

        seen_symbols.add(symbol)
        seen_codes.add(ins_code)

        result.append(
            InstrumentMarketConfig(
                symbol=symbol,
                legal_name=str(raw.get("legal_name") or symbol),
                instrument_type=str(raw.get("instrument_type") or "UNKNOWN"),
                is_gold_fund=bool(raw.get("is_gold_fund", False)),
                is_anchor=bool(raw.get("is_anchor", False)),
                ins_code=ins_code,
                isin=(
                    str(tsetmc["isin"]).strip()
                    if tsetmc.get("isin")
                    else None
                ),
                requires_nav_redemption=bool(
                    tsetmc.get("requires_nav_redemption", False)
                ),
            )
        )

    gold = [r for r in result if r.is_gold_fund]
    anchors = [r for r in result if r.is_anchor]
    if len(gold) != 10:
        raise ValueError(f"Expected exactly 10 gold funds, found {len(gold)}.")
    if len(anchors) != 1 or anchors[0].symbol != "عیار":
        raise ValueError("Expected exactly one anchor: عیار.")
    return tuple(result)


def load_project_config(project_root: str | Path) -> ProjectConfig:
    root = Path(project_root).resolve()
    config_dir = root / "config"

    market = _load_yaml(config_dir / "market_config.yaml")
    _validate_strict_market_policy(market)

    return ProjectConfig(
        root=root,
        app=_load_yaml(config_dir / "app.yaml"),
        market=market,
        strategy_a=_load_yaml(config_dir / "strategy_a.yaml"),
        strategy_b=_load_yaml(config_dir / "strategy_b.yaml"),
        relative_value=_load_yaml(config_dir / "relative_value.yaml"),
        instruments=_load_instruments(config_dir / "instruments.yaml"),
    )
