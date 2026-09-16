import json
from datetime import datetime

import aiomqtt

from app.events import MSK, ChildState, PassEvent
from app.mesh import Child
from app.publisher import MqttPublisher

IVAN = Child(101, "guid-101", "Иван", "3-А")
EXIT = PassEvent(
    "2026-09-16|1234|out|14:33", 101, "exit", datetime(2026, 9, 16, 14, 33, tzinfo=MSK), "ГБОУ Школа № 1", None
)


class FakeClient:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    async def publish(self, topic, payload, qos=0, retain=False):
        if self.fail:
            raise aiomqtt.MqttError("connection lost")
        self.messages.append((topic, payload, retain))

    def topics(self):
        return [topic for topic, _, _ in self.messages]


async def test_attach_announces_online_first():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    assert client.messages == [("mesh_passes/availability", "online", True)]


async def test_account_discovery_published_once_and_states_retained():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)

    await publisher.publish_account(777, "ok", None, None)
    await publisher.publish_account(777, "api_error", None, None)

    assert client.topics().count("homeassistant/device/mesh_passes_777/config") == 1
    assert ("mesh_passes/777/status", "api_error", True) in client.messages
    assert ("mesh_passes/777/last_poll", "None", True) in client.messages


async def test_child_state_retained_and_event_not_retained():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    state = ChildState(False, None, EXIT.at, "ГБОУ Школа № 1", [{"in": "08:07", "out": "14:33"}])

    await publisher.publish_child_state(777, IVAN, state)
    await publisher.publish_event(777, IVAN, EXIT)

    topic, payload, retain = client.messages[-2]
    assert (topic, retain) == ("mesh_passes/777/child/101/state", True)
    assert json.loads(payload) == {
        "at_school": "OFF",
        "last_entry": None,
        "last_exit": "2026-09-16T14:33:00+03:00",
        "school": "ГБОУ Школа № 1",
        "visits": [{"in": "08:07", "out": "14:33"}],
    }
    topic, payload, retain = client.messages[-1]
    assert (topic, retain) == ("mesh_passes/777/child/101/event", False)
    assert json.loads(payload) == {
        "event_type": "exit",
        "child": "Иван",
        "time": "2026-09-16T14:33:00+03:00",
        "school": "ГБОУ Школа № 1",
        "person": None,
    }


async def test_detached_publisher_caches_and_flushes_on_attach():
    publisher = MqttPublisher("homeassistant")
    await publisher.publish_account(777, "ok", None, None)
    await publisher.publish_event(777, IVAN, EXIT)

    client = FakeClient()
    await publisher.attach(client)

    assert client.topics()[0] == "mesh_passes/availability"
    assert "mesh_passes/777/status" in client.topics()
    assert client.messages[-1][0] == "mesh_passes/777/child/101/event"


async def test_connection_loss_queues_event():
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(FakeClient(fail=True))

    await publisher.publish_event(777, IVAN, EXIT)
    client = FakeClient()
    await publisher.attach(client)

    assert client.messages[-1][0] == "mesh_passes/777/child/101/event"


async def test_vanished_child_is_removed():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    await publisher.publish_children(777, [IVAN, Child(102, "guid-102", "Мария", "1-А")])

    await publisher.publish_children(777, [IVAN])

    assert ("homeassistant/device/mesh_passes_777_102/config", "", True) in client.messages
    assert ("mesh_passes/777/child/102/state", "", True) in client.messages


async def test_republish_resends_retained():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    await publisher.publish_account(777, "ok", None, None)
    client.messages.clear()

    await publisher.republish()

    assert "homeassistant/device/mesh_passes_777/config" in client.topics()
    assert "mesh_passes/777/status" in client.topics()
