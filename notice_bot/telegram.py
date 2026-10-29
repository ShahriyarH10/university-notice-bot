from __future__ import annotations

import html
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from notice_bot.categories import categorize
from notice_bot.models import Attachment, Notice
from notice_bot.parser import DATE_UNAVAILABLE

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
# Captions on photos and documents are much shorter than messages.
CAPTION_LIMIT = 1024
# Telegram sends 2-10 files as one album.
ALBUM_LIMIT = 10
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

    @property
    def text_with_link(self) -> str:
        """The text with the link written into it, for albums, which can't
        have buttons."""
        return self.text + _link_suffix(self.button_url, self.button_label)


def _link_suffix(url: str, label: str = OutgoingMessage.button_label) -> str:
    return f'\n\n<a href="{html.escape(url)}">{html.escape(label)}</a>'


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


def format_message(
    notice: Notice, body: str, *, max_body_length: int = 600, caption: bool = False
) -> OutgoingMessage:
    """Build the message for a notice. With `caption`, it is kept short enough
    to be the caption of the notice's files, link included.

    Layout::

        🎓 #Admission · 📅 23 Sep 2026

        Notice title in bold

        ▎ excerpt, collapsed after a few lines
        [ 🔗 Read full notice ]

    HTML mode with escaping is used because notice titles routinely contain
    characters (`_`, `*`, `[`) that make Telegram's Markdown modes reject the
    whole message. The link is a button rather than part of the text, so
    shortening the body can never cut it in half.
    """
    category = categorize(notice.title)
    meta = f"{category.emoji} {category.hashtag}"
    if notice.date != DATE_UNAVAILABLE:
        meta += f"  ·  📅 {html.escape(notice.date)}"

    url = safe_url(notice.url)
    head = "\n".join([meta, "", f"<b>{html.escape(notice.title)}</b>"])
    # Telegram counts characters after parsing the HTML, so measuring the
    # markup errs on the safe side.
    limit = CAPTION_LIMIT - len(_link_suffix(url)) if caption else MESSAGE_LIMIT

    text = head
    while body:
        excerpt = html.escape(shorten(body, max_body_length))
        text = f"{head}\n\n<blockquote expandable>{excerpt}</blockquote>"
        excess = len(text) - limit
        if excess <= 0:
            break
        max_body_length -= excess
        if max_body_length < 50:  # not worth showing
            text = head
            break
    if len(text) > limit:  # only possible with an absurdly long title
        raise TelegramError(f"message of {len(text)} characters exceeds Telegram's limit")
    return OutgoingMessage(text=text, button_url=url)


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

    def send_message(
        self, message: OutgoingMessage, files: Sequence[tuple[Attachment, bytes]] = ()
    ) -> int:
        """Send a notice, with its files attached if there are any, and return
        the ID of the message to pin.

        Files are uploaded rather than passed to Telegram as URLs because
        aiub.edu's Cloudflare protection would likely block Telegram's servers.
        `message` must have been formatted as a caption when there are files.
        """
        if not files:
            result = self._call(
                "sendMessage",
                {
                    "text": message.text,
                    "parse_mode": "HTML",
                    # The site's link preview card just repeats the title.
                    "link_preview_options": json.dumps({"is_disabled": True}),
                    "reply_markup": self._button(message),
                },
            )
            return int(result["message_id"])
        if len(files) == 1:
            return self._send_file(message, *files[0])
        return self._send_album(message, files[:ALBUM_LIMIT])

    def _send_file(self, message: OutgoingMessage, attachment: Attachment, content: bytes) -> int:
        data = {
            "caption": message.text,
            "parse_mode": "HTML",
            "reply_markup": self._button(message),
        }
        if attachment.kind == "photo":
            try:
                result = self._call(
                    "sendPhoto", data, files={"photo": (attachment.filename, content)}
                )
                return int(result["message_id"])
            except TelegramError as exc:
                # e.g. a very tall scanned notice exceeds the photo size ratio.
                log.info("Sending %s as a document instead: %s", attachment.filename, exc)
        result = self._call(
            "sendDocument", data, files={"document": (attachment.filename, content)}
        )
        return int(result["message_id"])

    def _send_album(
        self, message: OutgoingMessage, files: Sequence[tuple[Attachment, bytes]]
    ) -> int:
        """Send the files as one album with the notice as the first caption.

        An album can't mix photos with documents, so a mixed set is sent as
        documents.
        """
        if all(attachment.kind == "photo" for attachment, _ in files):
            try:
                return self._call_album(message, files, "photo")
            except TelegramError as exc:
                log.info("Sending the photos as documents instead: %s", exc)
        return self._call_album(message, files, "document")

    def _call_album(
        self, message: OutgoingMessage, files: Sequence[tuple[Attachment, bytes]], kind: str
    ) -> int:
        media: list[dict[str, str]] = []
        uploads: dict[str, tuple[str, bytes]] = {}
        for i, (attachment, content) in enumerate(files):
            item = {"type": kind, "media": f"attach://file{i}"}
            if i == 0:
                item.update(caption=message.text_with_link, parse_mode="HTML")
            elif attachment.label and kind == "document":
                # Each document shows its own caption; a photo album only one.
                item["caption"] = attachment.label
            media.append(item)
            uploads[f"file{i}"] = (attachment.filename, content)
        result = self._call("sendMediaGroup", {"media": json.dumps(media)}, files=uploads)
        return int(result[0]["message_id"])

    @staticmethod
    def _button(message: OutgoingMessage) -> str:
        return json.dumps(
            {"inline_keyboard": [[{"text": message.button_label, "url": message.button_url}]]}
        )

    def pin_message(self, message_id: int) -> None:
        """Pin a message without notifying the chat a second time. In a group
        or channel the bot must be an admin allowed to pin messages."""
        self._call(
            "pinChatMessage", {"message_id": str(message_id), "disable_notification": "true"}
        )

    def unpin_message(self, message_id: int) -> None:
        self._call("unpinChatMessage", {"message_id": str(message_id)})

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
