from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

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
    # When the bot sent (or, on a first run, recorded) the notice, in UTC.
    seen_at: str | None = None


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
            if all(v is None or isinstance(v, str) for v in fields.values()):
                return SeenEntry(url=item["url"], **fields)
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
