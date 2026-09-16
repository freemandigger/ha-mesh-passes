import asyncio
import logging
from collections.abc import Callable

import aiomqtt

from app.discovery import AVAILABILITY_TOPIC, BASE
from app.publisher import MqttPublisher
from app.settings import Settings

_LOGGER = logging.getLogger(__name__)
RECONNECT_SECONDS = 10


async def handle_message(
    topic: str, payload: bytes, prefix: str, publisher: MqttPublisher, poll_now: Callable[[], None]
) -> None:
    if topic == f"{prefix}/status" and payload == b"online":
        await publisher.republish()
    elif topic.startswith(f"{BASE}/") and topic.endswith("/poll_now/set"):
        poll_now()


async def listen(client: aiomqtt.Client, prefix: str, publisher: MqttPublisher, poll_now: Callable[[], None]) -> None:
    async def handle_all() -> None:
        async for message in client.messages:
            payload = message.payload if isinstance(message.payload, bytes) else str(message.payload).encode()
            await handle_message(message.topic.value, payload, prefix, publisher, poll_now)

    messages = asyncio.create_task(handle_all())
    lost = asyncio.create_task(publisher.connection_lost.wait())
    try:
        await asyncio.wait({messages, lost}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        messages.cancel()
        lost.cancel()
    if not messages.done():
        raise aiomqtt.MqttError("публикация не прошла")
    messages.result()


async def run_mqtt(settings: Settings, publisher: MqttPublisher, poll_now: Callable[[], None]) -> None:
    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_username,
                password=settings.mqtt_password,
                identifier="ha-mesh-passes",
                will=aiomqtt.Will(AVAILABILITY_TOPIC, "offline", qos=1, retain=True),
            ) as client:
                _LOGGER.info("MQTT подключён: %s:%d", settings.mqtt_host, settings.mqtt_port)
                await client.subscribe(f"{settings.discovery_prefix}/status")
                await client.subscribe(f"{BASE}/+/poll_now/set")
                await publisher.attach(client)
                await listen(client, settings.discovery_prefix, publisher, poll_now)
        except aiomqtt.MqttError as err:
            publisher.detach()
            _LOGGER.warning("MQTT недоступен (%s), переподключение через %d с", err, RECONNECT_SECONDS)
            await asyncio.sleep(RECONNECT_SECONDS)
