from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Protocol

from notice_bot.categories import categorize
from notice_bot.config import LOCAL_TZ, Settings
from notice_bot.http import FetchError
from notice_bot.models import Attachment, Notice
from notice_bot.parser import parse_notice_detail, parse_notice_list
from notice_bot.state import SEEN_AT_FORMAT, SeenStore
from notice_bot.telegram import OutgoingMessage, TelegramError, format_message

log = logging.getLogger(__name__)

Fetch = Callable[[str], str]
Download = Callable[[str], bytes]


class Messenger(Protocol):
    """Where notices are delivered (TelegramClient in production)."""

    def send_message(
        self, message: OutgoingMessage, files: Sequence[tuple[Attachment, bytes]] = ()
    ) -> int: ...

    def pin_message(self, message_id: int) -> None: ...

    def unpin_message(self, message_id: int) -> None: ...


EXIT_OK = 0
EXIT_FAILED = 1


def run(
    settings: Settings,
    *,
    fetch: Fetch,
    download: Download,
    messenger: Messenger | None,
    dry_run: bool = False,
) -> int:
    """Check the notice board once and deliver anything new.

    Returns a process exit code: non-zero if the board could not be read or any
    delivery failed, so the scheduled workflow surfaces the problem.
    """
    log.info("Checking for new AIUB notices…")

    try:
        notices = parse_notice_list(fetch(settings.notice_url))
    except FetchError as exc:
        log.error("%s", exc)
        return EXIT_FAILED
    if not notices:
        log.error(
            "No notices found on %s — the page structure may have changed.", settings.notice_url
        )
        return EXIT_FAILED
    log.info("Found %d notices on the page.", len(notices))

    store = SeenStore(settings.state_file, settings.max_state_entries)
    if not store.exists:
        # First run: record the current board instead of flooding the chat
        # with every notice that is already there.
        log.info("No state file yet; recording %d existing notices without sending.", len(notices))
        seen_at = _now()
        for notice in notices:
            store.add(notice.url, title=notice.title, date=notice.date, seen_at=seen_at)
        if not dry_run:
            store.save()
        return EXIT_OK
    store.load()

    new_notices = [n for n in notices if n.url not in store]
    if not new_notices:
        log.info("No new notices. Standing by.")

    failures = 0
    # Oldest first, so they arrive in chronological order.
    for notice in reversed(new_notices):
        log.info("New notice: %s", notice.title)
        notice, message, attachments = _prepare(notice, fetch, settings)
        pin = categorize(notice.title).label in settings.pin_categories
        if dry_run:
            print("--- dry run: would send ---")
            print(f"{message.text}\n[{message.button_label}] {message.button_url}")
            for attachment in attachments:
                print(f"+ {attachment.kind}: {attachment.filename}  ({attachment.url})")
            print("(would pin)\n" if pin else "")
            continue
        assert messenger is not None
        try:
            message_id = _send(notice, message, _download(attachments, download), messenger)
        except TelegramError as exc:
            failures += 1
            log.error("Could not send %r: %s", notice.title, exc)
            continue  # not marked as seen, so the next run retries it
        store.add(notice.url, title=notice.title, date=notice.date, seen_at=_now())
        store.save()  # persist per notice so a crash can't cause duplicates

        # Pinning is best-effort: the notice itself is already delivered and
        # recorded, and retrying it would mean sending the notice again.
        if pin:
            try:
                messenger.pin_message(message_id)
            except TelegramError as exc:
                log.warning("Could not pin %r: %s", notice.title, exc)
            else:
                store.set_pinned(notice.url, message_id)
                store.save()

    if new_notices and dry_run:
        log.info("Dry run: %d notice(s) would be sent.", len(new_notices))
    elif new_notices:
        log.info("Done. %d sent, %d failed.", len(new_notices) - failures, failures)

    _unpin_old(store, messenger, settings, dry_run=dry_run)
    return EXIT_FAILED if failures else EXIT_OK


