from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path


class SyncState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict = {"days": []}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {"days": []}
        if not isinstance(self._data.get("days"), list):
            self._data["days"] = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_synced(self, day: date) -> bool:
        return any(record.get("date") == day.isoformat() for record in self._data["days"])

    def synced_event_count(self, day: date) -> int | None:
        for record in self._data["days"]:
            if record.get("date") == day.isoformat():
                return record.get("events")
        return None

    def mark_synced(self, day: date, post_ids: set[str], event_count: int) -> None:
        remaining = [record for record in self._data["days"] if record.get("date") != day.isoformat()]
        remaining.append(
            {
                "date": day.isoformat(),
                "post_ids": sorted(post_ids),
                "events": event_count,
                "synced_at": datetime.now(UTC).isoformat(),
            }
        )
        remaining.sort(key=lambda record: record["date"])
        self._data["days"] = remaining
        self.save()
