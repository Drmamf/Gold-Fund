from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import CommonSnapshot, FundSnapshot, FundValuation, ValuationBatch
from app.database import SessionLocal
from app.models import AssetCompositionHistory, Instrument
from app.units import pct_points_to_fraction


ZERO = Decimal("0")
ONE = Decimal("1")


class AssetCompositionProvider(Protocol):
    def latest_for_date(
        self, trade_date: date
    ) -> Mapping[int, "AssetMix"]:
        ...


@dataclass(frozen=True)
class AssetMix:
    composition_id: int
    fund_id: int
    as_of_date: date
    bullion_weight: Decimal
    coin_weight: Decimal


class PostgresAssetCompositionProvider:
    """Read the latest composition version available on or before trade_date."""

    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    def latest_for_date(self, trade_date: date) -> Mapping[int, AssetMix]:
        with self.session_factory() as session:
            gold_fund_ids = session.scalars(
                select(Instrument.id).where(
                    Instrument.is_gold_fund.is_(True),
                    Instrument.is_active.is_(True),
                )
            ).all()

            out: dict[int, AssetMix] = {}
            for fund_id in gold_fund_ids:
                row = session.scalar(
                    select(AssetCompositionHistory)
                    .where(
                        AssetCompositionHistory.fund_id == int(fund_id),
                        AssetCompositionHistory.as_of_date <= trade_date,
                    )
                    .order_by(
                        AssetCompositionHistory.as_of_date.desc(),
                        AssetCompositionHistory.id.desc(),
                    )
                    .limit(1)
                )
                if row is None:
                    continue
                out[int(fund_id)] = AssetMix(
                    composition_id=int(row.id),
                    fund_id=int(row.fund_id),
                    as_of_date=row.as_of_date,
                    bullion_weight=Decimal(str(row.normalized_bullion_weight)),
                    coin_weight=Decimal(str(row.normalized_coin_weight)),
                )
            return out


@dataclass(frozen=True)
class ValuationConfig:
    troy_ounce_grams: Decimal
    bullion_certificate_grams: Decimal
    bullion_fineness: Decimal
    coin_pure_gold_grams: Decimal
    threshold_by_symbol: Mapping[str, tuple[Decimal, Decimal]]

    @classmethod
    def from_yaml(
        cls,
        market_path: str | Path,
        strategy_b_path: str | Path,
    ) -> "ValuationConfig":
        with Path(market_path).open("r", encoding="utf-8") as fh:
            market = yaml.safe_load(fh) or {}
        with Path(strategy_b_path).open("r", encoding="utf-8") as fh:
            strategy_b = yaml.safe_load(fh) or {}

        v = market.get("valuation", {})
        thresholds = strategy_b.get("thresholds_pct", {})
        threshold_map: dict[str, tuple[Decimal, Decimal]] = {}
        for symbol, row in thresholds.items():
            threshold_map[str(symbol)] = (
                pct_points_to_fraction(row["buy"]),
                pct_points_to_fraction(row["sell"]),
            )

        cfg = cls(
            troy_ounce_grams=Decimal(str(v.get("troy_ounce_grams", "31.1034768"))),
            bullion_certificate_grams=Decimal(
                str(v.get("bullion_certificate_grams", "0.1"))
            ),
            bullion_fineness=Decimal(str(v.get("bullion_fineness", "0.995"))),
            coin_pure_gold_grams=Decimal(
                str(v.get("coin_pure_gold_grams", "7.3197"))
            ),
            threshold_by_symbol=threshold_map,
        )
        if cfg.troy_ounce_grams <= ZERO:
            raise ValueError("troy_ounce_grams must be positive")
        if cfg.bullion_certificate_grams <= ZERO:
            raise ValueError("bullion_certificate_grams must be positive")
        if not (ZERO < cfg.bullion_fineness <= ONE):
            raise ValueError("bullion_fineness must be in (0,1]")
        if cfg.coin_pure_gold_grams <= ZERO:
            raise ValueError("coin_pure_gold_grams must be positive")
        return cfg


