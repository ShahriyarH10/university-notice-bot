from __future__ import annotations

from dataclasses import dataclass


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
