from app.mqtt_runner import handle_message
from app.publisher import MqttPublisher


class FakeClient:
    def __init__(self):
        self.messages = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.messages.append((topic, payload, retain))


async def make_publisher():
    client = FakeClient()
    publisher = MqttPublisher("homeassistant")
    await publisher.attach(client)
    await publisher.publish_account(777, "ok", None, None)
    client.messages.clear()
    return publisher, client


async def test_home_assistant_online_triggers_republish():
    publisher, client = await make_publisher()
    polls = []

    await handle_message("homeassistant/status", b"online", "homeassistant", publisher, lambda: polls.append(1))

    assert "homeassistant/device/mesh_passes_777/config" in [topic for topic, _, _ in client.messages]
    assert polls == []


async def test_home_assistant_offline_is_ignored():
    publisher, client = await make_publisher()

    await handle_message("homeassistant/status", b"offline", "homeassistant", publisher, lambda: None)

    assert client.messages == []


async def test_poll_now_button():
    publisher, client = await make_publisher()
    polls = []

    await handle_message("mesh_passes/777/poll_now/set", b"PRESS", "homeassistant", publisher, lambda: polls.append(1))

    assert polls == [1]
    assert client.messages == []
