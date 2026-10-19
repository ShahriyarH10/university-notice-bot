"""The weekly digest: one message listing the notices of the past few days."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

from notice_bot.app import EXIT_FAILED, EXIT_OK, Messenger
from notice_bot.categories import CATEGORIES, categorize
from notice_bot.config import LOCAL_TZ, Settings
from notice_bot.state import SeenEntry, SeenStore
from notice_bot.telegram import MESSAGE_LIMIT, OutgoingMessage, TelegramError, safe_url

log = logging.getLogger(__name__)

_SEEN_AT_FORMAT = "%Y-%m-%d %H:%M:%S"
# Room kept free for the "…and N more" line.
_OVERFLOW_RESERVE = 40


def build_digest(
    entries: list[SeenEntry], *, now: datetime, days: int, board_url: str
) -> OutgoingMessage | None:
    """Group the notices seen in the last `days` days by category, or return
    None if there were none. Entries without a title or time (written by old
    versions of the bot) are skipped."""
    since = now - timedelta(days=days)
    recent = [e for e in entries if e.title and _seen_time(e) and since <= _seen_time(e) <= now]
    if not recent:
        return None

    groups: dict[str, list[SeenEntry]] = {}
    for entry in recent:
        groups.setdefault(categorize(entry.title or "").label, []).append(entry)

    count = f"{len(recent)} notice{'s' if len(recent) != 1 else ''}"
    lines = [
        f"🗞️ <b>Weekly digest</b>  ·  {_day(since)} \u2013 {_day(now)} {now.year}",
        f"{count} in the last {days} days",
    ]
    sections: list[list[str]] = []
    for category in CATEGORIES:
        group = groups.get(category.label)
        if not group:
            continue
        section = ["", f"{category.emoji} <b>{html.escape(category.label)}</b>"]
        section += [_line(entry) for entry in group]
        sections.append(section)

    text, left_out = _fit(lines, sections)
    if left_out:
        text += f"\n\n…and {left_out} more on the notice board."
    return OutgoingMessage(text=text, button_url=board_url, button_label="📋 All notices")


def run_digest(
    settings: Settings,
    *,
    messenger: Messenger | None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """Send the digest. Returns a process exit code."""
    store = SeenStore(settings.state_file, settings.max_state_entries)
    if not store.exists:
        log.error("No state file at %s; nothing to summarise.", settings.state_file)
        return EXIT_FAILED
    store.load()

    message = build_digest(
        store.entries(),
        now=now or datetime.now(LOCAL_TZ),
        days=settings.digest_days,
        board_url=settings.notice_url,
    )
    if message is None:
        log.info("No notices in the last %d days; no digest sent.", settings.digest_days)
        return EXIT_OK
    if dry_run:
        print("--- dry run: would send ---")
        print(f"{message.text}\n[{message.button_label}] {message.button_url}\n")
        return EXIT_OK
    assert messenger is not None
    try:
        messenger.send_message(message)
    except TelegramError as exc:
        log.error("Could not send the digest: %s", exc)
        return EXIT_FAILED
    log.info("Digest sent.")
    return EXIT_OK


def _seen_time(entry: SeenEntry) -> datetime | None:
    try:
        return datetime.strptime(entry.seen_at or "", _SEEN_AT_FORMAT).replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b}"


def _line(entry: SeenEntry) -> str:
    link = f'<a href="{html.escape(safe_url(entry.url))}">{html.escape(entry.title or "")}</a>'
    return f"• {link}"


def _fit(header: list[str], sections: list[list[str]]) -> tuple[str, int]:
    """Join header and sections, dropping trailing notice lines that would
    push the message past Telegram's limit. Returns the text and how many
    notices were dropped."""
    limit = MESSAGE_LIMIT - _OVERFLOW_RESERVE
    lines = list(header)
    left_out = 0
    for section in sections:
        heading, notices = section[:2], section[2:]
        if left_out or len("\n".join([*lines, *heading, notices[0]])) > limit:
            left_out += len(notices)
            continue
        lines += heading
        for i, line in enumerate(notices):
            if len("\n".join([*lines, line])) > limit:
                left_out += len(notices) - i
                break
            lines.append(line)
    return "\n".join(lines), left_out
