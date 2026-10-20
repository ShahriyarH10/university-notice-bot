from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from notice_bot.config import LOCAL_TZ

log = logging.getLogger(__name__)


class StateError(RuntimeError):
    """The state file exists but cannot be trusted."""


@dataclass(frozen=True)
class SeenEntry:
    """A notice the bot has dealt with. Only `url` is required: entries
    written by older versions of the bot are bare URLs."""

    url: str
    title: str | None = None
    # The date shown on the notice board.
    date: str | None = None
    # Local date and time ("YYYY-MM-DD HH:MM:SS") when the bot sent, or on a
    # first run recorded, the notice.
    seen_at: str | None = None
    # Telegram message ID, while the bot keeps the notice pinned.
    pinned: int | None = None

    @property
    def seen_time(self) -> datetime | None:
        """`seen_at` as a datetime, or None if missing or malformed."""
        try:
            return datetime.strptime(self.seen_at or "", SEEN_AT_FORMAT).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            return None


SEEN_AT_FORMAT = "%Y-%m-%d %H:%M:%S"


class SeenStore:
    """Ordered record (oldest first) of notices that were already delivered.

    Stored as a JSON list of objects, appended to, so that a run that sends
    one notice adds one entry to the git diff. The old format, a list of bare
    URL strings, is still read and is upgraded the next time the file is saved.
    """

    def __init__(self, path: Path, max_entries: int = 500) -> None:
        self.path = path
        self.max_entries = max_entries
        self._entries: dict[str, SeenEntry] = {}

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def __contains__(self, url: str) -> bool:
        return url in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[SeenEntry]:
        """All entries, oldest first."""
        return list(self._entries.values())

    def pinned(self) -> list[SeenEntry]:
        """Entries the bot currently has pinned, oldest first."""
        return [e for e in self._entries.values() if e.pinned is not None]

    def set_pinned(self, url: str, message_id: int | None) -> None:
        """Record that `url`'s message is pinned (an ID) or no longer is (None)."""
        self._entries[url] = replace(self._entries[url], pinned=message_id)

    def load(self) -> None:
        """Read the file. A corrupt file raises instead of reading as empty,
        which would make the bot re-send every notice on the board."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot read state file {self.path}: {exc}") from exc
        if not isinstance(data, list):
            raise StateError(f"{self.path} must contain a JSON list")

        entries: dict[str, SeenEntry] = {}
        for item in data:
            entry = self._parse_entry(item)
            entries.setdefault(entry.url, entry)
        self._entries = entries

    def _parse_entry(self, item: object) -> SeenEntry:
        if isinstance(item, str):
            return SeenEntry(url=item)
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            fields = {k: item.get(k) for k in ("title", "date", "seen_at")}
            pinned = item.get("pinned")
            valid_pin = pinned is None or (isinstance(pinned, int) and not isinstance(pinned, bool))
            if valid_pin and all(v is None or isinstance(v, str) for v in fields.values()):
                return SeenEntry(url=item["url"], **fields, pinned=pinned)
        raise StateError(f"{self.path} has an invalid entry: {item!r}")

    def add(
        self,
        url: str,
        *,
        title: str | None = None,
        date: str | None = None,
        seen_at: str | None = None,
    ) -> None:
        if url not in self._entries:
            self._entries[url] = SeenEntry(url=url, title=title, date=date, seen_at=seen_at)

    def save(self) -> None:
        """Write atomically so an interrupted run can't leave a truncated file."""
        if len(self._entries) > self.max_entries:
            newest = list(self._entries.values())[-self.max_entries :]
            self._entries = {e.url: e for e in newest}
        payload = [
            {k: v for k, v in asdict(entry).items() if v is not None}
            for entry in self._entries.values()
        ]
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
