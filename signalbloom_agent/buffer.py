"""Readings waiting to be sent, kept in a file so they survive a restart."""

import json
import math
import os
import tempfile
from pathlib import Path

FILE_NAME = "buffer.jsonl"
KEEP_SECONDS = 24 * 60 * 60


def cap_for(interval_seconds: int) -> int:
    """How many readings make up 24 hours at this interval."""
    return math.ceil(KEEP_SECONDS / interval_seconds)


class ReadingBuffer:
    """Oldest-first queue of readings, saved as one JSON object per line.

    Every change rewrites the file through a temp file and os.replace, so a
    crash leaves either the old file or the new one, never half of one.
    """

    def __init__(self, data_dir: str | Path, max_readings: int):
        self.path = Path(data_dir) / FILE_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._max = max_readings
        self._items: list[dict] = []
        self.bad_lines = 0  # lines in the file that couldn't be read
        self._load()

    def __len__(self) -> int:
        return len(self._items)

    def add(self, reading: dict) -> int:
        """Add a reading. Returns how many of the oldest were dropped to make room."""
        self._items.append(reading)
        dropped = max(0, len(self._items) - self._max)
        del self._items[:dropped]
        self._save()
        return dropped

    def oldest(self, n: int) -> list[dict]:
        return [dict(r) for r in self._items[:n]]

    def remove_oldest(self, n: int) -> None:
        if n <= 0:
            return
        del self._items[:n]
        self._save()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                self.bad_lines += 1
                continue
            if isinstance(item, dict) and isinstance(item.get("measured_at"), str):
                self._items.append(item)
            else:
                self.bad_lines += 1
        # A smaller cap than last time (e.g. a new interval) keeps the newest.
        del self._items[:max(0, len(self._items) - self._max)]

    def _save(self) -> None:
        text = "".join(json.dumps(r) + "\n" for r in self._items)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".buffer-", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
