from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from notice_bot.config import Settings
from notice_bot.http import FetchError
from notice_bot.models import Notice
from notice_bot.parser import parse_notice_detail, parse_notice_list
from notice_bot.state import SeenStore
from notice_bot.telegram import TelegramError, format_message

log = logging.getLogger(__name__)

Fetch = Callable[[str], str]
Send = Callable[[str], None]

EXIT_OK = 0
EXIT_FAILED = 1


def run(settings: Settings, *, fetch: Fetch, send: Send, dry_run: bool = False) -> int:
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
        for notice in notices:
            store.add(notice.url)
        if not dry_run:
            store.save()
        return EXIT_OK
    store.load()

    new_notices = [n for n in notices if n.url not in store]
    if not new_notices:
        log.info("No new notices. Standing by.")
        return EXIT_OK

    failures = 0
    # Oldest first, so they arrive in chronological order.
    for notice in reversed(new_notices):
        log.info("New notice: %s", notice.title)
        message = _build_message(notice, fetch, settings)
        if dry_run:
            print(f"--- dry run: would send ---\n{message}\n")
            continue
        try:
            send(message)
        except TelegramError as exc:
            failures += 1
            log.error("Could not send %r: %s", notice.title, exc)
            continue  # not marked as seen, so the next run retries it
        store.add(notice.url)
        store.save()  # persist per notice so a crash can't cause duplicates

    if dry_run:
        log.info("Dry run: %d notice(s) would be sent.", len(new_notices))
    else:
        log.info("Done. %d sent, %d failed.", len(new_notices) - failures, failures)
    return EXIT_FAILED if failures else EXIT_OK


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


def _build_message(notice: Notice, fetch: Fetch, settings: Settings) -> str:
    body = ""
    try:
        detail = parse_notice_detail(fetch(notice.url))
    except FetchError as exc:
        log.warning("Sending %r without a body: %s", notice.title, exc)
    else:
        body = detail.body
        # The listing truncates long titles with "..."; prefer the full one.
        if detail.title and _is_truncation_of(notice.title, detail.title):
            notice = replace(notice, title=detail.title)
    return format_message(notice, body, max_body_length=settings.max_body_length)
