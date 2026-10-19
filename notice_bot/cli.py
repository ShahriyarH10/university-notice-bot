from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from notice_bot import __version__
from notice_bot.app import EXIT_FAILED, run
from notice_bot.config import DEFAULT_STATE_FILE, Settings
from notice_bot.digest import run_digest
from notice_bot.http import create_session, fetch_file, fetch_html
from notice_bot.state import StateError
from notice_bot.telegram import TelegramClient

log = logging.getLogger("notice_bot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notice_bot",
        description="Forward new AIUB notices to a Telegram chat.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="JSON file of already-sent notice URLs (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print messages instead of sending them; the state file is not modified",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="send a summary of the past week's notices instead of checking the board",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    settings = Settings.from_env(state_file=args.state_file)
    if not args.dry_run and not (settings.telegram_token and settings.telegram_chat_id):
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (or use --dry-run).")
        return EXIT_FAILED

    messenger = None
    if not args.dry_run:
        assert settings.telegram_token and settings.telegram_chat_id
        messenger = TelegramClient(settings.telegram_token, settings.telegram_chat_id)

    try:
        if args.digest:
            return run_digest(settings, messenger=messenger, dry_run=args.dry_run)
        session = create_session()
        fetch = partial(fetch_html, session, timeout=settings.request_timeout)
        download = partial(
            fetch_file,
            session,
            timeout=settings.request_timeout,
            max_bytes=settings.max_attachment_bytes,
        )
        return run(
            settings, fetch=fetch, download=download, messenger=messenger, dry_run=args.dry_run
        )
    except StateError as exc:
        log.error("%s", exc)
        return EXIT_FAILED
