import asyncio
import contextlib
import logging
import time
from datetime import datetime

import pytest

from app.auth import LoginState
from app.events import MSK
from app.mesh import MeshClient
from app.poller import Poller
from app.poller_state import PollerState
from app.settings import build_settings
from fake_mos import make_mark
from helpers import wait_for

SETTINGS = build_settings({}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})


class FakePublisher:
    def __init__(self):
        self.calls = []
        self.exit_marks = []

    async def publish_account(self, profile_id, status, last_poll, token_expires):
        self.calls.append(("account", status))

    async def publish_children(self, profile_id, children):
        self.calls.append(("children", [child.id for child in children]))

    async def publish_child_state(self, profile_id, child, state):
        self.calls.append(("state", child.id, state.at_school))

    async def publish_event(self, profile_id, child, event, marks=None):
        self.calls.append(("event", child.id, event.kind, event.id))
        if marks is not None:
            self.exit_marks.append(
                [(change.kind, change.mark.id, change.previous, change.mark.value) for change in marks]
            )

    async def publish_mark(self, profile_id, child, change):
        self.calls.append(("mark", child.id, change.kind, change.mark.id, change.previous, change.mark.value))

    def of(self, kind):
        return [call for call in self.calls if call[0] == kind]


class FlakyPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self._fail_next_account = True

    async def publish_account(self, profile_id, status, last_poll, token_expires):
        if self._fail_next_account:
            self._fail_next_account = False
            raise RuntimeError("broker gone")
        await super().publish_account(profile_id, status, last_poll, token_expires)


class Clock:
    def __init__(self, hours, minutes):
        self.set(hours, minutes)

    def set(self, hours, minutes, day=16):
        self.now = datetime(2026, 9, day, hours, minutes, tzinfo=MSK)

    def __call__(self):
        return self.now


def day(time_in, time_out):
    return [
        {
            "date": "2026-09-16",
            "visits": [
                {
                    "in": time_in,
                    "out": time_out,
                    "isIncomplete": time_out == "-",
                    "organizationId": 1234,
                    "organizationShortName": "ГБОУ Школа № 1",
                }
            ],
        }
    ]


def marks_requests(fake_mos):
    return [request for request in fake_mos.requests if request.path == "/api/family/mobile/v1/marks"]


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def clock():
    return Clock(14, 35)


@pytest.fixture
def make_poller(fake_mos, logged_in_auth, api_http, tmp_path, publisher, clock):
    def make(settings=SETTINGS):
        return Poller(
            logged_in_auth, MeshClient(api_http, fake_mos.base_url), publisher, settings, tmp_path / "state.json", clock
        )

    return make


async def test_exit_event_published_once(fake_mos, make_poller, publisher):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    poller = make_poller()

    assert await poller.poll_once() == 180
    assert await poller.poll_once() == 180

    assert publisher.of("event") == [("event", 101, "exit", "2026-09-16|1234|out|14:33")]
    assert publisher.of("children") == [("children", [101])]
    assert publisher.of("state") == [("state", 101, False), ("state", 101, False)]
    assert publisher.of("account")[-1] == ("account", "ok")
    assert poller.status == "ok"
    assert poller.child_states[101].last_exit == datetime(2026, 9, 16, 14, 33, tzinfo=MSK)


async def test_restart_does_not_repeat_events(fake_mos, make_poller, publisher):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    await make_poller().poll_once()

    await make_poller().poll_once()

    assert len(publisher.of("event")) == 1


async def test_old_passes_update_state_without_events(fake_mos, make_poller, publisher, clock):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    clock.set(18, 0)

    await make_poller().poll_once()

    assert publisher.of("event") == []
    assert publisher.of("state") == [("state", 101, False)]


