import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from typing import Protocol

import aiomqtt

from app.discovery import (
    AVAILABILITY_TOPIC,
    account_discovery,
    account_topic,
    child_discovery,
    child_slugs,
    child_topic,
)
from app.events import ChildState, PassEvent
from app.marks import MarkChange, change_fields
from app.mesh import Child

_LOGGER = logging.getLogger(__name__)
MAX_PENDING_EVENTS = 100


class MqttClient(Protocol):
    async def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None: ...


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


class MqttPublisher:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._client: MqttClient | None = None
        self._retained: dict[str, str] = {}
        self._pending: deque[tuple[str, str]] = deque(maxlen=MAX_PENDING_EVENTS)
        self._child_topics: dict[int, list[str]] = {}
        self.connection_lost = asyncio.Event()

    async def attach(self, client: MqttClient) -> None:
        self._client = client
        self.connection_lost.clear()
        await self._send(AVAILABILITY_TOPIC, "online", retain=True)
        for topic, payload in list(self._retained.items()):
            if await self._send(topic, payload, retain=True) and not payload:
                del self._retained[topic]
        while self._pending and self._client is not None:
            topic, payload = self._pending.popleft()
            if not await self._send(topic, payload, retain=False):
                self._pending.appendleft((topic, payload))

    def detach(self) -> None:
        self._client = None

    async def republish(self) -> None:
        if self._client is not None:
            await self.attach(self._client)

    async def publish_account(
        self, profile_id: int, status: str, last_poll: datetime | None, token_expires: datetime | None
    ) -> None:
        await self._ensure_account(profile_id)
        await self._retain(account_topic(profile_id, "status"), status)
        await self._retain(account_topic(profile_id, "last_poll"), _iso(last_poll) or "None")
        await self._retain(account_topic(profile_id, "token_expires"), _iso(token_expires) or "None")

    async def publish_children(self, profile_id: int, children: list[Child]) -> None:
        await self._ensure_account(profile_id)
        current = {child.id for child in children}
        for child_id in [known for known in self._child_topics if known not in current]:
            for topic in self._child_topics.pop(child_id):
                await self._retain(topic, "")
        slugs = child_slugs(children)
        for child in children:
            topic, config = child_discovery(self._prefix, profile_id, child, slugs[child.id])
            await self._retain(topic, _json(config))
            self._child_topics[child.id] = [topic, child_topic(profile_id, child.id, "state")]

    async def publish_child_state(self, profile_id: int, child: Child, state: ChildState) -> None:
        payload = {
            "at_school": "ON" if state.at_school else "OFF",
            "last_entry": _iso(state.last_entry),
            "last_exit": _iso(state.last_exit),
            "school": state.school,
            "visits": state.visits,
        }
        await self._retain(child_topic(profile_id, child.id, "state"), _json(payload))

    async def publish_event(
        self, profile_id: int, child: Child, event: PassEvent, marks: list[MarkChange] | None = None
    ) -> None:
        data = {
            "event_type": event.kind,
            "child": child.first_name,
            "time": event.at.isoformat(),
            "school": event.school,
            "person": event.person,
        }
        if marks is not None:
            data["marks"] = [{"kind": change.kind, **change_fields(change)} for change in marks]
        await self._send_event(child_topic(profile_id, child.id, "event"), _json(data), child, event.kind)

    async def publish_mark(self, profile_id: int, child: Child, change: MarkChange) -> None:
        payload = _json({"event_type": change.kind, "child": child.first_name, **change_fields(change)})
        await self._send_event(child_topic(profile_id, child.id, "mark"), payload, child, change.kind)

    async def _send_event(self, topic: str, payload: str, child: Child, kind: str) -> None:
        if not await self._send(topic, payload, retain=False):
            if len(self._pending) == MAX_PENDING_EVENTS:
                _LOGGER.warning(
                    "Очередь событий MQTT переполнена, старое событие отброшено (ребёнок %d, %s)",
                    child.id,
                    kind,
                )
            self._pending.append((topic, payload))

    async def _ensure_account(self, profile_id: int) -> None:
        topic, config = account_discovery(self._prefix, profile_id)
        if topic not in self._retained:
            await self._retain(topic, _json(config))

    async def _retain(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload
        if await self._send(topic, payload, retain=True) and not payload:
            del self._retained[topic]

    async def _send(self, topic: str, payload: str, *, retain: bool) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(topic, payload, qos=1, retain=retain)
        except aiomqtt.MqttError:
            if self._client is client:
                self._client = None
                self.connection_lost.set()
            return False
        return True
