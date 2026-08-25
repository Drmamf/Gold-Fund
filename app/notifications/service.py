from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select, text as sql_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.contracts import FundSnapshot, StrategySignal
from app.models import DataError, NotificationLog, StrategyRuntimeState
from app.notifications.bale_client import BaleBotClient
from app.notifications import templates
from app.reporting.account_reporter import (
    AccountReporter,
    STRATEGY_A,
    STRATEGY_B,
)
from app.reporting.csv_exporter import CSVExporter


class BaleNotificationCoordinator:
    """
    Operational notifications are best-effort and must never stop trading.

    Signal alerts are sent before execution, preserving the project's
    Signal != Execution separation.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        client: BaleBotClient,
        channel: str = "BALE",
        output_dir: str | Path = "./output/exports",
        timezone: str = "Asia/Tehran",
    ):
        self.engine = engine
        self.client = client
        self.channel = channel.strip().upper()
        self.tz = ZoneInfo(timezone)
        self.accounts = AccountReporter(engine, timezone=timezone)
        self.exporter = CSVExporter(
            engine,
            output_dir=output_dir,
            timezone=timezone,
        )

    def _log(
        self,
        *,
        notification_type: str,
        status: str,
        text: str = "",
        cycle_id: int | None = None,
        strategy_id: str | None = None,
        provider_message_id: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            with Session(self.engine) as session:
                with session.begin():
                    session.add(
                        NotificationLog(
                            cycle_id=cycle_id,
                            strategy_id=strategy_id,
                            channel=self.channel,
                            notification_type=notification_type,
                            recipient=self.client.config.chat_id,
                            message_hash=hashlib.sha256(
                                text.encode("utf-8")
                            ).hexdigest() if text else None,
                            status=status,
                            provider_message_id=provider_message_id,
                            error_message=error_message,
                            payload=payload or {},
                        )
                    )
        except Exception:
            # Notification logging must not destabilize the trading process.
            pass

    @staticmethod
    def _message_id(payload: dict[str, Any]) -> str | None:
        result = payload.get("result")
        if isinstance(result, dict):
            value = result.get("message_id")
            return str(value) if value is not None else None
        return None

    def send_text(
        self,
        text: str,
        *,
        notification_type: str,
        cycle_id: int | None = None,
        strategy_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        try:
            response = self.client.send_message(text)
            self._log(
                notification_type=notification_type,
                status="SENT",
                text=text,
                cycle_id=cycle_id,
                strategy_id=strategy_id,
                provider_message_id=self._message_id(response),
                payload=payload,
            )
            return True
        except Exception as exc:
            self._log(
                notification_type=notification_type,
                status="FAILED",
                text=text,
                cycle_id=cycle_id,
                strategy_id=strategy_id,
                error_message=str(exc),
                payload=payload,
            )
            return False

    def send_file(
        self,
        path: str | Path,
        *,
        caption: str,
        notification_type: str,
        strategy_id: str | None = None,
    ) -> bool:
        try:
            response = self.client.send_document(
                path, caption=caption
            )
            self._log(
                notification_type=notification_type,
                status="SENT",
                text=caption,
                strategy_id=strategy_id,
                provider_message_id=self._message_id(response),
                payload={"file": str(path)},
            )
            return True
        except Exception as exc:
            self._log(
                notification_type=notification_type,
                status="FAILED",
                text=caption,
                strategy_id=strategy_id,
                error_message=str(exc),
                payload={"file": str(path)},
            )
            return False


    def send_asset_composition_reminder(self, item: Mapping[str, Any]) -> bool:
        return self.send_text(
            templates.asset_composition_reminder_card(item),
            notification_type="ASSET_COMPOSITION_REMINDER",
            payload=dict(item),
        )

    def send_market_open_status(self, trade_date: date) -> None:
        start_today = datetime.combine(
            trade_date, time.min, tzinfo=self.tz
        )
        for strategy_id in (STRATEGY_A, STRATEGY_B):
            report = self.accounts.snapshot_report(
                strategy_id, before=start_today
            )
            self.send_text(
                templates.open_account_card(strategy_id, report),
                notification_type="MARKET_OPEN_ACCOUNT_STATUS",
                strategy_id=strategy_id,
            )

    def send_operational_start(self, at: datetime) -> None:
        self.send_text(
            templates.operational_start_card(at),
            notification_type="OPERATIONAL_START",
        )

    @staticmethod
    def _rotation_opportunity_key(
        signal: StrategySignal,
    ) -> str | None:
        """Stable user-facing identity for a Strategy-B rotation opportunity."""
        if not (
            signal.strategy_id == STRATEGY_B
            and signal.signal_type == "ROTATE_TO"
        ):
            return None

        position_id = (signal.payload or {}).get("position_id")
        if (
            position_id is None
            or signal.source_fund_id is None
            or signal.target_fund_id is None
        ):
            return None

        try:
            return ":".join(
                (
                    str(int(position_id)),
                    str(int(signal.source_fund_id)),
                    str(int(signal.target_fund_id)),
                )
            )
        except Exception:
            return None

    @staticmethod
    def _rotation_notification_transition(
        *,
        previous_state: Mapping[str, Any] | None,
        market_date: date,
        current_keys: set[str],
    ) -> tuple[set[str], dict[str, Any]]:
        """Suppress only continuously-active opportunities; absence re-arms them."""
        previous = dict(previous_state or {})
        notified: set[str] = set()

        # Always re-arm at the start of a new trading day.
        if previous.get("market_date") == market_date.isoformat():
            notified = {
                str(key)
                for key in previous.get("notified_keys", [])
                if key is not None
            }

        retained = notified & current_keys
        state = {
            "market_date": market_date.isoformat(),
            "active_keys": sorted(current_keys),
            "notified_keys": sorted(retained),
        }
        return retained, state

    def _prepare_strategy_b_rotation_notifications(
        self,
        *,
        market_date: date,
        rotation_signals: Sequence[StrategySignal],
    ) -> tuple[list[StrategySignal], set[str]]:
        keyed: dict[str, StrategySignal] = {}
        unkeyed: list[StrategySignal] = []

        for signal in rotation_signals:
            key = self._rotation_opportunity_key(signal)
            if key is None:
                # Fail-open: malformed/legacy signals must not be hidden.
                unkeyed.append(signal)
                continue
            keyed.setdefault(key, signal)

        current_keys = set(keyed)

        try:
            with Session(self.engine) as session:
                with session.begin():
                    row = session.scalar(
                        select(StrategyRuntimeState).where(
                            StrategyRuntimeState.strategy_id == STRATEGY_B,
                            StrategyRuntimeState.scope_key == "NOTIFICATION",
                            StrategyRuntimeState.state_key == "rotation_opportunities",
                        )
                    )
                    retained, state_value = self._rotation_notification_transition(
                        previous_state=(row.state_value if row is not None else {}),
                        market_date=market_date,
                        current_keys=current_keys,
                    )

                    if row is None:
                        session.add(
                            StrategyRuntimeState(
                                strategy_id=STRATEGY_B,
                                scope_key="NOTIFICATION",
                                state_key="rotation_opportunities",
                                state_value=state_value,
                            )
                        )
                    else:
                        row.state_value = state_value
                        row.updated_at = datetime.now(self.tz)

            selected = [
                signal
                for key, signal in keyed.items()
                if key not in retained
            ]
            selected.extend(unkeyed)
            return selected, current_keys

        except Exception:
            # Notification-state failures must never block trading or hide a signal.
            return list(rotation_signals), current_keys

    def _mark_strategy_b_rotation_notifications_sent(
        self,
        *,
        market_date: date,
        current_keys: set[str],
        sent_keys: set[str],
    ) -> None:
        if not sent_keys:
            return

        try:
            with Session(self.engine) as session:
                with session.begin():
                    row = session.scalar(
                        select(StrategyRuntimeState).where(
                            StrategyRuntimeState.strategy_id == STRATEGY_B,
                            StrategyRuntimeState.scope_key == "NOTIFICATION",
                            StrategyRuntimeState.state_key == "rotation_opportunities",
                        )
                    )
                    _, state_value = self._rotation_notification_transition(
                        previous_state=(row.state_value if row is not None else {}),
                        market_date=market_date,
                        current_keys=current_keys,
                    )
                    notified = set(state_value["notified_keys"])
                    notified.update(sent_keys)
                    notified.intersection_update(current_keys)
                    state_value["notified_keys"] = sorted(notified)

                    if row is None:
                        session.add(
                            StrategyRuntimeState(
                                strategy_id=STRATEGY_B,
                                scope_key="NOTIFICATION",
                                state_key="rotation_opportunities",
                                state_value=state_value,
                            )
                        )
                    else:
                        row.state_value = state_value
                        row.updated_at = datetime.now(self.tz)
        except Exception:
            # A failed state write only risks a duplicate notification later.
            pass

    def notify_signals(
        self,
        *,
        cycle_id: int,
        signals: Sequence[StrategySignal],
        funds: Mapping[int, FundSnapshot],
        strategy_id: str | None = None,
        generated_signals: Sequence[StrategySignal] | None = None,
        at: datetime | None = None,
    ) -> None:
        generated_at = at or datetime.now(self.tz)
        persisted_signals = list(signals)
        raw_signals = list(
            generated_signals if generated_signals is not None else signals
        )

        effective_strategy_id = strategy_id
        if effective_strategy_id is None:
            for signal in raw_signals or persisted_signals:
                effective_strategy_id = signal.strategy_id
                break

        rotation_signals_to_notify: list[StrategySignal] = []
        current_rotation_keys: set[str] = set()

        if effective_strategy_id == STRATEGY_B:
            raw_rotations = [
                signal
                for signal in raw_signals
                if signal.strategy_id == STRATEGY_B
                and signal.signal_type == "ROTATE_TO"
            ]
            (
                rotation_signals_to_notify,
                current_rotation_keys,
            ) = self._prepare_strategy_b_rotation_notifications(
                market_date=generated_at.date(),
                rotation_signals=raw_rotations,
            )

        # Strategy B can persist every fund that crossed its Buy Threshold
        # for full DB audit. These are candidates, not separate user-facing
        # entry actions. Bale reports exactly one threshold candidate:
        # the fund with the lowest Total Bubble in this cycle.
        threshold_candidates = [
            signal
            for signal in persisted_signals
            if signal.strategy_id == STRATEGY_B
            and signal.signal_type == "THRESHOLD_BUY"
        ]

        selected_threshold = None

        if threshold_candidates:
            def _bubble_rank(signal: StrategySignal):
                try:
                    return (0, Decimal(str(signal.total_bubble)))
                except Exception:
                    return (1, Decimal("999999"))

            selected_threshold = min(
                threshold_candidates,
                key=_bubble_rank,
            )

            payload = dict(selected_threshold.payload or {})
            payload["threshold_candidate_count"] = len(threshold_candidates)
            payload["threshold_candidate_fund_ids"] = [
                int(s.fund_id)
                for s in sorted(threshold_candidates, key=_bubble_rank)
                if s.fund_id is not None
            ]
            selected_threshold.payload = payload

        signals_to_notify = [
            signal
            for signal in persisted_signals
            if not (
                signal.strategy_id == STRATEGY_B
                and signal.signal_type == "THRESHOLD_BUY"
            )
            and not (
                effective_strategy_id == STRATEGY_B
                and signal.strategy_id == STRATEGY_B
                and signal.signal_type == "ROTATE_TO"
            )
        ]

        # Strategy-B rotations use raw generated opportunities for notification
        # state. The repository/executor's existing 30-minute persistence rule
        # remains unchanged, so this changes Bale noise only.
        if effective_strategy_id == STRATEGY_B:
            signals_to_notify.extend(rotation_signals_to_notify)

        if selected_threshold is not None:
            signals_to_notify.append(selected_threshold)

        sent_rotation_keys: set[str] = set()

        for signal in signals_to_notify:
            rotation_key = self._rotation_opportunity_key(signal)
            sent = self.send_text(
                templates.signal_card(
                    signal,
                    funds,
                    at=generated_at,
                ),
                notification_type="SIGNAL",
                cycle_id=cycle_id,
                strategy_id=signal.strategy_id,
                payload={
                    "signal_type": signal.signal_type,
                    "signal_stage": signal.signal_stage,
                    "threshold_candidate_count": (
                        signal.payload or {}
                    ).get("threshold_candidate_count"),
                    "rotation_opportunity_key": rotation_key,
                },
            )

            if sent and rotation_key is not None:
                sent_rotation_keys.add(rotation_key)

        if effective_strategy_id == STRATEGY_B:
            self._mark_strategy_b_rotation_notifications_sent(
                market_date=generated_at.date(),
                current_keys=current_rotation_keys,
                sent_keys=sent_rotation_keys,
            )

    def _suppress_repeated_stale_ounce_alert(
        self,
        *,
        source: str,
        operation: str,
        error: Exception | str,
        when: datetime,
    ) -> bool:
        """
        Keep every provider failure in data_errors, but avoid flooding Bale
        with the same TGJU stale-ounce warning every 3-minute cycle.

        Only TGJU validate_gold_ounce + STALEPRICE is throttled.
        Other API errors are still sent immediately.
        """
        error_text = str(error)
        normalized_error = (
            error_text.upper()
            .replace("_", "")
            .replace(" ", "")
        )

        if not (
            source.upper() == "TGJU"
            and operation == "validate_gold_ounce"
            and "STALEPRICE" in normalized_error
        ):
            return False

        try:
            with self.engine.connect() as conn:
                recent_exists = conn.execute(
                    sql_text("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM notification_log
                            WHERE notification_type = 'API_ERROR'
                              AND status = 'SENT'
                              AND payload->>'source' = :source
                              AND payload->>'operation' = :operation
                              AND sent_at >= (
                                  CURRENT_TIMESTAMP
                                  - INTERVAL '30 minutes'
                              )
                        )
                    """),
                    {
                        "source": source,
                        "operation": operation,
                    },
                ).scalar_one()

            return bool(recent_exists)

        except Exception:
            # Fail-open for notifications:
            # never hide a real warning if throttle lookup itself fails.
            return False

    def notify_api_error(
        self,
        *,
        source: str,
        operation: str,
        error: Exception | str,
        cycle_id: int | None = None,
        instrument_symbol: str | None = None,
        endpoint: str | None = None,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        when = occurred_at or datetime.now(self.tz)

        # Every logical failed provider call gets its own DataError row
        # and its own Bale warning. No dedup/suppression is applied.
        try:
            with Session(self.engine) as session:
                with session.begin():
                    session.add(
                        DataError(
                            cycle_id=cycle_id,
                            source=source,
                            error_type="API_FETCH_FAILED",
                            severity="ERROR",
                            message=str(error),
                            details={
                                "operation": operation,
                                "instrument_symbol": instrument_symbol,
                                "endpoint": endpoint,
                                **(details or {}),
                            },
                        )
                    )
        except Exception:
            pass

        text = templates.api_error_card(
            source=source,
            operation=operation,
            error=str(error),
            occurred_at=when,
            instrument_symbol=instrument_symbol,
            endpoint=endpoint,
        )
        payload = {
            "source": source,
            "operation": operation,
            "instrument_symbol": instrument_symbol,
            "endpoint": endpoint,
            **(details or {}),
        }

        if self._suppress_repeated_stale_ounce_alert(
            source=source,
            operation=operation,
            error=error,
            when=when,
        ):
            self._log(
                notification_type="API_ERROR",
                status="SUPPRESSED",
                text=text,
                cycle_id=cycle_id,
                payload={
                    **payload,
                    "suppression_reason": "STALE_OUNCE_30_MIN_COOLDOWN",
                },
            )
            return

        self.send_text(
            text,
            notification_type="API_ERROR",
            cycle_id=cycle_id,
            payload=payload,
        )

    def send_close_bundle(self, trade_date: date) -> None:
        for strategy_id in (STRATEGY_A, STRATEGY_B):
            report = self.accounts.snapshot_report(
                strategy_id, trade_date=trade_date
            )
            self.send_text(
                templates.close_account_card(
                    strategy_id, report
                ),
                notification_type="MARKET_CLOSE_ACCOUNT_STATUS",
                strategy_id=strategy_id,
            )

        path = self.exporter.export_daily_signals(trade_date)
        counts = self.exporter.daily_signal_counts(trade_date)
        caption = templates.signals_file_caption(
            date_text=trade_date.isoformat(),
            count_a=counts.get(STRATEGY_A, 0),
            count_b=counts.get(STRATEGY_B, 0),
        )
        self.send_file(
            path,
            caption=caption,
            notification_type="DAILY_SIGNALS_CSV",
        )

    def send_weekly_backup(self, trade_date: date) -> None:
        try:
            path, table_count = self.exporter.export_full_database_zip(
                trade_date
            )
            caption = templates.backup_caption(
                date_text=trade_date.isoformat(),
                table_count=table_count,
                file_size_bytes=path.stat().st_size,
            )
            self.send_file(
                path,
                caption=caption,
                notification_type="WEEKLY_DATABASE_BACKUP",
            )
        except Exception as exc:
            # Backup construction/upload errors are operational, not market API
            # errors. Still surface them visually in Bale if possible.
            self.send_text(
                "\n".join([
                    "🚨  ** خطای بکاپ هفتگی ** ",
                    templates.SEP,
                    f"❌ {templates.safe_text(exc, 800)}",
                    f"🕒 {datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S')}",
                ]),
                notification_type="WEEKLY_BACKUP_ERROR",
            )
