from __future__ import annotations

import html
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from notice_bot.categories import categorize
from notice_bot.models import Notice
from notice_bot.parser import DATE_UNAVAILABLE

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
_MAX_RATE_LIMIT_WAIT = 60
_URL_SAFE = ":/?&=#%@+,;~!$'()*-._"


class TelegramError(RuntimeError):
    """Telegram refused or failed to deliver a message."""


@dataclass(frozen=True)
class OutgoingMessage:
    """A message plus the link shown as a button underneath it."""

    text: str
    button_url: str
    button_label: str = "🔗 Read full notice"


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


def format_message(notice: Notice, body: str, *, max_body_length: int = 600) -> OutgoingMessage:
    """Build the message for a notice.

    Layout::

        🎓 Admission · 📅 23 Sep 2026

        Notice title in bold

        ▎ excerpt, collapsed after a few lines
        [ 🔗 Read full notice ]

    HTML mode with escaping is used because notice titles routinely contain
    characters (`_`, `*`, `[`) that make Telegram's Markdown modes reject the
    whole message. The link is a button rather than part of the text, so
    shortening the body can never cut it in half.
    """
    category = categorize(notice.title)
    meta = f"{category.emoji} <b>{html.escape(category.label)}</b>"
    if notice.date != DATE_UNAVAILABLE:
        meta += f"  ·  📅 {html.escape(notice.date)}"

    parts = [meta, "", f"<b>{html.escape(notice.title)}</b>"]
    if body:
        excerpt = html.escape(shorten(body, max_body_length))
        parts += ["", f"<blockquote expandable>{excerpt}</blockquote>"]

    text = "\n".join(parts)
    if len(text) > MESSAGE_LIMIT:  # only possible with an absurdly long title
        raise TelegramError(f"message of {len(text)} characters exceeds Telegram's limit")
    # Telegram rejects the whole message if a button URL contains characters
    # it considers invalid (brackets, spaces), so encode anything unsafe.
    return OutgoingMessage(text=text, button_url=quote(notice.url, safe=_URL_SAFE))


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

    def send_message(self, message: OutgoingMessage) -> None:
        """Send a message, waiting out Telegram rate limits."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message.text,
            "parse_mode": "HTML",
            # The site's link preview card just repeats the title.
            "link_preview_options": json.dumps({"is_disabled": True}),
            "reply_markup": json.dumps(
                {"inline_keyboard": [[{"text": message.button_label, "url": message.button_url}]]}
            ),
        }

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
