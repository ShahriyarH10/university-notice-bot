import json
from pathlib import Path

import pytest

from notice_bot.app import EXIT_FAILED, EXIT_OK, run
from notice_bot.config import Settings
from notice_bot.http import FetchError
from notice_bot.telegram import TelegramError

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "listing.html").read_text(encoding="utf-8")
DETAIL = (FIXTURES / "detail.html").read_text(encoding="utf-8")

NOW = "2026-10-12T10:14:33Z"
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


class Outbox:
    def __init__(self, fail_on=()):
        self.messages, self.fail_on = [], tuple(fail_on)

    def __call__(self, message):
        if any(marker in message.text for marker in self.fail_on):
            raise TelegramError("rejected")
        self.messages.append(message.text)


def saved_entries(settings):
    return json.loads(settings.state_file.read_text())


def saved(settings):
    return [e if isinstance(e, str) else e["url"] for e in saved_entries(settings)]


def test_first_run_records_board_without_sending(settings):
    outbox = Outbox()
    assert run(settings, fetch=Site(), send=outbox) == EXIT_OK
    assert outbox.messages == []
    assert saved(settings) == [URL_1, URL_2, URL_3]


def test_sends_only_unseen_notices_oldest_first(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox()
    assert run(settings, fetch=Site(), send=outbox) == EXIT_OK
    assert len(outbox.messages) == 2
    assert "Slot_1" in outbox.messages[0]  # second card is older than the first
    assert "General Notice" in outbox.messages[1]
    assert saved(settings) == [URL_3, URL_2, URL_1]


def test_truncated_listing_title_is_replaced_by_full_title(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    run(settings, fetch=Site(), send=outbox)
    assert "(regarding usage of web groups/ blogs/ social media)" in outbox.messages[0]


def test_nothing_new_sends_nothing(settings):
    settings.state_file.write_text(json.dumps([URL_1, URL_2, URL_3]))
    site, outbox = Site(), Outbox()
    assert run(settings, fetch=site, send=outbox) == EXIT_OK
    assert outbox.messages == []
    assert site.requested == ["https://board"]  # no detail pages fetched


def test_failed_delivery_is_retried_next_run_and_fails_the_run(settings):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox(fail_on=["Slot_1"])
    assert run(settings, fetch=Site(), send=outbox) == EXIT_FAILED
    assert len(outbox.messages) == 1  # the other notice still went out
    assert saved(settings) == [URL_3, URL_1]

    assert run(settings, fetch=Site(), send=(retry := Outbox())) == EXIT_OK
    assert len(retry.messages) == 1
    assert "Slot_1" in retry.messages[0]


def test_unreachable_notice_page_still_sends_without_body(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    outbox = Outbox()
    assert run(settings, fetch=Site(broken={URL_1}), send=outbox) == EXIT_OK
    assert len(outbox.messages) == 1
    assert "General Notice" in outbox.messages[0]
    assert "blockquote" not in outbox.messages[0]


def test_unreachable_board_fails_the_run(settings):
    outbox = Outbox()
    assert run(settings, fetch=Site(broken={"https://board"}), send=outbox) == EXIT_FAILED
    assert outbox.messages == []
    assert not settings.state_file.exists()


def test_board_without_notices_fails_the_run(settings):
    site = Site(listing="<html><body>Cloudflare challenge</body></html>")
    assert run(settings, fetch=site, send=Outbox()) == EXIT_FAILED


def test_dry_run_prints_and_leaves_state_untouched(settings, capsys):
    settings.state_file.write_text(json.dumps([URL_3]))
    outbox = Outbox()
    assert run(settings, fetch=Site(), send=outbox, dry_run=True) == EXIT_OK
    assert outbox.messages == []
    assert saved(settings) == [URL_3]
    assert capsys.readouterr().out.count("would send") == 2


def test_dry_run_first_run_does_not_create_state(settings):
    run(settings, fetch=Site(), send=Outbox(), dry_run=True)
    assert not settings.state_file.exists()


def test_state_records_title_board_date_and_time_seen(settings):
    settings.state_file.write_text(json.dumps([URL_2, URL_3]))
    run(settings, fetch=Site(), send=Outbox())
    # The full title from the notice page is stored, not the truncated one.
    assert saved_entries(settings)[-1] == {
        "url": URL_1,
        "title": "General Notice (regarding usage of web groups/ blogs/ social media)",
        "date": "12 Oct 2026",
        "seen_at": NOW,
    }


def test_first_run_records_details_too(settings):
    run(settings, fetch=Site(), send=Outbox())
    assert saved_entries(settings)[1] == {
        "url": URL_2,
        "title": "Result & Viva: *Slot_1*",
        "date": "5 September 2026",
        "seen_at": NOW,
    }
