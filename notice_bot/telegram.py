from __future__ import annotations

import html
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from notice_bot.categories import categorize
from notice_bot.models import Attachment, Notice
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
    return OutgoingMessage(text=text, button_url=safe_url(notice.url))


def safe_url(url: str) -> str:
    """Telegram rejects a whole message if a link contains characters it
    considers invalid (brackets, spaces), so encode anything unsafe."""
    return quote(url, safe=_URL_SAFE)


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        upload_timeout: float = 120.0,
        max_attempts: int = 3,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self._timeout = timeout
        self._upload_timeout = upload_timeout
        self._max_attempts = max_attempts

    def send_message(self, message: OutgoingMessage) -> int:
        """Send a message and return its message ID."""
        result = self._call(
            "sendMessage",
            {
                "text": message.text,
                "parse_mode": "HTML",
                # The site's link preview card just repeats the title.
                "link_preview_options": json.dumps({"is_disabled": True}),
                "reply_markup": json.dumps(
                    {
                        "inline_keyboard": [
                            [{"text": message.button_label, "url": message.button_url}]
                        ]
                    }
                ),
            },
        )
        return int(result["message_id"])

    def send_file(self, attachment: Attachment, content: bytes, *, reply_to: int) -> None:
        """Upload a notice's file as a silent reply to the notice message.

        The file is uploaded rather than passed to Telegram as a URL because
        aiub.edu's Cloudflare protection would likely block Telegram's servers.
        """
        data = {
            "reply_parameters": json.dumps(
                {"message_id": reply_to, "allow_sending_without_reply": True}
            ),
            "disable_notification": "true",
        }
        if attachment.label:
            data["caption"] = attachment.label
        if attachment.kind == "photo":
            try:
                self._call("sendPhoto", data, files={"photo": (attachment.filename, content)})
                return
            except TelegramError as exc:
                # e.g. a very tall scanned notice exceeds the photo size ratio.
                log.info("Sending %s as a document instead: %s", attachment.filename, exc)
        self._call("sendDocument", data, files={"document": (attachment.filename, content)})

    def pin_message(self, message_id: int) -> None:
        """Pin a message without notifying the chat a second time. In a group
        or channel the bot must be an admin allowed to pin messages."""
        self._call(
            "pinChatMessage", {"message_id": str(message_id), "disable_notification": "true"}
        )

    def _call(
        self,
        method: str,
        data: dict[str, str],
        *,
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> Any:
        """Call a Bot API method and return its result, waiting out rate limits."""
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        payload = {"chat_id": self._chat_id, **data}
        timeout = self._upload_timeout if files else self._timeout

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.post(url, data=payload, files=files, timeout=timeout)
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
                return result.get("result") or {}
            description = result.get("description") or f"HTTP {response.status_code}"
            raise TelegramError(self._redact(f"Telegram rejected the {method}: {description}"))
        raise AssertionError("unreachable")

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
