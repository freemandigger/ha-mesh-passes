from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.mesh import Mark

MARKS_DAYS = 30

Known = dict[str, list[str]]


@dataclass(frozen=True)
class MarkChange:
    kind: Literal["new", "changed"]
    mark: Mark
    previous: str | None


def marks_from(today: date) -> date:
    return today - timedelta(days=MARKS_DAYS - 1)


def _order(change: MarkChange) -> tuple[date, str, int]:
    return change.mark.day, change.mark.subject, change.mark.id


def diff_marks(known: Known | None, marks: list[Mark], today: date) -> tuple[list[MarkChange], Known]:
    result = dict(known or {})
    changes = []
    for mark in marks:
        key = str(mark.id)
        old = result.get(key)
        result[key] = [mark.value, mark.day.isoformat()]
        if known is None or (old is not None and old[0] == mark.value):
            continue
        changes.append(MarkChange("new", mark, None) if old is None else MarkChange("changed", mark, old[0]))
    cutoff = marks_from(today).isoformat()
    return sorted(changes, key=_order), {key: item for key, item in result.items() if item[1] >= cutoff}


def merge_held(held: list[MarkChange], changes: list[MarkChange]) -> list[MarkChange]:
    merged = {change.mark.id: change for change in held}
    for change in changes:
        earlier = merged.get(change.mark.id)
        if earlier is None:
            merged[change.mark.id] = change
        elif earlier.kind == "new":
            merged[change.mark.id] = MarkChange("new", change.mark, None)
        elif earlier.previous == change.mark.value:
            del merged[change.mark.id]
        else:
            merged[change.mark.id] = MarkChange("changed", change.mark, earlier.previous)
    return sorted(merged.values(), key=_order)


def change_fields(change: MarkChange) -> dict[str, object]:
    mark = change.mark
    return {
        "subject": mark.subject,
        "value": mark.value,
        "previous": change.previous,
        "date": mark.day.isoformat(),
        "control_form": mark.control_form,
        "weight": mark.weight,
        "is_exam": mark.is_exam,
    }


def change_to_dict(change: MarkChange) -> dict[str, object]:
    return {"kind": change.kind, "id": change.mark.id, **change_fields(change)}


def change_from_dict(data: dict) -> MarkChange:
    kind = data["kind"]
    if kind not in ("new", "changed"):
        raise ValueError(f"неизвестный вид изменения: {kind!r}")
    mark = Mark(
        id=int(data["id"]),
        day=date.fromisoformat(data["date"]),
        subject=str(data["subject"]),
        value=str(data["value"]),
        weight=data.get("weight"),
        control_form=data.get("control_form"),
        is_exam=bool(data.get("is_exam")),
    )
    return MarkChange(kind, mark, data.get("previous"))