def _d(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _positive(value: Any) -> Optional[Decimal]:
    result = _d(value)
    return result if result is not None and result > ZERO else None


class SharedValuationEngine:
    """
    Shared, account-independent valuation engine.

    STRICT executable-price policy:
      * ETF price = Best Ask only (signal_price must equal best_ask).
      * IME bullion price = Best Ask only, supplied by CommonSnapshot.
      * IME coin price = Best Ask only, supplied by CommonSnapshot.
      * ETF NAV = TSETMC redemption NAV only.
      * There is no midpoint/last/close/settlement/NAV fallback.

    Formulas:
      pure_gold_irr_per_gram = ounce_usd * usd_irr / 31.1034768
      fair_bullion = pure_gold * 0.1 * 0.995
      bullion_bubble = ime_bullion_ask / fair_bullion - 1
      fair_coin = pure_gold * 7.3197
      coin_bubble = ime_coin_ask / fair_coin - 1

      fair_nav_factor = wb/(1+b_bullion) + wc/(1+b_coin)
      intrinsic = 1/fair_nav_factor - 1
      nominal = ETF_best_ask / TSETMC_redemption_nav - 1
      total = (1+nominal)*(1+intrinsic)-1
    """

    def __init__(
        self,
        config: ValuationConfig,
        *,
        composition_provider: AssetCompositionProvider,
    ):
        self.config = config
        self.composition_provider = composition_provider

    @classmethod
    def from_yaml(
        cls,
        market_path: str | Path,
        strategy_b_path: str | Path,
        *,
        session_factory=SessionLocal,
    ) -> "SharedValuationEngine":
        return cls(
            ValuationConfig.from_yaml(market_path, strategy_b_path),
            composition_provider=PostgresAssetCompositionProvider(session_factory),
        )

    def _invalid_row(
        self,
        fund_id: int,
        symbol: str,
        *,
        mix: AssetMix | None = None,
    ) -> FundValuation:
        thresholds = self.config.threshold_by_symbol.get(symbol)
        return FundValuation(
            fund_id=fund_id,
            nominal_bubble=None,
            intrinsic_bubble=None,
            total_bubble=None,
            buy_threshold=thresholds[0] if thresholds else None,
            sell_threshold=thresholds[1] if thresholds else None,
            valid=False,
            asset_composition_id=(mix.composition_id if mix else None),
            bullion_weight=(mix.bullion_weight if mix else None),
            coin_weight=(mix.coin_weight if mix else None),
            fair_nav_factor=None,
        )

    def calculate(
        self,
        common: CommonSnapshot,
        funds: Mapping[int, FundSnapshot],
        trade_date: date,
    ) -> ValuationBatch:
        usd = _positive(common.usd_irr)
        ounce = _positive(common.ounce_usd)
        bullion_ask = _positive(common.ime_bullion_price)
        coin_ask = _positive(common.ime_coin_price)

        common_valid = bool(
            common.valuation_inputs_usable
            and usd is not None
            and ounce is not None
            and bullion_ask is not None
            and coin_ask is not None
        )

        pure_gold = None
        fair_bullion = None
        fair_coin = None
        bullion_bubble = None
        coin_bubble = None

        if common_valid:
            assert usd is not None and ounce is not None
            assert bullion_ask is not None and coin_ask is not None
            pure_gold = ounce * usd / self.config.troy_ounce_grams
            fair_bullion = (
                pure_gold
                * self.config.bullion_certificate_grams
                * self.config.bullion_fineness
            )
            fair_coin = pure_gold * self.config.coin_pure_gold_grams
            if fair_bullion <= ZERO or fair_coin <= ZERO:
                common_valid = False
            else:
                bullion_bubble = bullion_ask / fair_bullion - ONE
                coin_bubble = coin_ask / fair_coin - ONE
                if bullion_bubble <= -ONE or coin_bubble <= -ONE:
                    common_valid = False
                    bullion_bubble = None
                    coin_bubble = None

        enriched_common = replace(
            common,
            valuation_inputs_usable=common_valid,
            bullion_bubble=bullion_bubble,
            coin_bubble=coin_bubble,
            pure_gold_irr_per_gram=pure_gold if common_valid else None,
            fair_bullion_price=fair_bullion if common_valid else None,
            fair_coin_price=fair_coin if common_valid else None,
        )

        mixes = self.composition_provider.latest_for_date(trade_date)
        rows: dict[int, FundValuation] = {}

        for fund_id, snap in funds.items():
            thresholds = self.config.threshold_by_symbol.get(snap.symbol)
            mix = mixes.get(int(fund_id))

            # Non-gold instruments (e.g. Afran) intentionally have no threshold
            # valuation row and remain invalid for the gold-relative engines.
            if thresholds is None or mix is None or not common_valid:
                rows[int(fund_id)] = self._invalid_row(
                    int(fund_id), snap.symbol, mix=mix
                )
                continue

            ask = _positive(snap.best_ask)
            signal_price = _positive(snap.signal_price)
            nav = _positive(snap.nav_redemption)
            if (
                not snap.data_valid
                or ask is None
                or signal_price is None
                or signal_price != ask
                or nav is None
                or bullion_bubble is None
                or coin_bubble is None
            ):
                rows[int(fund_id)] = self._invalid_row(
                    int(fund_id), snap.symbol, mix=mix
                )
                continue

            wb = mix.bullion_weight
            wc = mix.coin_weight
            weight_sum = wb + wc
            if wb < ZERO or wc < ZERO or abs(weight_sum - ONE) > Decimal("0.000001"):
                rows[int(fund_id)] = self._invalid_row(
                    int(fund_id), snap.symbol, mix=mix
                )
                continue

            denom_b = ONE + bullion_bubble
            denom_c = ONE + coin_bubble
            if denom_b <= ZERO or denom_c <= ZERO:
                rows[int(fund_id)] = self._invalid_row(
                    int(fund_id), snap.symbol, mix=mix
                )
                continue

            fair_nav_factor = wb / denom_b + wc / denom_c
            if fair_nav_factor <= ZERO:
                rows[int(fund_id)] = self._invalid_row(
                    int(fund_id), snap.symbol, mix=mix
                )
                continue

            intrinsic = ONE / fair_nav_factor - ONE
            nominal = ask / nav - ONE
            total = (ONE + nominal) * (ONE + intrinsic) - ONE

            rows[int(fund_id)] = FundValuation(
                fund_id=int(fund_id),
                nominal_bubble=nominal,
                intrinsic_bubble=intrinsic,
                total_bubble=total,
                buy_threshold=thresholds[0],
                sell_threshold=thresholds[1],
                valid=True,
                asset_composition_id=mix.composition_id,
                bullion_weight=wb,
                coin_weight=wc,
                fair_nav_factor=fair_nav_factor,
            )

        return ValuationBatch(common=enriched_common, funds=rows)