async def test_entry_then_exit_across_polls(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(8, 2)
    await poller.poll_once()
    fake_mos.visits["guid-101"] = day("08:00", "13:00")
    clock.set(13, 3)
    await poller.poll_once()

    assert [call[2] for call in publisher.of("event")] == ["entry", "exit"]
    assert [call[2] for call in publisher.of("state")] == [True, False]


async def test_api_errors_back_off(fake_mos, make_poller, publisher):
    poller = make_poller()
    fake_mos.api_status = 503

    delays = [await poller.poll_once() for _ in range(5)]

    assert delays == [180, 360, 720, 900, 900]
    assert poller.status == "api_error"
    fake_mos.api_status = 200
    assert await poller.poll_once() == 180
    assert poller.status == "ok"


async def test_retry_after_is_respected(fake_mos, make_poller):
    fake_mos.api_status = 429
    fake_mos.api_headers = {"Retry-After": "1200"}

    assert await make_poller().poll_once() == 1200


async def test_revoked_token_is_renewed_and_retried(fake_mos, make_poller, logged_in_auth):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    fake_mos.revoked.add(await logged_in_auth.token())
    poller = make_poller()

    await poller.poll_once()

    assert poller.status == "ok"
    assert len(fake_mos.issued_tokens) == 2


async def test_unrecoverable_auth_requires_login(fake_mos, make_poller, logged_in_auth, publisher):
    fake_mos.revoked.add(await logged_in_auth.token())
    fake_mos.sso_alive = False
    poller = make_poller()

    await poller.poll_once()

    assert poller.status == "auth_required"
    assert logged_in_auth.state is LoginState.AUTH_REQUIRED
    assert publisher.of("account")[-1] == ("account", "auth_required")


async def test_unreachable_renewal_is_api_error(fake_mos, make_poller, logged_in_auth, publisher):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    fake_mos.revoked.add(await logged_in_auth.token())
    fake_mos.renewal_status = 503
    poller = make_poller()

    assert await poller.poll_once() == 180

    assert poller.status == "api_error"
    assert logged_in_auth.state is LoginState.LOGGED_IN
    assert publisher.of("account")[-1] == ("account", "api_error")
    fake_mos.renewal_status = 200
    await poller.poll_once()
    assert poller.status == "ok"


async def test_run_waits_outside_window_and_polls_on_request(fake_mos, make_poller, publisher, clock):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    clock.set(22, 0)
    poller = make_poller()
    task = asyncio.create_task(poller.run())
    try:
        await wait_for(lambda: ("account", "outside_hours") in publisher.calls)
        assert publisher.of("state") == []

        poller.request_poll()

        await wait_for(lambda: publisher.of("state"))
        assert poller.status == "ok"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_run_keeps_session_alive_outside_window(fake_mos, make_poller, logged_in_auth, publisher, clock):
    clock.set(22, 0)
    logged_in_auth.logged_in_at = time.time() - 3700
    poller = make_poller()
    task = asyncio.create_task(poller.run())
    try:
        await wait_for(lambda: len(fake_mos.issued_tokens) == 2)
        await wait_for(lambda: ("account", "outside_hours") in publisher.calls)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_run_survives_unexpected_publisher_error(fake_mos, logged_in_auth, api_http, tmp_path, clock):
    fake_mos.visits["guid-101"] = day("08:07", "14:33")
    publisher = FlakyPublisher()
    poller = Poller(
        logged_in_auth, MeshClient(api_http, fake_mos.base_url), publisher, SETTINGS, tmp_path / "state.json", clock
    )
    task = asyncio.create_task(poller.run())
    try:
        await wait_for(lambda: poller.status == "api_error")
        assert not task.done()

        poller.request_poll()

        await wait_for(lambda: poller.status == "ok")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_first_marks_check_is_silent(fake_mos, make_poller, publisher, tmp_path):
    fake_mos.marks["101"] = [make_mark(1, "5")]

    await make_poller().poll_once()

    assert publisher.of("mark") == []
    assert [request.query for request in marks_requests(fake_mos)] == [
        {"student_id": "101", "from": "2026-08-18", "to": "2026-09-16"}
    ]
    assert PollerState.load(tmp_path / "state.json").known_marks(101) == {"1": ["5", "2026-09-16"]}


async def test_new_mark_outside_school_published_after_interval(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "4")]

    clock.set(14, 45)
    await poller.poll_once()
    assert publisher.of("mark") == []
    assert len(marks_requests(fake_mos)) == 1

    clock.set(14, 50)
    await poller.poll_once()
    assert publisher.of("mark") == [("mark", 101, "new", 1, None, "4")]


