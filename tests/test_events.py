from datetime import date, datetime

from app.events import MSK, ChildState, child_state, visit_events
from app.mesh import Visit


def visit(time_in="08:07", time_out="14:33", incomplete=False, org=1234):
    return Visit(date(2026, 9, 16), time_in, time_out, incomplete, org, "ГБОУ Школа № 1", "Мама", "Папа")


def at(hours, minutes):
    return datetime(2026, 9, 16, hours, minutes, tzinfo=MSK)


def test_complete_visit_gives_entry_and_exit():
    events = visit_events(101, [visit()])
    assert [(e.id, e.kind, e.at, e.person) for e in events] == [
        ("2026-09-16|1234|in|08:07", "entry", at(8, 7), "Мама"),
        ("2026-09-16|1234|out|14:33", "exit", at(14, 33), "Папа"),
    ]
    assert {e.child_id for e in events} == {101}


def test_child_still_inside_has_no_exit():
    assert [e.kind for e in visit_events(101, [visit(time_out="-")])] == ["entry"]
    assert [e.kind for e in visit_events(101, [visit(incomplete=True)])] == ["entry"]


def test_exit_without_entry():
    assert [e.kind for e in visit_events(101, [visit(time_in="-")])] == ["exit"]


def test_unknown_organization():
    assert visit_events(101, [visit(org=None)])[0].id == "2026-09-16|-|in|08:07"


def test_two_visits_are_ordered_by_time():
    events = visit_events(101, [visit("12:00", "15:00"), visit("08:00", "11:00")])
    assert [e.id.split("|", 2)[2] for e in events] == ["in|08:00", "out|11:00", "in|12:00", "out|15:00"]


def test_state_while_at_school():
    state = child_state([visit("12:00", "-"), visit("08:00", "11:00")], None, None)
    assert state.at_school is True
    assert state.last_entry == at(12, 0)
    assert state.last_exit == at(11, 0)
    assert state.school == "ГБОУ Школа № 1"
    assert state.visits == [{"in": "08:00", "out": "11:00"}, {"in": "12:00", "out": "-"}]


def test_state_after_exit_without_entry():
    assert child_state([visit("08:00", "-"), visit("-", "14:00")], None, None).at_school is False


def test_state_without_visits_keeps_previous_times():
    entry, leave = datetime(2026, 9, 15, 8, 0, tzinfo=MSK), datetime(2026, 9, 15, 14, 0, tzinfo=MSK)
    assert child_state([], entry, leave) == ChildState(False, entry, leave, None, [])
