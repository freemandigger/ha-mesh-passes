import asyncio
import contextlib
import time
from datetime import datetime

import pytest

from app.auth import LoginState
from app.events import MSK
from app.mesh import MeshClient
from app.poller import Poller
from app.settings import build_settings
from helpers import wait_for

SETTINGS = build_settings({}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish_account(self, profile_id, status, last_poll, token_expires):
        self.calls.append(("account", status))

    async def publish_children(self, profile_id, children):
        self.calls.append(("children", [child.id for child in children]))

    async def publish_child_state(self, profile_id, child, state):
        self.calls.append(("state", child.id, state.at_school))

    async def publish_event(self, profile_id, child, event):
        self.calls.append(("event", child.id, event.kind, event.id))

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

    def set(self, hours, minutes):
        self.now = datetime(2026, 9, 16, hours, minutes, tzinfo=MSK)

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


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def clock():
    return Clock(14, 35)


@pytest.fixture
def make_poller(fake_mos, logged_in_auth, api_http, tmp_path, publisher, clock):
    def make():
        return Poller(
            logged_in_auth, MeshClient(api_http, fake_mos.base_url), publisher, SETTINGS, tmp_path / "state.json", clock
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
