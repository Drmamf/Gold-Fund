from __future__ import annotations

import logging
import os

logger = logging.getLogger("wallex_gold.live")


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def notify_ops(text: str) -> None:
    """Send live-account messages to Telegram (preferred) and Bale if enabled."""
    if not text or not text.strip():
        return
    if _enabled("TELEGRAM_ENABLED", "false"):
        try:
            from app.notifications.telegram_client import TelegramBotClient

            TelegramBotClient.from_env().send_message(text)
        except Exception:
            logger.exception("Live Telegram notify failed")
    if _enabled("BALE_ENABLED", "false"):
        try:
            from app.notifications.bale_client import BaleBotClient

            BaleBotClient.from_env().send_message(text)
        except Exception:
            logger.exception("Live Bale notify failed")
