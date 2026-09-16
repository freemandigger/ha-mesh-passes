from datetime import date, datetime, timedelta

from app.events import MSK, PassEvent
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
