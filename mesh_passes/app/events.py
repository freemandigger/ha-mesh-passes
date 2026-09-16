from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from app.mesh import Visit

MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class PassEvent:
    id: str
    child_id: int
    kind: Literal["entry", "exit"]
    at: datetime
    school: str | None
    person: str | None


@dataclass(frozen=True)
class ChildState:
    at_school: bool
    last_entry: datetime | None
    last_exit: datetime | None
    school: str | None
    visits: list[dict[str, str]]


def _at(day: date, hhmm: str) -> datetime | None:
    try:
        hours, minutes = hhmm.split(":")
        return datetime.combine(day, time(int(hours), int(minutes)), MSK)
    except ValueError:
        return None


def _last_activity(visit: Visit) -> str:
    return max(value for value in (visit.time_in, visit.time_out, "") if value != "-")


def visit_events(child_id: int, visits: list[Visit]) -> list[PassEvent]:
    events = []
    for visit in visits:
        prefix = f"{visit.day.isoformat()}|{visit.organization_id if visit.organization_id is not None else '-'}"
        entry_at = _at(visit.day, visit.time_in)
        if entry_at:
            events.append(
                PassEvent(f"{prefix}|in|{visit.time_in}", child_id, "entry", entry_at, visit.school, visit.person_in)
            )
        exit_at = _at(visit.day, visit.time_out)
        if exit_at and not visit.incomplete:
            events.append(
                PassEvent(f"{prefix}|out|{visit.time_out}", child_id, "exit", exit_at, visit.school, visit.person_out)
            )
    return sorted(events, key=lambda event: event.at)


def child_state(visits: list[Visit], last_entry: datetime | None, last_exit: datetime | None) -> ChildState:
    events = visit_events(0, visits)
    ordered = sorted(visits, key=_last_activity)
    current = ordered[-1] if ordered else None
    return ChildState(
        at_school=bool(current and (current.incomplete or _at(current.day, current.time_out) is None)),
        last_entry=max((e.at for e in events if e.kind == "entry"), default=last_entry),
        last_exit=max((e.at for e in events if e.kind == "exit"), default=last_exit),
        school=next((v.school for v in reversed(ordered) if v.school), None),
        visits=[{"in": v.time_in, "out": v.time_out} for v in ordered],
    )
