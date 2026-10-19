from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Notice:
    """A notice as listed on the notice board."""

    title: str
    url: str
    date: str


@dataclass(frozen=True)
class NoticeDetail:
    """What could be extracted from a notice's own page."""

    # Full title. The listing page truncates long titles with "...".
    title: str | None
    body: str
    # Files linked or embedded in the notice (PDFs, images), in page order.
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class Attachment:
    """A file that belongs to a notice, such as a PDF schedule or a scanned notice."""

    url: str
    filename: str
    # "photo" is shown inline in Telegram; anything else is sent as a document.
    kind: Literal["photo", "document"]
    # Link text from the page (e.g. "LL.B"), used as the caption. May be empty.
    label: str = ""
