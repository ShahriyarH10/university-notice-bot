import json
from dataclasses import replace
from pathlib import Path

import pytest

from notice_bot.app import EXIT_FAILED, EXIT_OK, run
from notice_bot.config import Settings
from notice_bot.http import FetchError
from notice_bot.telegram import TelegramError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "listing.html").read_text(encoding="utf-8")
DETAIL = (FIXTURES / "detail.html").read_text(encoding="utf-8")
DETAIL_FILES = (FIXTURES / "detail_attachments.html").read_text(encoding="utf-8")

NOW = "2026-10-12 16:14:33"
URL_1 = "https://www.aiub.edu/general-notice-regarding-web-groups"
URL_2 = "https://www.aiub.edu/admission-result_[slot-1]"
URL_3 = "https://www.aiub.edu/no-date-notice"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("notice_bot.app._now", lambda: NOW)


@pytest.fixture
def settings(tmp_path):
    return Settings(state_file=tmp_path / "state.json", notice_url="https://board")


class Site:
    """Stands in for the network: serves the listing and detail pages."""

    def __init__(self, listing=LISTING, detail=DETAIL, broken=()):
        self.listing, self.detail, self.broken = listing, detail, set(broken)
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        if url in self.broken:
            raise FetchError(f"boom {url}")
        return self.listing if url == "https://board" else self.detail

    def download(self, url):
        self.requested.append(url)
        if url in self.broken:
            raise FetchError(f"boom {url}")
        return f"bytes of {url}".encode()


class Outbox:
    """Stands in for Telegram: records what was sent, files and pins."""

    def __init__(self, fail_on=(), fail_files=False, fail_pins=False, unpin_error=None):
        self.messages, self.fail_on = [], tuple(fail_on)
        self.files, self.pins, self.unpins = [], [], []
        self.fail_files, self.fail_pins, self.unpin_error = fail_files, fail_pins, unpin_error

    def send_message(self, message):
        if any(marker in message.text for marker in self.fail_on):
            raise TelegramError("rejected")
        self.messages.append(message.text)
        return len(self.messages)  # message ID

    def send_file(self, attachment, content, *, reply_to):
        if self.fail_files:
            raise TelegramError("file rejected")
        self.files.append((attachment.filename, content, reply_to))

    def pin_message(self, message_id):
        if self.fail_pins:
            raise TelegramError("not an admin")
        self.pins.append(message_id)

    def unpin_message(self, message_id):
        if self.unpin_error:
            raise TelegramError(self.unpin_error)
        self.unpins.append(message_id)


def check(settings, site=None, outbox=None, **kwargs):
    site = site or Site()
    return run(settings, fetch=site, download=site.download, messenger=outbox, **kwargs)


def saved_entries(settings):
    return json.loads(settings.state_file.read_text())


def saved(settings):
    return [e if isinstance(e, str) else e["url"] for e in saved_entries(settings)]


def test_first_run_records_board_without_sending(settings):
    outbox = Outbox()
    assert check(settings, Site(), outbox) == EXIT_OK
    assert outbox.messages == []
    assert saved(settings) == [URL_1, URL_2, URL_3]


