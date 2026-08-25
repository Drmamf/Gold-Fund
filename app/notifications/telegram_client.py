from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Optional

import requests
from andro_cfw import CFWSession


class TelegramAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramClientConfig:
    token: str
    chat_id: str
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "TelegramClientConfig":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
        if not chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is not set.")

        return cls(
            token=token,
            chat_id=chat_id,
            timeout_seconds=float(
                os.getenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "15")
            ),
        )


class TelegramBotClient:
    """
    Thin Telegram Bot API client routed through andro-cfw.
    """

    def __init__(
        self,
        config: TelegramClientConfig,
        *,
        cfw_session: Optional[CFWSession] = None,
        session: Optional[requests.Session] = None,
    ):
        self.config = config
        self.cfw_session = cfw_session or CFWSession.load()
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "TelegramBotClient":
        return cls(TelegramClientConfig.from_env())

    def _url(self, method: str) -> str:
        base_url = self.cfw_session.api_base_url().rstrip("/")
        return f"{base_url}/bot{self.config.token}/{method}"

    @staticmethod
    def _parse_response(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise TelegramAPIError(
                f"Telegram returned non-JSON response: "
                f"HTTP {response.status_code}"
            ) from exc

        if response.status_code >= 400 or not payload.get("ok", False):
            description = payload.get("description") or response.text
            code = payload.get("error_code") or response.status_code
            raise TelegramAPIError(
                f"Telegram API error {code}: {description}"
            )

        return payload

    def send_message(self, text: str) -> dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("Telegram message text is empty.")

        if len(text) > 4096:
            raise ValueError(
                f"Telegram message exceeds 4096 characters: {len(text)}"
            )

        response = self.session.post(
            self._url("sendMessage"),
            json={
                "chat_id": self.config.chat_id,
                "text": text,
            },
            timeout=self.config.timeout_seconds,
        )

        return self._parse_response(response)

    def send_document(
        self,
        file_path: str | Path,
        *,
        caption: str = "",
    ) -> dict[str, Any]:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        with path.open("rb") as fh:
            response = self.session.post(
                self._url("sendDocument"),
                data={
                    "chat_id": self.config.chat_id,
                    "caption": caption,
                },
                files={
                    "document": (
                        path.name,
                        fh,
                        "application/octet-stream",
                    )
                },
                timeout=self.config.timeout_seconds,
            )

        return self._parse_response(response)
