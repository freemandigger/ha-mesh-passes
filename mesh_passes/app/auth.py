"""Вход в mos.ru по QR-коду и хранение сессии МЭШ."""

import asyncio
import contextlib
import enum
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiohttp
import segno

from app.mesh import Child, MeshClient, MeshError
from app.session_store import SessionData, cookie_value, load_session, save_session
from app.tokens import jwt_exp

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://login.mos.ru"
SCHOOL_URL = "https://school.mos.ru"
SESSION_COOKIES = frozenset({"aupd_token", "aupd_refresh_token", "Ltpatoken2", "Ltpaexpires"})
LOGIN_ERRORS = (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, AttributeError)


class LoginState(enum.StrEnum):
    LOGGED_OUT = "logged_out"
    QR = "qr"
    CONFIRM = "confirm"
    SMS = "sms"
    LOGGED_IN = "logged_in"
    AUTH_REQUIRED = "auth_required"


class NotLoggedIn(Exception):
    """Нет действующей сессии mos.ru."""


@dataclass
class SmsPrompt:
    deadline: float
    attempts: int | None


def ae_params(*, script: bool, school_url: str) -> dict[str, str]:
    params = {
        "scope": "birthday contacts openid profile snils blitz_change_password blitz_user_rights blitz_qr_auth",
        "access_type": "offline",
        "response_type": "code",
        "state": str(uuid.uuid4()),
        "client_id": "dnevnik.mos.ru",
        "redirect_uri": f"{school_url}/v3/auth/sudir/callback",
        "code_challenge_method": "S256",
    }
    if script:
        params["display"] = "script"
    return params


