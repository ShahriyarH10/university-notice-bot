import json

import pytest
import requests

from notice_bot.models import Notice
from notice_bot.parser import DATE_UNAVAILABLE
from notice_bot.telegram import (
    MESSAGE_LIMIT,
    OutgoingMessage,
    TelegramClient,
    TelegramError,
    format_message,
    shorten,
)

NOTICE = Notice(
    title="Result & Viva: *Slot_1* [x]", url="https://www.aiub.edu/a?b=1&c=2", date="12 Oct 2026"
)


class TestShorten:
    def test_short_text_is_unchanged(self):
        assert shorten("hello", 10) == "hello"

    def test_cuts_at_paragraph_boundary(self):
        text = "first paragraph here\n\nsecond paragraph that is long"
        assert shorten(text, 30) == "first paragraph here…"

    def test_cuts_at_word_boundary(self):
        assert shorten("alpha beta gamma delta", 14) == "alpha beta…"

    def test_hard_cut_without_boundaries(self):
        assert shorten("x" * 50, 10) == "x" * 9 + "…"

    def test_never_exceeds_limit(self):
        assert len(shorten("word " * 100, 40)) <= 40


class TestFormatMessage:
    def test_layout_has_category_date_bold_title_and_collapsible_excerpt(self):
        notice = Notice(
            title="Admission Test Final Result", url="https://www.aiub.edu/a", date="12 Oct 2026"
        )
        message = format_message(notice, "Details here")
        assert message.text == (
            "🎓 <b>Admission</b>  ·  📅 12 Oct 2026\n"
            "\n"
            "<b>Admission Test Final Result</b>\n"
            "\n"
            "<blockquote expandable>Details here</blockquote>"
        )
        assert message.button_url == "https://www.aiub.edu/a"

    def test_escapes_html_in_every_field(self):
        message = format_message(NOTICE, "1 < 2 & 3 > 2")
        assert "Result &amp; Viva: *Slot_1* [x]" in message.text
        assert "1 &lt; 2 &amp; 3 &gt; 2" in message.text

    def test_unknown_date_is_left_out(self):
        notice = Notice(title="Plain", url="https://x", date=DATE_UNAVAILABLE)
        assert "📅" not in format_message(notice, "").text

    def test_body_is_optional(self):
        assert "blockquote" not in format_message(NOTICE, "").text

    def test_unsafe_url_characters_are_encoded_for_the_button(self):
        notice = Notice(title="t", url="https://www.aiub.edu/a b_[slot-1]?x=1&y=2", date="d")
        assert format_message(notice, "").button_url == (
            "https://www.aiub.edu/a%20b_%5Bslot-1%5D?x=1&y=2"
        )

    def test_long_body_is_shortened(self):
        message = format_message(NOTICE, "word " * 2000, max_body_length=600)
        assert len(message.text) < MESSAGE_LIMIT
        assert "…</blockquote>" in message.text

    def test_oversized_message_is_rejected(self):
        huge = Notice(title="t" * 5000, url="https://x", date="d")
        with pytest.raises(TelegramError):
            format_message(huge, "")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, *, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._json_error:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data, timeout):
        self.calls.append((url, data))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


HI = OutgoingMessage(text="hi", button_url="https://x")


def client(session, **kwargs):
    return TelegramClient("SECRET-TOKEN", "42", session=session, **kwargs)


class TestSendMessage:
    def test_posts_html_message(self):
        session = FakeSession(FakeResponse(200, {"ok": True}))
        client(session).send_message(HI)
        url, data = session.calls[0]
        assert url == "https://api.telegram.org/botSECRET-TOKEN/sendMessage"
        assert data["chat_id"] == "42"
        assert data["text"] == "hi"
        assert data["parse_mode"] == "HTML"
        assert json.loads(data["link_preview_options"]) == {"is_disabled": True}
        assert json.loads(data["reply_markup"]) == {
            "inline_keyboard": [[{"text": "🔗 Read full notice", "url": "https://x"}]]
        }

    def test_api_rejection_raises_with_description(self):
        session = FakeSession(FakeResponse(400, {"ok": False, "description": "Bad Request: nope"}))
        with pytest.raises(TelegramError, match="nope"):
            client(session).send_message(HI)

    def test_non_json_error_response_raises(self):
        session = FakeSession(FakeResponse(502, json_error=True))
        with pytest.raises(TelegramError, match="HTTP 502"):
            client(session).send_message(HI)

    def test_rate_limit_is_retried(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr("notice_bot.telegram.time.sleep", sleeps.append)
        session = FakeSession(
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}),
            FakeResponse(200, {"ok": True}),
        )
        client(session).send_message(HI)
        assert sleeps == [3]
        assert len(session.calls) == 2

    def test_rate_limit_gives_up_after_max_attempts(self, monkeypatch):
        monkeypatch.setattr("notice_bot.telegram.time.sleep", lambda _: None)
        limited = {
            "ok": False,
            "description": "Too Many Requests",
            "parameters": {"retry_after": 1},
        }
        session = FakeSession(*(FakeResponse(429, limited) for _ in range(3)))
        with pytest.raises(TelegramError, match="Too Many Requests"):
            client(session, max_attempts=3).send_message(HI)

    def test_token_is_redacted_from_network_errors(self):
        error = requests.ConnectionError(
            "failed for https://api.telegram.org/botSECRET-TOKEN/sendMessage"
        )
        with pytest.raises(TelegramError) as info:
            client(FakeSession(error)).send_message(HI)
        assert "SECRET-TOKEN" not in str(info.value)
        assert "***" in str(info.value)
