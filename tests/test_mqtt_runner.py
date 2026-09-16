import asyncio
import contextlib

import aiomqtt

from app.mqtt_runner import handle_message, run_mqtt
from app.publisher import MqttPublisher
from app.settings import build_settings
from helpers import wait_for

SETTINGS = build_settings({}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})


class Silence:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()


class FakeClient:
    def __init__(self):
        self.messages = Silence()
        self.published = []
        self.fail = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.closed = True

    async def subscribe(self, topic):
        pass

    async def publish(self, topic, payload, qos=0, retain=False):
        if self.fail:
            raise aiomqtt.MqttError("Operation timed out")
        self.published.append((topic, payload, retain))

    def topics(self):
        return [topic for topic, _, _ in self.published]


async def make_publisher():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    await publisher.publish_account(777, "ok", None, None)
    client.published.clear()
    return publisher, client


async def test_home_assistant_online_triggers_republish():
    publisher, client = await make_publisher()
    polls = []

    await handle_message("homeassistant/status", b"online", "homeassistant", publisher, lambda: polls.append(1))

    assert "homeassistant/device/mesh_passes_777/config" in client.topics()
    assert polls == []


async def test_home_assistant_offline_is_ignored():
    publisher, client = await make_publisher()

    await handle_message("homeassistant/status", b"offline", "homeassistant", publisher, lambda: None)

    assert client.published == []


async def test_poll_now_button():
    publisher, client = await make_publisher()
    polls = []

    await handle_message("mesh_passes/777/poll_now/set", b"PRESS", "homeassistant", publisher, lambda: polls.append(1))

    assert polls == [1]
    assert client.published == []


async def test_publish_failure_forces_reconnect(monkeypatch):
    first, second = FakeClient(), FakeClient()
    clients = iter([first, second])
    monkeypatch.setattr(aiomqtt, "Client", lambda **kwargs: next(clients))
    monkeypatch.setattr("app.mqtt_runner.RECONNECT_SECONDS", 0)
    publisher = MqttPublisher("homeassistant")
    task = asyncio.create_task(run_mqtt(SETTINGS, publisher, lambda: None))
    try:
        await wait_for(lambda: first.topics() == ["mesh_passes/availability"])
        first.fail = True

        await publisher.publish_account(777, "ok", None, None)

        await wait_for(lambda: "mesh_passes/777/status" in second.topics())
        assert first.closed
        assert second.published[0] == ("mesh_passes/availability", "online", True)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
