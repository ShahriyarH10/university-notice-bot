from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

BASE_URL = "https://www.aiub.edu"
NOTICE_URL = f"{BASE_URL}/category/notices"
DEFAULT_STATE_FILE = Path("last_notices.json")
# Bangladesh Standard Time (UTC+6). It has no daylight saving, so a fixed offset is exact.
LOCAL_TZ = timezone(timedelta(hours=6), "BST")


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Secrets come from the environment only."""

    notice_url: str = NOTICE_URL
    state_file: Path = DEFAULT_STATE_FILE
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    # Longest body excerpt included in a Telegram message, in characters.
    max_body_length: int = 600
    # Oldest entries are dropped from the state file beyond this size. It must
    # stay well above the number of notices shown on the listing page, or old
    # notices could be re-sent.
    max_state_entries: int = 500
    request_timeout: float = 30.0

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        return cls(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            **overrides,  # type: ignore[arg-type]
        )
