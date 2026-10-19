"""Pure HTML -> data functions. No network access, so they are easy to test."""

from __future__ import annotations

import calendar
import logging
import posixpath
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from notice_bot.config import BASE_URL
from notice_bot.models import Attachment, Notice, NoticeDetail

log = logging.getLogger(__name__)

_MONTHS = "|".join(
    sorted({*calendar.month_name[1:], *calendar.month_abbr[1:]}, key=len, reverse=True)
)
_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})")
DATE_UNAVAILABLE = "Date unavailable"

# Page chrome that must never leak into a notice body.
_CHROME_SELECTOR = "nav, header, footer, script, style, .header, .footer, .navbar, .sidebar, .menu"
_BLOCK_TAGS = ["p", "table", "ul", "ol", "a"]
# A list shorter than this right after the notice is treated as navigation.
_MIN_LIST_LENGTH = 200
_MIN_BODY_LENGTH = 50

_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip"}
# Site logos and icons live here; notice files live under /Files/Uploads/.
_TEMPLATE_PATH = "/files/templates/"


def parse_notice_list(html: str, base_url: str = BASE_URL) -> list[Notice]:
    """Extract notices from the listing page, in page order (newest first)."""
    soup = BeautifulSoup(html, "html.parser")
    notices: list[Notice] = []
    seen_urls: set[str] = set()

    for card in soup.find_all("a", href=True):
        heading = card.find("h2")
        if heading is None:
            continue
        url = urljoin(base_url, str(card["href"]))
        if url in seen_urls:
            continue
        seen_urls.add(url)

        match = _DATE_RE.search(card.get_text(" ", strip=True))
        date = " ".join(match.groups()) if match else DATE_UNAVAILABLE
        notices.append(Notice(title=heading.get_text(strip=True), url=url, date=date))

    return notices


def parse_notice_detail(html: str, base_url: str = BASE_URL) -> NoticeDetail:
    """Extract the full title and a plain-text body from a notice page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select(_CHROME_SELECTOR):
        tag.decompose()

    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(" ", strip=True) if title_tag else None

    paragraphs: list[str] = []
    if title_tag is not None:
        captured: list[Tag] = []
        for element in title_tag.find_all_next(_BLOCK_TAGS):
            if element.name == "a":
                if element.find("h2"):  # start of the "more notices" cards
                    break
                continue
            text = _table_text(element) if element.name == "table" else _clean_text(element)
            if element.name in ("ul", "ol") and len(text) < _MIN_LIST_LENGTH:
                break
            # Skip anything nested in an element that was already captured.
            if not text or any(parent in captured for parent in element.parents):
                continue
            captured.append(element)
            paragraphs.append(text)

    body = "\n\n".join(paragraphs)
    if len(body) < _MIN_BODY_LENGTH:
        fallback = [_clean_text(p) for p in soup.find_all("p")]
        body = "\n\n".join([t for t in fallback if len(t) > 30][:5])

    attachments = _attachments(title_tag, base_url) if title_tag is not None else ()
    return NoticeDetail(title=title, body=body, attachments=attachments)


def _attachments(title_tag: Tag, base_url: str) -> tuple[Attachment, ...]:
    """Files linked (<a href>) or embedded (<img src>) after the notice title,
    up to the "more notices" cards."""
    found: dict[str, Attachment] = {}
    for element in title_tag.find_all_next(["a", "img"]):
        if element.name == "a" and element.find("h2"):  # start of the "more notices" cards
            break
        ref = element.get("href") if element.name == "a" else element.get("src")
        if not ref:
            continue
        url = urljoin(base_url, str(ref))
        path = unquote(urlparse(url).path)
        if _TEMPLATE_PATH in path.casefold() or url in found:
            continue
        filename = posixpath.basename(path)
        extension = posixpath.splitext(filename)[1].casefold()
        if extension in _PHOTO_EXTENSIONS:
            kind = "photo"
        elif extension in _DOCUMENT_EXTENSIONS:
            kind = "document"
        else:
            continue
        label = element.get_text(" ", strip=True) if element.name == "a" else ""
        if "click here" in label.casefold():  # says nothing as a caption
            label = ""
        found[url] = Attachment(url=url, filename=filename, kind=kind, label=label)
    return tuple(found.values())


def _clean_text(element: Tag) -> str:
    """Text of `element` with <br> kept as line breaks and whitespace collapsed."""
    for br in element.find_all("br"):
        br.replace_with("\n")
    lines = (" ".join(line.split()) for line in element.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def _table_text(table: Tag) -> str:
    """One line per row, cells separated by " | "."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [" ".join(_clean_text(c).split()) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)