def _unpin_old(
    store: SeenStore, messenger: Messenger | None, settings: Settings, *, dry_run: bool
) -> None:
    """Unpin the bot's pins beyond the newest `max_pins`, and any older than
    `pin_days`. Pins made by people in the chat are never touched."""
    pinned = store.pinned()
    now = datetime.strptime(_now(), SEEN_AT_FORMAT).replace(tzinfo=LOCAL_TZ)
    cutoff = now - timedelta(days=settings.pin_days)
    overflow = max(len(pinned) - settings.max_pins, 0)
    expired = pinned[:overflow] + [
        e for e in pinned[overflow:] if e.seen_time is not None and e.seen_time < cutoff
    ]

    for entry in expired:
        assert entry.pinned is not None
        if dry_run:
            print(f"--- dry run: would unpin {entry.title or entry.url}")
            continue
        assert messenger is not None
        try:
            messenger.unpin_message(entry.pinned)
        except TelegramError as exc:
            # Someone deleted the message: there is nothing left to unpin.
            # Anything else may be temporary, so try again next run.
            if "not found" not in str(exc):
                log.warning("Could not unpin %r: %s", entry.title, exc)
                continue
        log.info("Unpinned %r", entry.title)
        store.set_pinned(entry.url, None)
        store.save()


def _is_truncation_of(short: str, full: str) -> bool:
    """True if `short` is the site's "..."-truncated form of `full`.

    Only letters and digits are compared: the listing also drops punctuation
    (e.g. "LL B" for "LL.B."), so an exact prefix match would miss them.
    """
    if not short.endswith("..."):
        return False
    stem, target = _alnum(short[:-3]), _alnum(full)
    return bool(stem) and target.startswith(stem)


def _alnum(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _now() -> str:
    """Current local (UTC+6) date and time, e.g. 2026-10-12 16:14:33."""
    return datetime.now(LOCAL_TZ).strftime(SEEN_AT_FORMAT)


def _prepare(
    notice: Notice, fetch: Fetch, settings: Settings
) -> tuple[Notice, OutgoingMessage, tuple[Attachment, ...]]:
    """Fetch the notice page; return the notice (with its full title, if the
    listing truncated it), the message to send and the files to send with it."""
    body = ""
    attachments: tuple[Attachment, ...] = ()
    try:
        detail = parse_notice_detail(fetch(notice.url))
    except FetchError as exc:
        log.warning("Sending %r without a body: %s", notice.title, exc)
    else:
        body = detail.body
        attachments = detail.attachments[: settings.max_attachments]
        # A notice that is only a file often has "Please Click Here for More
        # Details" as its whole text; the file itself is attached.
        if attachments and len(body) < 80 and "click here" in body.casefold():
            body = ""
        if len(detail.attachments) > len(attachments):
            log.info(
                "Sending %d of %d attachments of %r",
                len(attachments),
                len(detail.attachments),
                notice.title,
            )
        # The listing truncates long titles with "..."; prefer the full one.
        if detail.title and _is_truncation_of(notice.title, detail.title):
            notice = replace(notice, title=detail.title)
    message = format_message(
        notice, body, max_body_length=settings.max_body_length, caption=bool(attachments)
    )
    return notice, message, attachments


def _download(
    attachments: tuple[Attachment, ...], download: Download
) -> list[tuple[Attachment, bytes]]:
    """Download what can be downloaded; a missing file shouldn't hold back the notice."""
    files = []
    for attachment in attachments:
        try:
            files.append((attachment, download(attachment.url)))
        except FetchError as exc:
            log.warning("Could not download attachment %s: %s", attachment.filename, exc)
    return files


def _send(
    notice: Notice,
    message: OutgoingMessage,
    files: list[tuple[Attachment, bytes]],
    messenger: Messenger,
) -> int:
    """Send the notice with its files attached, or on its own if Telegram
    rejects the files."""
    if files:
        try:
            return messenger.send_message(message, files)
        except TelegramError as exc:
            log.warning("Sending %r without its files: %s", notice.title, exc)
    return messenger.send_message(message)