def test_sends_only_unseen_notices_oldest_first(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox()
    assert check(settings, Site(), outbox) == EXIT_OK
    assert len(outbox.messages) == 2
    assert "Slot_1" in outbox.messages[0]  # second card is older than the first
    assert "General Notice" in outbox.messages[1]
    assert saved(settings) == [URL_3, URL_2, URL_1]


def test_truncated_listing_title_is_replaced_by_full_title(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    check(settings, Site(), outbox)
    assert "(regarding usage of web groups/ blogs/ social media)" in outbox.messages[0]


def test_nothing_new_sends_nothing(settings):
    settings.state_file.write_text(json.dumps([URL_1, URL_2, URL_3]))
    site, outbox = Site(), Outbox()
    assert check(settings, site, outbox) == EXIT_OK
    assert outbox.messages == []
    assert site.requested == ["https://board"]  # no detail pages fetched


def test_failed_delivery_is_retried_next_run_and_fails_the_run(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox(fail_on=["Slot_1"])
    assert check(settings, Site(), outbox) == EXIT_FAILED
    assert len(outbox.messages) == 1  # the other notice still went out
    assert saved(settings) == [URL_3, URL_1]

    assert check(settings, Site(), (retry := Outbox())) == EXIT_OK
    assert len(retry.messages) == 1
    assert "Slot_1" in retry.messages[0]


def test_unreachable_notice_page_still_sends_without_body(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    assert check(settings, Site(broken={URL_1}), outbox) == EXIT_OK
    assert len(outbox.messages) == 1
    assert "General Notice" in outbox.messages[0]
    assert "blockquote" not in outbox.messages[0]


def test_unreachable_board_fails_the_run(settings):
    outbox = Outbox()
    assert check(settings, Site(broken={"https://board"}), outbox) == EXIT_FAILED
    assert outbox.messages == []
    assert not settings.state_file.exists()


def test_board_without_notices_fails_the_run(settings):
    site = Site(listing="<html><body>Cloudflare challenge</body></html>")
    assert check(settings, site, Outbox()) == EXIT_FAILED


def test_dry_run_prints_and_leaves_state_untouched(settings, capsys):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox()
    assert check(settings, Site(), outbox, dry_run=True) == EXIT_OK
    assert outbox.messages == []
    assert saved(settings) == [URL_3]
    assert capsys.readouterr().out.count("would send") == 2


def test_dry_run_first_run_does_not_create_state(settings):
    check(settings, Site(), Outbox(), dry_run=True)
    assert not settings.state_file.exists()


def test_state_records_title_board_date_and_time_seen(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    check(settings, Site(), Outbox())
    # The full title from the notice page is stored, not the truncated one.
    assert saved_entries(settings)[-1] == {
        "url": URL_1,
        "title": "General Notice (regarding usage of web groups/ blogs/ social media)",
        "date": "12 Oct 2026",
        "seen_at": NOW,
    }


def test_first_run_records_details_too(settings):
    check(settings, Site(), Outbox())
    assert saved_entries(settings)[1] == {
        "url": URL_2,
        "title": "Result & Viva: *Slot_1*",
        "date": "5 September 2026",
        "seen_at": NOW,
    }


def test_attachments_are_sent_as_replies_to_their_notice(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    assert check(settings, Site(detail=DETAIL_FILES), outbox) == EXIT_OK
    assert [(name, reply_to) for name, _, reply_to in outbox.files] == [
        ("ll.-b.-1.pdf", 1),
        ("b.-pharm-1.pdf", 1),
        ("exam-schedule-[ug-&-pg).pdf", 1),
        ("1(2).webp", 1),
    ]
    assert outbox.files[0][1] == b"bytes of https://www.aiub.edu/Files/Uploads/ll.-b.-1.pdf"


def test_attachments_beyond_the_limit_are_not_sent(settings):
    settings = replace(settings, max_attachments=2)
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    check(settings, Site(detail=DETAIL_FILES), outbox)
    assert len(outbox.files) == 2


def test_failed_attachments_do_not_fail_the_run_or_resend_the_notice(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    broken = {"https://www.aiub.edu/Files/Uploads/ll.-b.-1.pdf"}
    outbox = Outbox()
    assert check(settings, Site(detail=DETAIL_FILES, broken=broken), outbox) == EXIT_OK
    assert len(outbox.files) == 3  # the others still went out
    assert URL_1 in saved(settings)


def test_rejected_attachments_still_record_the_notice(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox(fail_files=True)
    assert check(settings, Site(detail=DETAIL_FILES), outbox) == EXIT_OK
    assert len(outbox.messages) == 1
    assert URL_1 in saved(settings)


def test_notices_in_pinned_categories_are_pinned(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox()
    check(settings, Site(), outbox)
    # Message 1 is "Result & Viva" (Exams); message 2 is a general notice.
    assert outbox.pins == [1]


def test_failed_pin_does_not_fail_the_run(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox(fail_pins=True)
    assert check(settings, Site(), outbox) == EXIT_OK
    assert len(outbox.messages) == 2


def test_dry_run_lists_attachments_and_pins(settings, capsys):
    settings.state_file.write_text(json.dumps([URL_1, URL_3]))
    site = Site(detail=DETAIL_FILES)
    check(settings, site, None, dry_run=True)
    out = capsys.readouterr().out
    assert "+ document: ll.-b.-1.pdf" in out
    assert "(would pin)" in out
    assert not any("Files/Uploads" in url for url in site.requested)  # nothing downloaded


def test_click_here_placeholder_body_is_dropped_when_files_follow(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    detail = (
        "<h1>Mid Term Exam Schedule</h1><div class='question-column'><p>"
        "<a href='/Files/Uploads/schedule.pdf'>Please Click Here for More Details</a></p></div>"
    )
    outbox = Outbox()
    check(settings, Site(detail=detail), outbox)
    assert "blockquote" not in outbox.messages[0]


def pinned_state(*pins):
    """State with every board notice seen, plus older pinned notices.
    Each pin is (message ID, seen_at)."""
    entries = [
        {
            "url": f"https://www.aiub.edu/pinned-{mid}",
            "title": f"Pinned {mid}",
            "seen_at": at,
            "pinned": mid,
        }
        for mid, at in pins
    ]
    return [*entries, URL_1, URL_2, URL_3]


def pins_in(settings):
    return [e["pinned"] for e in saved_entries(settings) if isinstance(e, dict) and "pinned" in e]


def test_pinned_message_id_is_recorded(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    check(settings, Site(), Outbox())
    assert pins_in(settings) == [1]


def test_oldest_pins_beyond_the_limit_are_unpinned(settings):
    settings = replace(settings, max_pins=2)
    recent = "2026-10-11 10:00:00"
    settings.state_file.write_text(
        json.dumps(pinned_state((10, recent), (11, recent), (12, recent)))
    )
    outbox = Outbox()
    assert check(settings, Site(), outbox) == EXIT_OK
    assert outbox.unpins == [10]
    assert pins_in(settings) == [11, 12]


def test_pins_older_than_pin_days_are_unpinned_even_with_nothing_new(settings):
    settings.state_file.write_text(
        json.dumps(pinned_state((10, "2026-09-27 10:00:00"), (11, "2026-10-01 10:00:00")))
    )
    outbox = Outbox()
    check(settings, Site(), outbox)
    assert outbox.messages == []
    assert outbox.unpins == [10]  # 15 days old; 11 is 11 days old
    assert pins_in(settings) == [11]


def test_failed_unpin_is_retried_next_run(settings):
    settings.state_file.write_text(json.dumps(pinned_state((10, "2026-09-01 10:00:00"))))
    assert check(settings, Site(), Outbox(unpin_error="Too Many Requests")) == EXIT_OK
    assert pins_in(settings) == [10]


def test_pin_of_a_deleted_message_is_forgotten(settings):
    settings.state_file.write_text(json.dumps(pinned_state((10, "2026-09-01 10:00:00"))))
    check(settings, Site(), Outbox(unpin_error="Bad Request: message to unpin not found"))
    assert pins_in(settings) == []


def test_dry_run_reports_unpins_without_changing_state(settings, capsys):
    settings.state_file.write_text(json.dumps(pinned_state((10, "2026-09-01 10:00:00"))))
    check(settings, Site(), None, dry_run=True)
    assert "would unpin Pinned 10" in capsys.readouterr().out
    assert pins_in(settings) == [10]
