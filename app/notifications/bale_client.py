from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Optional

import requests


class BaleAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class BaleClientConfig:
    token: str
    chat_id: str
    base_url: str = "https://tapi.bale.ai"
    timeout_seconds: float = 15.0
    max_document_bytes: int = 50_000_000

    @classmethod
    def from_env(cls) -> "BaleClientConfig":
        token = os.getenv("BALE_BOT_TOKEN", "").strip()
        chat_id = os.getenv("BALE_CHAT_ID", "").strip()
        if not token:
            raise RuntimeError("BALE_BOT_TOKEN is not set.")
        if not chat_id:
            raise RuntimeError("BALE_CHAT_ID is not set.")

        return cls(
            token=token,
            chat_id=chat_id,
            base_url=os.getenv(
                "BALE_BASE_URL", "https://tapi.bale.ai"
            ).rstrip("/"),
            timeout_seconds=float(
                os.getenv("BALE_REQUEST_TIMEOUT_SECONDS", "15")
            ),
            max_document_bytes=int(
                os.getenv("BALE_MAX_DOCUMENT_BYTES", "50000000")
            ),
        )


class BaleBotClient:
    """
    Thin HTTP client for Bale Bot API.

    Deliberately no automatic POST retry is performed. A lost HTTP response
    after Bale accepted a POST could otherwise duplicate an operational alert.
    """

    def __init__(
        self,
        config: BaleClientConfig,
        *,
        session: Optional[requests.Session] = None,
    ):
        self.config = config
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "BaleBotClient":
        return cls(BaleClientConfig.from_env())

    def _url(self, method: str) -> str:
        return (
            f"{self.config.base_url}/bot"
            f"{self.config.token}/{method}"
        )

    @staticmethod
    def _parse_response(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise BaleAPIError(
                f"Bale returned non-JSON response: HTTP {response.status_code}"
            ) from exc

        if response.status_code >= 400 or not payload.get("ok", False):
            description = payload.get("description") or response.text
            code = payload.get("error_code") or response.status_code
            raise BaleAPIError(
                f"Bale API error {code}: {description}"
            )
        return payload

    def send_message(self, text: str) -> dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("Bale message text is empty.")
        if len(text) > 4096:
            raise ValueError(
                f"Bale message exceeds 4096 characters: {len(text)}"
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

        size = path.stat().st_size
        if size > self.config.max_document_bytes:
            raise BaleAPIError(
                f"Document is too large for configured Bale limit: "
                f"{size:,} > {self.config.max_document_bytes:,} bytes"
            )

        if len(caption) > 4096:
            raise ValueError(
                f"Bale document caption exceeds 4096 characters: {len(caption)}"
            )

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
