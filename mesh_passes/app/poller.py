import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.auth import Auth, LoginState, NotLoggedIn
from app.events import MSK, ChildState, PassEvent, child_state, visit_events
from app.marks import MarkChange, diff_marks, marks_from, merge_held
from app.mesh import Child, Mark, MeshApiError, MeshAuthError, MeshClient, MeshError
from app.poller_state import PollerState
from app.schedule import in_window
from app.settings import Settings

_LOGGER = logging.getLogger(__name__)
MAX_BACKOFF = 900


class Publisher(Protocol):
    async def publish_account(
        self, profile_id: int, status: str, last_poll: datetime | None, token_expires: datetime | None
    ) -> None: ...

    async def publish_children(self, profile_id: int, children: list[Child]) -> None: ...

    async def publish_child_state(self, profile_id: int, child: Child, state: ChildState) -> None: ...

    async def publish_event(
        self, profile_id: int, child: Child, event: PassEvent, marks: list[MarkChange] | None = None
    ) -> None: ...

    async def publish_mark(self, profile_id: int, child: Child, change: MarkChange) -> None: ...


def _moscow_now() -> datetime:
    return datetime.now(MSK)


class Poller:
    def __init__(
        self,
        auth: Auth,
        mesh: MeshClient,
        publisher: Publisher,
        settings: Settings,
        state_path: Path,
        clock: Callable[[], datetime] = _moscow_now,
    ) -> None:
        self._auth = auth
        self._mesh = mesh
        self._publisher = publisher
        self._settings = settings
        self._state_path = state_path
        self._clock = clock
        self._state = PollerState.load(state_path)
        self._interval = settings.poll_interval * 60
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._errors = 0
        self._children_day: date | None = None
        self._published_children: list[int] | None = None
        self.status = "ok" if auth.state is LoginState.LOGGED_IN else "auth_required"
        self.last_poll: datetime | None = None
        self.child_states: dict[int, ChildState] = {}
        self._marks_checked: dict[int, datetime] = {}
        self._marks_failing = False
        if settings.marks_interval == 0:
            self._state.clear_marks()

    def request_poll(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        delay: float = 0
        while True:
            forced = await self._sleep(delay)
            try:
                await self._auth.keepalive()
                if forced or in_window(self._clock(), self._settings):
                    delay = await self.poll_once()
                else:
                    await self._set_status("outside_hours")
                    delay = self._interval
            except Exception as err:
                self.status = "api_error"
                _LOGGER.error("Непредвиденная ошибка цикла опроса: %s", type(err).__name__)
                _LOGGER.debug("Подробности ошибки цикла опроса", exc_info=True)
                delay = self._interval

    async def poll_once(self) -> float:
        async with self._lock:
            now = self._clock()
            if self._auth.state is not LoginState.LOGGED_IN:
                await self._set_status("auth_required")
                return self._interval
            try:
                try:
                    await self._poll_all(now)
                except MeshAuthError:
                    await self._auth.renew()
                    await self._poll_all(now)
            except (NotLoggedIn, MeshAuthError):
                self._auth.mark_expired()
                await self._set_status("auth_required")
                return self._interval
            except MeshApiError as err:
                self._errors += 1
                delay = max(self._interval, min(self._interval * 2 ** (self._errors - 1), MAX_BACKOFF))
                delay = max(delay, err.retry_after or 0)
                _LOGGER.warning("Ошибка API МЭШ: %s; следующая попытка через %d с", err, delay)
                await self._set_status("api_error")
                return delay
            finally:
                self._state.save(self._state_path)
            self._errors = 0
            self.last_poll = now
            await self._set_status("ok")
            return self._interval

    async def _sleep(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False
        finally:
            self._wake.clear()

    async def _poll_all(self, now: datetime) -> None:
        await self._sync_children(now)
        for child in list(self._auth.children):
            await self._poll_child(child, now)
        self._state.prune(now.date())

    async def _sync_children(self, now: datetime) -> None:
        if self._children_day != now.date():
            await self._auth.refresh_children()
            self._children_day = now.date()
        ids = [child.id for child in self._auth.children]
        if ids != self._published_children:
            await self._publisher.publish_children(self._auth.profile_id, self._auth.children)
            self._published_children = ids

    async def _poll_child(self, child: Child, now: datetime) -> None:
        token = await self._auth.token()
        visits = [
            visit
            for visit in await self._mesh.visits(token, self._auth.profile_id, child, now.date())
            if visit.day == now.date()
        ]
        fresh = self._state.take_new(
            visit_events(child.id, visits), now, timedelta(minutes=self._settings.max_event_age)
        )
        state = child_state(visits, *self._state.last_times(child.id))
        self._state.set_last_times(child.id, state.last_entry, state.last_exit)
        self.child_states[child.id] = state
        await self._publisher.publish_child_state(self._auth.profile_id, child, state)
        exits = [event for event in fresh if event.kind == "exit"]
        changes = await self._check_marks(child, now, bool(exits))
        exit_marks, mark_events = self._route_marks(child, changes, state.at_school, bool(exits))
        for event in fresh:
            _LOGGER.info("Проход: %s, ребёнок %d", event.kind, child.id)
            marks = exit_marks if exits and event is exits[-1] else None
            await self._publisher.publish_event(self._auth.profile_id, child, event, marks)
        for change in mark_events:
            _LOGGER.info("Оценка: %s, ребёнок %d", change.kind, child.id)
            await self._publisher.publish_mark(self._auth.profile_id, child, change)

    async def _check_marks(self, child: Child, now: datetime, exit_now: bool) -> list[MarkChange]:
        interval = timedelta(minutes=self._settings.marks_interval)
        last = self._marks_checked.get(child.id)
        if not interval or not (exit_now or last is None or now - last >= interval):
            return []
        self._marks_checked[child.id] = now
        try:
            marks = await self._fetch_marks(child, now.date())
            changes, known = diff_marks(self._state.known_marks(child.id), marks, now.date())
        except Exception as err:
            if not self._marks_failing:
                self._marks_failing = True
                _LOGGER.warning("Оценки МЭШ недоступны: %s", err if isinstance(err, MeshError) else type(err).__name__)
            _LOGGER.debug("Подробности ошибки оценок", exc_info=True)
            return []
        self._state.set_known_marks(child.id, known)
        if self._marks_failing:
            self._marks_failing = False
            _LOGGER.info("Оценки МЭШ снова доступны")
        return changes

    async def _fetch_marks(self, child: Child, today: date) -> list[Mark]:
        profile_id, date_from = self._auth.profile_id, marks_from(today)
        try:
            return await self._mesh.marks(await self._auth.token(), profile_id, child, date_from, today)
        except MeshAuthError:
            return await self._mesh.marks(await self._auth.renew(), profile_id, child, date_from, today)

    def _route_marks(
        self, child: Child, changes: list[MarkChange], at_school: bool, exit_now: bool
    ) -> tuple[list[MarkChange], list[MarkChange]]:
        pending = merge_held(self._state.held_changes(child.id), changes)
        if at_school and not exit_now:
            self._state.set_held(child.id, pending)
            return [], []
        self._state.set_held(child.id, [])
        return (pending, []) if exit_now else ([], pending)

    async def _set_status(self, status: str) -> None:
        self.status = status
        if self._auth.profile_id is not None:
            await self._publisher.publish_account(
                self._auth.profile_id, status, self.last_poll, self._auth.token_expires()
            )
