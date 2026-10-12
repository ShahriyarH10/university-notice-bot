from __future__ import annotations

import html
import logging
import time

import requests

from notice_bot.models import Notice

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
_MAX_RATE_LIMIT_WAIT = 60


class TelegramError(RuntimeError):
    """Telegram refused or failed to deliver a message."""


def shorten(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` characters, preferring a paragraph, line or
    word boundary, and mark the cut with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    for separator in ("\n\n", "\n", " "):
        boundary = cut.rfind(separator)
        if boundary > limit // 2:
            cut = cut[:boundary]
            break
    return cut.rstrip() + "…"


def format_message(notice: Notice, body: str, *, max_body_length: int = 600) -> str:
    """Build the HTML message for a notice.

    HTML mode with escaping is used because notice titles routinely contain
    characters (`_`, `*`, `[`) that make Telegram's Markdown modes reject the
    whole message. The body is shortened *before* formatting so the closing
    link can never be cut in half.
    """
    parts = [
        "🔔 <b>New AIUB Notice</b>",
        f"📅 {html.escape(notice.date)}",
        "",
        f"📋 <b>{html.escape(notice.title)}</b>",
    ]
    if body:
        excerpt = shorten(body, max_body_length)
        parts += ["", html.escape(excerpt)]
    parts += ["", f'🔗 <a href="{html.escape(notice.url, quote=True)}">Read full notice</a>']

    message = "\n".join(parts)
    if len(message) > MESSAGE_LIMIT:  # only possible with an absurdly long title
        raise TelegramError(f"message of {len(message)} characters exceeds Telegram's limit")
    return message


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        max_attempts: int = 3,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_attempts = max_attempts

    def send_message(self, text: str) -> None:
        """Send an HTML-formatted message, waiting out Telegram rate limits."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.post(url, data=payload, timeout=self._timeout)
            except requests.RequestException as exc:
                raise TelegramError(self._redact(f"request failed: {exc}")) from None

            if response.status_code == 429 and attempt < self._max_attempts:
                wait = self._retry_after(response)
                log.warning("Telegram rate limit hit; waiting %ds", wait)
                time.sleep(wait)
                continue

            try:
                result = response.json()
            except ValueError:
                result = {}
            if response.ok and result.get("ok"):
                return
            description = result.get("description") or f"HTTP {response.status_code}"
            raise TelegramError(self._redact(f"Telegram rejected the message: {description}"))

    @staticmethod
    def _retry_after(response: requests.Response) -> int:
        try:
            wait = int(response.json()["parameters"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            wait = 5
        return min(max(wait, 1), _MAX_RATE_LIMIT_WAIT)

    def _redact(self, message: str) -> str:
        # The bot token is part of the request URL and shows up in
        # requests' exception messages; keep it out of CI logs.
        return message.replace(self._token, "***")
