from pathlib import Path

from notice_bot.models import Attachment, Notice
from notice_bot.parser import DATE_UNAVAILABLE, parse_notice_detail, parse_notice_list

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseNoticeList:
    def test_extracts_notices_in_page_order(self):
        notices = parse_notice_list(read("listing.html"))
        assert [n.title for n in notices] == [
            "General Notice (regarding usage of web groups/ ...",
            "Result & Viva: *Slot_1*",
            "Notice without a date",
        ]

    def test_relative_links_are_made_absolute(self):
        notices = parse_notice_list(read("listing.html"))
        assert notices[0].url == "https://www.aiub.edu/general-notice-regarding-web-groups"
        assert notices[1].url == "https://www.aiub.edu/admission-result_[slot-1]"

    def test_date_is_parsed_from_card_text(self):
        notices = parse_notice_list(read("listing.html"))
        assert notices[0].date == "12 Oct 2026"
        assert notices[1].date == "5 September 2026"

    def test_missing_date_has_placeholder(self):
        assert parse_notice_list(read("listing.html"))[2].date == DATE_UNAVAILABLE

    def test_duplicate_links_are_collapsed(self):
        urls = [n.url for n in parse_notice_list(read("listing.html"))]
        assert len(urls) == len(set(urls))

    def test_page_without_cards_returns_empty_list(self):
        assert parse_notice_list("<html><body><p>Maintenance</p></body></html>") == []

    def test_returns_notice_instances(self):
        assert all(isinstance(n, Notice) for n in parse_notice_list(read("listing.html")))


class TestParseNoticeDetail:
    def test_title_comes_from_the_notice_heading_not_the_site_logo(self):
        detail = parse_notice_detail(read("detail.html"))
        assert detail.title == "General Notice (regarding usage of web groups/ blogs/ social media)"

    def test_body_collapses_whitespace_and_keeps_line_breaks(self):
        body = parse_notice_detail(read("detail.html")).body
        assert body.startswith(
            "It has been observed that AIUB students create groups\nin numerous web blogs."
        )

    def test_empty_paragraphs_are_skipped(self):
        body = parse_notice_detail(read("detail.html")).body
        assert "\n\n\n" not in body

    def test_nested_elements_are_not_duplicated(self):
        body = parse_notice_detail(read("detail.html")).body
        assert body.count("Nested paragraph") == 1
        assert "Cell two" in body

    def test_table_rows_become_pipe_separated_lines(self):
        html = (
            "<h1>Load</h1><p>Minimum credits after dropping, as per the table.</p>"
            "<table><tr><th>Program</th><th>Credits</th></tr>"
            "<tr><td>Undergraduate</td><td>12</td></tr></table>"
        )
        body = parse_notice_detail(html).body
        assert body.endswith("Program | Credits\nUndergraduate | 12")

    def test_body_stops_at_related_notice_cards(self):
        body = parse_notice_detail(read("detail.html")).body
        assert "more notices" not in body
        assert "Copyright" not in body

    def test_short_body_falls_back_to_long_paragraphs(self):
        body = parse_notice_detail(read("detail_short.html")).body
        assert body == "This is a reasonably long paragraph that qualifies for the fallback path."

    def test_page_without_content_gives_empty_body(self):
        detail = parse_notice_detail("<html><body></body></html>")
        assert detail.title is None
        assert detail.body == ""


class TestAttachments:
    def test_files_linked_or_embedded_in_the_notice_are_found_in_page_order(self):
        attachments = parse_notice_detail(read("detail_attachments.html")).attachments
        assert [(a.kind, a.filename) for a in attachments] == [
            ("document", "ll.-b.-1.pdf"),
            ("document", "b.-pharm-1.pdf"),
            ("document", "exam-schedule-[ug-&-pg).pdf"),
            ("photo", "1(2).webp"),
        ]

    def test_links_are_absolute_and_the_link_text_is_the_label(self):
        first = parse_notice_detail(read("detail_attachments.html")).attachments[0]
        assert first == Attachment(
            url="https://www.aiub.edu/Files/Uploads/ll.-b.-1.pdf",
            filename="ll.-b.-1.pdf",
            kind="document",
            label="LL.B",
        )

    def test_click_here_link_text_is_not_used_as_a_label(self):
        attachments = parse_notice_detail(read("detail_attachments.html")).attachments
        assert attachments[2].label == ""

    def test_site_logo_pages_and_related_notices_are_ignored(self):
        urls = [a.url for a in parse_notice_detail(read("detail_attachments.html")).attachments]
        assert not any("Templates" in u or "about" in u or "unrelated" in u for u in urls)

    def test_notice_without_files_has_none(self):
        assert parse_notice_detail(read("detail.html")).attachments == ()
