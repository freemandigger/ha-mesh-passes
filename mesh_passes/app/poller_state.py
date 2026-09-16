import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from app.events import PassEvent
from app.session_store import write_private_json

KEEP_DAYS = 3


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _format(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class PollerState:
    seen: dict[str, str] = field(default_factory=dict)
    last: dict[str, dict[str, str | None]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PollerState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(dict(raw.get("seen", {})), dict(raw.get("last", {})))
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        write_private_json(path, {"seen": self.seen, "last": self.last})

    def take_new(self, events: list[PassEvent], now: datetime, max_age: timedelta) -> list[PassEvent]:
        fresh = []
        for event in events:
            if event.id in self.seen:
                continue
            self.seen[event.id] = event.at.date().isoformat()
            if now - event.at <= max_age:
                fresh.append(event)
        return fresh

    def last_times(self, child_id: int) -> tuple[datetime | None, datetime | None]:
        item = self.last.get(str(child_id), {})
        return _parse(item.get("last_entry")), _parse(item.get("last_exit"))

    def set_last_times(self, child_id: int, last_entry: datetime | None, last_exit: datetime | None) -> None:
        self.last[str(child_id)] = {"last_entry": _format(last_entry), "last_exit": _format(last_exit)}

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.seen = {event_id: day for event_id, day in self.seen.items() if day >= cutoff}
