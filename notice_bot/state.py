from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class StateError(RuntimeError):
    """The state file exists but cannot be trusted."""


class SeenStore:
    """Ordered record of notice URLs that were already delivered.

    Stored as a JSON list (oldest first) so that git diffs of the file stay
    small: a run that sends one notice changes exactly one line.
    """

    def __init__(self, path: Path, max_entries: int = 500) -> None:
        self.path = path
        self.max_entries = max_entries
        self._urls: list[str] = []
        self._index: set[str] = set()

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def __contains__(self, url: str) -> bool:
        return url in self._index

    def __len__(self) -> int:
        return len(self._urls)

    def load(self) -> None:
        """Read the file. A corrupt file raises instead of reading as empty,
        which would make the bot re-send every notice on the board."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot read state file {self.path}: {exc}") from exc
        if not isinstance(data, list) or not all(isinstance(u, str) for u in data):
            raise StateError(f"{self.path} must contain a JSON list of URL strings")
        self._urls = list(dict.fromkeys(data))
        self._index = set(self._urls)

    def add(self, url: str) -> None:
        if url not in self._index:
            self._urls.append(url)
            self._index.add(url)

    def save(self) -> None:
        """Write atomically so an interrupted run can't leave a truncated file."""
        if len(self._urls) > self.max_entries:
            self._urls = self._urls[-self.max_entries :]
            self._index = set(self._urls)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self._urls, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
