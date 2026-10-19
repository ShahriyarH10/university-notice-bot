import json
from datetime import datetime

import pytest

from notice_bot.app import EXIT_FAILED, EXIT_OK
from notice_bot.config import LOCAL_TZ, Settings
from notice_bot.digest import build_digest, run_digest
from notice_bot.state import SeenEntry
from notice_bot.telegram import MESSAGE_LIMIT, TelegramError

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=LOCAL_TZ)
BOARD = "https://www.aiub.edu/category/notices"


def entry(title, seen_at, url=None):
    url = url or "https://www.aiub.edu/" + title.lower().replace(" ", "-")
    return SeenEntry(url=url, title=title, date="12 Oct 2026", seen_at=seen_at)


def digest(entries):
    return build_digest(entries, now=NOW, days=7, board_url=BOARD)


def test_groups_the_weeks_notices_by_category():
    message = digest(
        [
            entry("Seat Plan of Mid Term Exams", "2026-10-12 10:00:00"),
            entry("Holiday Notice", "2026-10-13 10:00:00"),
            entry("Final Term Exam Schedule", "2026-10-17 10:00:00"),
        ]
    )
    assert message.text == (
        "🗞️ <b>Weekly digest</b>  ·  11 Oct \u2013 18 Oct 2026\n"
        "3 notices in the last 7 days\n"
        "\n"
        "🏖️ <b>Holiday</b>\n"
        '• <a href="https://www.aiub.edu/holiday-notice">Holiday Notice</a>\n'
        "\n"
        "📝 <b>Exams</b>\n"
        '• <a href="https://www.aiub.edu/seat-plan-of-mid-term-exams">'
        "Seat Plan of Mid Term Exams</a>\n"
        '• <a href="https://www.aiub.edu/final-term-exam-schedule">Final Term Exam Schedule</a>'
    )
    assert message.button_url == BOARD


def test_older_untimed_and_untitled_entries_are_left_out():
    message = digest(
        [
            entry("Old notice", "2026-10-10 23:59:59"),
            SeenEntry(url="https://www.aiub.edu/bare"),
            SeenEntry(url="https://www.aiub.edu/no-title", seen_at="2026-10-15 10:00:00"),
            entry("Bad time", "15:00:00"),
            entry("Recent notice", "2026-10-15 10:00:00"),
        ]
    )
    assert "1 notice in" in message.text
    assert "Recent notice" in message.text
    assert "Old notice" not in message.text


def test_quiet_week_gives_no_digest():
    assert digest([entry("Old notice", "2026-09-01 10:00:00")]) is None


def test_titles_and_links_are_escaped():
    message = digest([entry("A & <B>", "2026-10-15 10:00:00", url='https://x/a"b[1]')])
    assert '<a href="https://x/a%22b%5B1%5D">A &amp; &lt;B&gt;</a>' in message.text


def test_long_digest_is_cut_to_fit_telegram():
    entries = [entry(f"Notice {i} " + "x" * 150, "2026-10-15 10:00:00") for i in range(60)]
    message = digest(entries)
    assert len(message.text) <= MESSAGE_LIMIT
    shown = message.text.count("• ")
    assert message.text.endswith(f"…and {60 - shown} more on the notice board.")


class Outbox:
    def __init__(self, fail=False):
        self.messages, self.fail = [], fail

    def send_message(self, message):
        if self.fail:
            raise TelegramError("nope")
        self.messages.append(message.text)
        return 1


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps([{"url": "https://x", "title": "Exam", "seen_at": "2026-10-15 10:00:00"}])
    )
    return Settings(state_file=path)


def test_run_digest_sends_one_message(settings):
    outbox = Outbox()
    assert run_digest(settings, messenger=outbox, now=NOW) == EXIT_OK
    assert len(outbox.messages) == 1


def test_run_digest_failure_is_reported(settings):
    assert run_digest(settings, messenger=Outbox(fail=True), now=NOW) == EXIT_FAILED


def test_run_digest_without_state_file_fails(tmp_path):
    settings = Settings(state_file=tmp_path / "missing.json")
    assert run_digest(settings, messenger=Outbox(), now=NOW) == EXIT_FAILED


def test_run_digest_dry_run_prints(settings, capsys):
    assert run_digest(settings, messenger=None, dry_run=True, now=NOW) == EXIT_OK
    assert "Weekly digest" in capsys.readouterr().out
