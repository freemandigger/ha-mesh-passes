from datetime import date, datetime, timedelta

from app.events import MSK, PassEvent
from app.marks import MarkChange
from app.mesh import Mark
from app.poller_state import PollerState

NOW = datetime(2026, 9, 16, 14, 35, tzinfo=MSK)


def event(event_id, at):
    return PassEvent(event_id, 101, "exit", at, "ГБОУ Школа № 1", None)


def test_take_new_marks_seen_and_skips_old():
    state = PollerState()
    fresh, old = event("fresh", NOW - timedelta(minutes=2)), event("old", NOW - timedelta(hours=2))

    assert state.take_new([fresh, old], NOW, timedelta(minutes=30)) == [fresh]
    assert state.take_new([fresh, old], NOW, timedelta(minutes=30)) == []
    assert set(state.seen) == {"fresh", "old"}


def test_prune_keeps_three_days():
    state = PollerState(seen={"old": "2026-09-12", "edge": "2026-09-13", "new": "2026-09-16"})
    state.prune(date(2026, 9, 16))
    assert set(state.seen) == {"edge", "new"}


def test_save_and_load(tmp_path):
    path = tmp_path / "state.json"
    entry = datetime(2026, 9, 16, 8, 7, tzinfo=MSK)
    state = PollerState()
    state.seen["x"] = "2026-09-16"
    state.set_last_times(101, entry, None)
    state.save(path)

    loaded = PollerState.load(path)
    assert loaded.seen == {"x": "2026-09-16"}
    assert loaded.last_times(101) == (entry, None)
    assert loaded.last_times(999) == (None, None)
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_broken_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    assert PollerState.load(path) == PollerState()


FIXED = MarkChange(
    "changed", Mark(7, date(2026, 9, 16), "Математика", "4", 1, "Цифровое домашнее задание", False), "НВ"
)


def test_marks_and_held_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = PollerState()
    state.set_known_marks(101, {"7": ["4", "2026-09-16"]})
    state.set_held(101, [FIXED])
    state.save(path)

    loaded = PollerState.load(path)

    assert loaded.known_marks(101) == {"7": ["4", "2026-09-16"]}
    assert loaded.known_marks(102) is None
    assert loaded.held_changes(101) == [FIXED]
    assert loaded.held_changes(102) == []
    loaded.set_held(101, [])
    assert loaded.held == {}


def test_state_without_marks_loads(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"seen": {"x": "2026-09-16"}, "last": {}}', encoding="utf-8")

    state = PollerState.load(path)

    assert state.seen == {"x": "2026-09-16"}
    assert state.known_marks(101) is None
    assert state.held_changes(101) == []


def test_broken_held_entries_are_skipped():
    good = {
        "kind": "new",
        "id": 1,
        "date": "2026-09-16",
        "subject": "Математика",
        "value": "5",
        "previous": None,
        "control_form": None,
        "weight": 1,
        "is_exam": False,
    }
    state = PollerState(held={"101": [{"kind": "new"}, good, "junk"]})

    assert [change.mark.id for change in state.held_changes(101)] == [1]


def test_clear_marks():
    state = PollerState(marks={"101": {}}, held={"101": []})
    state.clear_marks()
    assert (state.marks, state.held) == ({}, {})
