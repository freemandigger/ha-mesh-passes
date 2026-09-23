from datetime import date

import pytest

from app.marks import MarkChange, change_from_dict, change_to_dict, diff_marks, marks_from, merge_held
from app.mesh import Mark

TODAY = date(2026, 9, 23)


def mark(mark_id, value, day=TODAY, subject="Математика"):
    return Mark(mark_id, day, subject, value, 1, "Домашнее задание", False)


def test_marks_from_covers_thirty_days():
    assert marks_from(TODAY) == date(2026, 8, 25)


def test_first_check_is_silent():
    changes, known = diff_marks(None, [mark(1, "5"), mark(2, "НВ")], TODAY)
    assert changes == []
    assert known == {"1": ["5", "2026-09-23"], "2": ["НВ", "2026-09-23"]}


def test_new_changed_and_unchanged():
    known = {"1": ["5", "2026-09-23"], "2": ["НВ", "2026-09-22"]}
    fixed = mark(2, "4", date(2026, 9, 22))
    english = mark(3, "3", subject="Английский язык")

    changes, updated = diff_marks(known, [mark(1, "5"), fixed, english], TODAY)

    assert changes == [MarkChange("changed", fixed, "НВ"), MarkChange("new", english, None)]
    assert updated == {"1": ["5", "2026-09-23"], "2": ["4", "2026-09-22"], "3": ["3", "2026-09-23"]}


def test_empty_response_keeps_known():
    known = {"1": ["5", "2026-09-23"]}

    changes, updated = diff_marks(known, [], TODAY)

    assert (changes, updated) == ([], known)
    assert diff_marks(updated, [mark(1, "5")], TODAY)[0] == []


def test_known_older_than_window_is_pruned():
    known = {"1": ["5", "2026-08-24"], "2": ["4", "2026-08-25"]}
    assert set(diff_marks(known, [], TODAY)[1]) == {"2"}


def test_merge_held_collapses_changes():
    first = merge_held([], [MarkChange("new", mark(1, "НВ"), None), MarkChange("changed", mark(2, "4"), "НВ")])
    second = merge_held(first, [MarkChange("changed", mark(1, "4"), "НВ"), MarkChange("changed", mark(2, "5"), "4")])

    assert second == [MarkChange("new", mark(1, "4"), None), MarkChange("changed", mark(2, "5"), "НВ")]
    assert merge_held(second, [MarkChange("changed", mark(2, "НВ"), "5")]) == [MarkChange("new", mark(1, "4"), None)]


def test_merge_held_orders_by_lesson_date_then_subject():
    late = MarkChange("new", mark(1, "3", date(2026, 9, 16)), None)
    russian = MarkChange("new", mark(2, "5", subject="Русский язык"), None)
    english = MarkChange("new", mark(3, "4", subject="Английский язык"), None)

    assert merge_held([russian], [english, late]) == [late, english, russian]


def test_change_round_trip():
    change = MarkChange("changed", Mark(7, date(2026, 9, 16), "Математика", "3", 2, "Контрольная работа", True), "НВ")

    data = change_to_dict(change)

    assert data == {
        "kind": "changed",
        "id": 7,
        "subject": "Математика",
        "value": "3",
        "previous": "НВ",
        "date": "2026-09-16",
        "control_form": "Контрольная работа",
        "weight": 2,
        "is_exam": True,
    }
    assert change_from_dict(data) == change


def test_change_from_dict_rejects_unknown_kind():
    data = {**change_to_dict(MarkChange("new", mark(1, "5"), None)), "kind": "deleted"}
    with pytest.raises(ValueError):
        change_from_dict(data)