class Auth:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        mesh: MeshClient,
        session_path: Path,
        *,
        login_url: str = LOGIN_URL,
        school_url: str = SCHOOL_URL,
        qr_poll_seconds: float = 3.0,
        qr_timeout_seconds: float = 600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._mesh = mesh
        self._path = session_path
        self._login = login_url
        self._school = school_url
        self._qr_poll = qr_poll_seconds
        self._qr_timeout = qr_timeout_seconds
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._token_before: str | None = None
        self.state = LoginState.LOGGED_OUT
        self.busy = False
        self.error: str | None = None
        self.qr_svg: str | None = None
        self.sms: SmsPrompt | None = None
        self.profile_id: int | None = None
        self.children: list[Child] = []
        self.logged_in_at: float | None = None
        self.last_renewal: float | None = None
        self.on_login: Callable[[], None] | None = None

    @property
    def _jar(self) -> aiohttp.CookieJar:
        return self._http.cookie_jar

    def load(self) -> None:
        data = load_session(self._path, self._jar)
        if data and data.profile_id and cookie_value(self._jar, "aupd_token"):
            self.profile_id = data.profile_id
            self.children = data.children
            self.logged_in_at = data.logged_in_at
            self.state = LoginState.LOGGED_IN

    def token_expires(self) -> datetime | None:
        token = cookie_value(self._jar, "aupd_token")
        return jwt_exp(token) if token else None

    async def start_login(self) -> None:
        await self._cancel_task()
        self._token_before = cookie_value(self._jar, "aupd_token")
        self.error = None
        self.sms = None
        link = None
        try:
            params = ae_params(script=True, school_url=self._school)
            async with self._http.get(f"{self._login}/sps/oauth/ae", params=params) as response:
                data = await response.json(content_type=None)
            link = next((i.get("link") for i in data.get("items", []) if i.get("inquire") == "show_qr_code"), None)
        except LOGIN_ERRORS as err:
            _LOGGER.warning("mos.ru не начал вход: %s", type(err).__name__)
        if not link:
            self._fail("Не удалось начать вход: mos.ru не выдал QR-код")
            return
        self._show_qr(link)
        self.state = LoginState.QR
        self._task = asyncio.create_task(self._qr_loop())

    async def submit_sms(self, code: str) -> None:
        if self.state is not LoginState.SMS or self.busy:
            return
        code = code.strip()
        if not code.isdigit():
            self.error = "Введите цифры из SMS"
            return
        self.busy = True
        try:
            url = f"{self._login}/sps/login/methods/headless/sms/bind"
            async with self._http.post(url, data={"sms-code": code}, allow_redirects=False) as response:
                location = response.headers.get("Location", "")
                data = await response.json(content_type=None) if response.status == 200 else {}
            if "askToTrust" in location:
                await self._trust()
            elif location:
                async with self._http.get(self._absolute(location)) as response:
                    await response.read()
            else:
                self._sms_error(data)
                return
            if self._new_token():
                await self._finish()
            else:
                self._fail("Вход не завершён: mos.ru не выдал токен")
        except LOGIN_ERRORS as err:
            _LOGGER.warning("Проверка SMS-кода прервана: %s", type(err).__name__)
            self._fail("Ошибка связи с mos.ru во время входа")
        finally:
            self.busy = False

    async def resend_sms(self) -> None:
        if self.state is not LoginState.SMS or self.busy:
            return
        self.busy = True
        try:
            await self._request_sms()
        except LOGIN_ERRORS as err:
            _LOGGER.warning("Повторная отправка SMS не удалась: %s", type(err).__name__)
            self._fail("Ошибка связи с mos.ru во время входа")
        finally:
            self.busy = False

    async def logout(self) -> None:
        await self._cancel_task()
        self._jar.clear(lambda morsel: morsel.key in SESSION_COOKIES)
        self._path.unlink(missing_ok=True)
        self.state = LoginState.AUTH_REQUIRED if self.profile_id else LoginState.LOGGED_OUT
        self.error = None
        self.sms = None
        self.qr_svg = None

    async def close(self) -> None:
        await self._cancel_task()

    async def _qr_loop(self) -> None:
        deadline = self._clock() + self._qr_timeout
        try:
            while self._clock() < deadline:
                params = {"_": str(int(self._clock() * 1000))}
                async with self._http.get(f"{self._login}/sps/login/methods/qrCode/pull", params=params) as response:
                    command = (await response.json(content_type=None)).get("command")
                if command == "askForConfirm":
                    self.state = LoginState.CONFIRM
                elif command == "needRefresh":
                    await self._refresh_qr()
                elif command == "needComplete":
                    await self._complete()
                    return
                await asyncio.sleep(self._qr_poll)
        except LOGIN_ERRORS as err:
            _LOGGER.warning("Вход по QR прерван: %s", type(err).__name__)
            self._fail("Ошибка связи с mos.ru во время входа")
            return
        self._fail("QR-код не отсканирован за 10 минут")

    async def _refresh_qr(self) -> None:
        headers = {"Content-Type": "text/json", "X-Requested-With": "XMLHttpRequest"}
        url = f"{self._login}/sps/login/methods/headless/qrCode/refresh"
        async with self._http.post(url, data="{}", headers=headers) as response:
            data = await response.json(content_type=None)
        self._show_qr(data["link"])
        self.state = LoginState.QR

    async def _complete(self) -> None:
        self.busy = True
        try:
            async with self._http.post(f"{self._login}/sps/login/methods/headless/qrCode/complete") as response:
                text = await response.text()
            if self._new_token():
                await self._finish()
                return
            inquires = {item.get("inquire") for item in json.loads(text).get("items", [])}
            if "ask_to_send_sms" in inquires:
                await self._request_sms()
            else:
                self._fail("mos.ru запросил неподдерживаемый способ подтверждения входа")
        finally:
            self.busy = False

    async def _request_sms(self) -> None:
        url = f"{self._login}/sps/login/methods/headless/sms/bind"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with self._http.post(url, headers=headers, allow_redirects=False) as response:
            data = await response.json(content_type=None)
        if data.get("inquire") != "enter_sms_code":
            self._fail("mos.ru не отправил SMS с кодом")
            return
        self.sms = SmsPrompt(self._clock() + float(data.get("ttl", 300)), data.get("remain_attempts"))
        self.error = None
        self.state = LoginState.SMS

    def _sms_error(self, data: dict) -> None:
        codes = {error.get("code") for error in data.get("errors", [])}
        if "remain_attempts" in data and self.sms:
            self.sms.attempts = data["remain_attempts"]
        if "no_attempts" in codes:
            self._fail("Попытки ввода кода закончились, начните вход заново")
        elif "expired" in codes:
            self.error = "Код истёк — запросите новый"
        else:
            self.error = "Неверный код"

    async def _trust(self) -> None:
        url = f"{self._login}/sps/login/ur/askToTrust"
        async with self._http.get(url) as response:
            await response.read()
        async with self._http.post(url, data={"action": "trust"}, headers={"Referer": url}) as response:
            await response.read()

    async def _finish(self) -> None:
        token = cookie_value(self._jar, "aupd_token")
        try:
            self.profile_id = await self._mesh.profile_id(token)
            self.children = await self._mesh.children(token, self.profile_id)
        except MeshError as err:
            _LOGGER.warning("МЭШ не отдал профиль после входа: %s", err)
            self._fail("Вход выполнен, но МЭШ не отдал профиль родителя")
            return
        self.logged_in_at = self._clock()
        self.state = LoginState.LOGGED_IN
        self.error = None
        self.sms = None
        self.qr_svg = None
        self._save()
        _LOGGER.info("Вход выполнен, детей в профиле: %d", len(self.children))
        if self.on_login:
            self.on_login()

    def _show_qr(self, link: str) -> None:
        self.qr_svg = segno.make(link, error="m").svg_inline(scale=6, border=2)

    def _new_token(self) -> bool:
        token = cookie_value(self._jar, "aupd_token")
        return token is not None and token != self._token_before

    def _absolute(self, location: str) -> str:
        return location if location.startswith("http") else self._login + location

    def _fail(self, message: str) -> None:
        self.error = message
        self.sms = None
        self.qr_svg = None
        self.state = LoginState.AUTH_REQUIRED if self.profile_id else LoginState.LOGGED_OUT

    def _save(self) -> None:
        save_session(self._path, self._jar, SessionData(self.profile_id, self.children, self.logged_in_at))

    async def _cancel_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