async def test_marks_held_at_school_and_sent_with_exit(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(8, 2)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "2", subject="Русский язык"), make_mark(2, "НВ")]
    clock.set(10, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [
        make_mark(1, "2", subject="Русский язык"),
        make_mark(2, "4"),
        make_mark(3, "3", day="2026-09-14", form="Контрольная работа", weight=2, exam=True),
    ]
    fake_mos.visits["guid-101"] = day("08:00", "13:00")
    clock.set(13, 3)
    await poller.poll_once()

    assert publisher.of("mark") == []
    assert [call[2] for call in publisher.of("event")] == ["entry", "exit"]
    assert publisher.exit_marks == [[("new", 3, None, "3"), ("new", 2, None, "4"), ("new", 1, None, "2")]]


async def test_exit_fetches_marks_immediately(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(13, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    fake_mos.visits["guid-101"] = day("08:00", "13:02")
    clock.set(13, 3)
    await poller.poll_once()

    assert len(marks_requests(fake_mos)) == 2
    assert publisher.exit_marks == [[("new", 1, None, "5")]]


async def test_marks_failure_does_not_block_exit(fake_mos, make_poller, publisher, clock, caplog):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(9, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    clock.set(9, 30)
    await poller.poll_once()
    fake_mos.marks_statuses = [503, 503]
    fake_mos.visits["guid-101"] = day("08:00", "13:00")

    with caplog.at_level(logging.INFO, logger="app.poller"):
        clock.set(13, 2)
        await poller.poll_once()
        clock.set(13, 20)
        await poller.poll_once()
        clock.set(13, 40)
        await poller.poll_once()

    assert publisher.of("event")[-1][2] == "exit"
    assert publisher.exit_marks == [[("new", 1, None, "5")]]
    assert publisher.of("mark") == []
    assert poller.status == "ok"
    messages = [record.getMessage() for record in caplog.records]
    assert sum("Оценки МЭШ недоступны" in message for message in messages) == 1
    assert "Оценки МЭШ снова доступны" in messages


async def test_marks_failure_logs_traceback_at_debug(fake_mos, make_poller, publisher, clock, caplog):
    poller = make_poller()
    await poller.poll_once()
    fake_mos.marks_statuses = [503]
    clock.set(14, 50)

    with caplog.at_level(logging.DEBUG, logger="app.poller"):
        await poller.poll_once()

    debug_records = [record for record in caplog.records if record.getMessage() == "Подробности ошибки оценок"]
    assert len(debug_records) == 1
    assert debug_records[0].exc_info is not None


async def test_stale_exit_releases_held_marks(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(9, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    clock.set(9, 30)
    await poller.poll_once()
    fake_mos.visits["guid-101"] = day("08:00", "13:00")
    clock.set(18, 0)
    await poller.poll_once()

    assert publisher.of("event") == []
    assert publisher.of("mark") == [("mark", 101, "new", 1, None, "5")]


async def test_marks_held_without_exit_released_next_day(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(9, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    clock.set(9, 30)
    await poller.poll_once()
    assert publisher.of("mark") == []

    clock.set(7, 0, day=17)
    await poller.poll_once()

    assert publisher.of("mark") == [("mark", 101, "new", 1, None, "5")]


async def test_marks_disabled(fake_mos, make_poller, publisher, tmp_path):
    PollerState(marks={"101": {"1": ["5", "2026-09-16"]}}).save(tmp_path / "state.json")
    settings = build_settings({"marks_interval": 0}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})
    fake_mos.visits["guid-101"] = day("08:07", "14:33")

    await make_poller(settings).poll_once()

    assert marks_requests(fake_mos) == []
    assert publisher.exit_marks == [[]]
    assert PollerState.load(tmp_path / "state.json").marks == {}


async def test_marks_unauthorized_renews_token(fake_mos, make_poller, publisher, clock):
    poller = make_poller()
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    fake_mos.marks_statuses = [401]
    clock.set(14, 50)

    await poller.poll_once()

    assert len(fake_mos.issued_tokens) == 2
    assert publisher.of("mark") == [("mark", 101, "new", 1, None, "5")]
    assert poller.status == "ok"


async def test_two_children_hold_marks_independently(fake_mos, make_poller, publisher, clock, tmp_path):
    fake_mos.children.append({"id": 102, "contingent_guid": "guid-102", "first_name": "Мария", "class_name": "1-А"})
    poller = make_poller()
    fake_mos.visits["guid-101"] = day("08:00", "-")
    clock.set(9, 0)
    await poller.poll_once()
    fake_mos.marks["101"] = [make_mark(1, "5")]
    fake_mos.marks["102"] = [make_mark(2, "4")]
    clock.set(9, 30)
    await poller.poll_once()

    assert publisher.of("mark") == [("mark", 102, "new", 2, None, "4")]
    assert [change.mark.id for change in PollerState.load(tmp_path / "state.json").held_changes(101)] == [1]
