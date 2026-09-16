# МЭШ: проходы — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** аддон Home Assistant, который входит в mos.ru по QR-коду, опрашивает проходы детей через турникеты школ в МЭШ и публикует их в HA через MQTT, плюс blueprints для уведомлений.

**Architecture:** один Python-процесс на asyncio: `auth` (вход и сессия) → `mesh` (API МЭШ) → `poller` (расписание, события) → `publisher`/`mqtt_runner` (MQTT discovery, состояния, события) и `web` (ingress-страница входа). Состояние — JSON-файлы в `/data`. Сборка — Docker-образ `python:3.12-slim`, распространение — репозиторий аддонов HA + GHCR.

**Tech Stack:** Python 3.12, aiohttp, aiomqtt 2.x, segno, tzdata; тесты — pytest, pytest-asyncio, pytest-aiohttp, PyYAML; линт — ruff; окружение — uv; CI — GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-16-mesh-passes-addon-design.md`

## Global Constraints

- Python `>=3.12`; runtime-зависимости только `aiohttp>=3.10,<4`, `aiomqtt>=2.3,<3`, `segno>=1.6,<2`, `tzdata>=2024.1`.
- Архитектуры: `aarch64`, `amd64`. Образы: `ghcr.io/freemandigger/mesh-passes-{arch}`. Репозиторий: `https://github.com/freemandigger/ha-mesh-passes`. Лицензия MIT.
- Вход в mos.ru только по QR (+ SMS + `action=trust`). Никакого входа по паролю, решения proof-of-work, секретов из официальных приложений.
- Запросы к `school.mos.ru/api/...` — отдельная `aiohttp.ClientSession` с `DummyCookieJar`; только заголовок `Authorization: Bearer`, без cookie и `Auth-Token`.
- В логах никогда: токены, cookies, коды SMS, ФИО, СНИЛС, телефоны, email, адреса. Допустимо: шаги, HTTP-статусы, ID ребёнка, ключи JSON.
- Файлы в каталоге данных — права `0600`, запись атомарная.
- Всё время — `Europe/Moscow`.
- Опции (по умолчанию / пределы): `poll_interval` 3 (2–60 мин), `active_from` `07:00`, `active_to` `20:00`, `active_days` `[mon..sat]`, `max_event_age` 30 (5–240 мин), `discovery_prefix` `homeassistant`, `log_level` `info`.
- Топики: `mesh_passes/availability`; `mesh_passes/<profile_id>/{status,last_poll,token_expires,poll_now/set}`; `mesh_passes/<profile_id>/child/<child_id>/{state,event}`; discovery `<prefix>/device/mesh_passes_<profile_id>[_<child_id>]/config`. События — без `retain`, всё остальное — с `retain`.
- Тексты интерфейса и логов — по-русски.
- **Чужой код не копируется.** Из открытых проектов берутся только сведения об API (адреса, параметры, порядок запросов); реализация пишется с нуля. Проекты-источники перечисляются в разделе «Благодарности» README (Task 13). Код GPL-проектов (`DavidZhivaev/SchoolAPI`) не открывать для заимствования.
- **Никаких личных данных владельца в репозитории:** ни chat_id, ни IP, ни имён детей, ни школы, ни ID профиля. В тестовых фикстурах — вымышленные значения (`Иван`, `3-А`, `ГБОУ Школа № 1`, `777`, `101`, `guid-101`, `1234`).
- Каждый commit завершается строкой `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Карта файлов

```
.gitignore, LICENSE, README.md, repository.yaml, pyproject.toml, docker-compose.dev.yaml
dev/mosquitto.conf
.github/workflows/ci.yaml, release.yaml
blueprints/automation/mesh_passes/pass_notification.yaml, session_alert.yaml
mesh_passes/
  config.yaml, Dockerfile, requirements.txt, DOCS.md, CHANGELOG.md
  translations/ru.yaml, en.yaml
  app/
    __init__.py        VERSION
    __main__.py        сборка и запуск
    settings.py        опции аддона / env → Settings
    tokens.py          срок действия JWT
    mesh.py            клиент API МЭШ, модели Child и Visit
    session_store.py   сохранение cookies и профиля
    auth.py            вход по QR/SMS, продление токена
    events.py          события прохода и состояние ребёнка
    schedule.py        окно опроса
    poller_state.py    state.json: увиденные события, последние времена
    poller.py          цикл опроса
    discovery.py       discovery-конфиги и топики MQTT
    publisher.py       публикация в MQTT с кешем и очередью
    mqtt_runner.py     подключение к брокеру, входящие сообщения
    web/__init__.py
    web/server.py      ingress-страница и JSON API
    web/static/index.html, app.js
tests/
  conftest.py, helpers.py, fake_mos.py
  test_settings.py, test_mesh.py, test_session_store.py, test_auth_login.py, test_auth_renew.py,
  test_events.py, test_schedule.py, test_poller_state.py, test_poller.py, test_discovery.py,
  test_publisher.py, test_mqtt_runner.py, test_web.py, test_main.py, test_blueprints.py
```

---

### Task 1: Каркас проекта и настройки

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `LICENSE`, `mesh_passes/app/__init__.py`, `mesh_passes/app/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `app.VERSION: str = "0.1.0"`
  - `app.settings.SettingsError(ValueError)`
  - `app.settings.Settings` (frozen dataclass): `addon: bool`, `poll_interval: int`, `active_from: datetime.time`, `active_to: datetime.time`, `active_days: frozenset[int]` (пн=0), `max_event_age: int`, `discovery_prefix: str`, `log_level: str`, `mqtt_host: str`, `mqtt_port: int`, `mqtt_username: str | None`, `mqtt_password: str | None`, `web_port: int`, `web_password: str | None`, `data_dir: pathlib.Path`
  - `app.settings.build_settings(options: Mapping[str, object], env: Mapping[str, str], mqtt_service: Mapping[str, object] | None) -> Settings`

- [ ] **Step 1: Создать конфигурацию проекта**

`pyproject.toml`:

```toml
[project]
name = "ha-mesh-passes"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "aiohttp>=3.10,<4",
  "aiomqtt>=2.3,<3",
  "segno>=1.6,<2",
  "tzdata>=2024.1",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-aiohttp>=1.0.5",
  "pyyaml>=6.0",
  "ruff>=0.6",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
pythonpath = ["mesh_passes"]
testpaths = ["tests"]

[tool.ruff]
line-length = 120
target-version = "py312"
src = ["mesh_passes", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

`.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
dev/data/
```

`LICENSE`:

```
MIT License

Copyright (c) 2026 Dmitriy Beketov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`mesh_passes/app/__init__.py`:

```python
VERSION = "0.1.0"
```

Run: `uv sync`
Expected: создан `.venv`, установлены зависимости без ошибок.

- [ ] **Step 2: Написать падающие тесты настроек**

`tests/test_settings.py`:

```python
from datetime import time
from pathlib import Path

import pytest

from app.settings import SettingsError, build_settings

ADDON_ENV = {"SUPERVISOR_TOKEN": "x"}
MQTT = {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "secret"}


def test_addon_defaults():
    settings = build_settings({}, ADDON_ENV, MQTT)
    assert settings.addon is True
    assert settings.poll_interval == 3
    assert (settings.active_from, settings.active_to) == (time(7, 0), time(20, 0))
    assert settings.active_days == frozenset({0, 1, 2, 3, 4, 5})
    assert settings.max_event_age == 30
    assert settings.discovery_prefix == "homeassistant"
    assert settings.log_level == "info"
    assert (settings.mqtt_host, settings.mqtt_port) == ("core-mosquitto", 1883)
    assert (settings.mqtt_username, settings.mqtt_password) == ("addons", "secret")
    assert settings.web_password is None
    assert settings.web_port == 8099
    assert settings.data_dir == Path("/data")


def test_addon_options_override_defaults():
    settings = build_settings({"poll_interval": 5, "active_days": ["sun"], "active_from": "08:30"}, ADDON_ENV, MQTT)
    assert settings.poll_interval == 5
    assert settings.active_days == frozenset({6})
    assert settings.active_from == time(8, 30)


def test_addon_requires_mqtt_service():
    with pytest.raises(SettingsError, match="MQTT"):
        build_settings({}, ADDON_ENV, None)


def test_docker_reads_environment():
    env = {
        "WEB_PASSWORD": "pw",
        "MQTT_HOST": "broker",
        "MQTT_PORT": "1884",
        "POLL_INTERVAL": "10",
        "ACTIVE_DAYS": "mon,fri",
        "DATA_DIR": "/tmp/mesh",
    }
    settings = build_settings({}, env, None)
    assert settings.addon is False
    assert (settings.mqtt_host, settings.mqtt_port) == ("broker", 1884)
    assert settings.poll_interval == 10
    assert settings.active_days == frozenset({0, 4})
    assert settings.web_password == "pw"
    assert settings.data_dir == Path("/tmp/mesh")


def test_docker_requires_web_password():
    with pytest.raises(SettingsError, match="WEB_PASSWORD"):
        build_settings({}, {}, None)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"poll_interval": 1}, "poll_interval"),
        ({"max_event_age": 500}, "max_event_age"),
        ({"active_from": "7:00"}, "active_from"),
        ({"active_from": "20:00", "active_to": "07:00"}, "active_to"),
        ({"active_days": ["funday"]}, "active_days"),
        ({"log_level": "loud"}, "log_level"),
    ],
)
def test_invalid_options(options, message):
    with pytest.raises(SettingsError, match=message):
        build_settings(options, ADDON_ENV, MQTT)
```

- [ ] **Step 2a: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.settings'`.

- [ ] **Step 3: Реализовать настройки**

`mesh_passes/app/settings.py`:

```python
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LOG_LEVELS = ("debug", "info", "warning", "error")
DEFAULT_OPTIONS: dict[str, object] = {
    "poll_interval": 3,
    "active_from": "07:00",
    "active_to": "20:00",
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "max_event_age": 30,
    "discovery_prefix": "homeassistant",
    "log_level": "info",
}


class SettingsError(ValueError):
    """Недопустимые настройки аддона."""


@dataclass(frozen=True)
class Settings:
    addon: bool
    poll_interval: int
    active_from: time
    active_to: time
    active_days: frozenset[int]
    max_event_age: int
    discovery_prefix: str
    log_level: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    web_port: int
    web_password: str | None
    data_dir: Path


def _hhmm(name: str, value: object) -> time:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise SettingsError(f"{name}: ожидается ЧЧ:ММ, получено {value!r}")
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _int(name: str, value: object, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name}: ожидается целое число, получено {value!r}") from None
    if not low <= number <= high:
        raise SettingsError(f"{name}: допустимо {low}–{high}, получено {number}")
    return number


def _days(value: object) -> frozenset[int]:
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple) or not items:
        raise SettingsError(f"active_days: ожидается список дней, получено {value!r}")
    days = set()
    for item in items:
        day = str(item).strip().lower()
        if day not in DAYS:
            raise SettingsError(f"active_days: неизвестный день {item!r}")
        days.add(DAYS.index(day))
    return frozenset(days)


def _options_from_env(env: Mapping[str, str]) -> dict[str, object]:
    return {key: env[key.upper()] for key in DEFAULT_OPTIONS if key.upper() in env}


def build_settings(
    options: Mapping[str, object], env: Mapping[str, str], mqtt_service: Mapping[str, object] | None
) -> Settings:
    addon = "SUPERVISOR_TOKEN" in env
    merged = {**DEFAULT_OPTIONS, **(options if addon else _options_from_env(env))}
    active_from = _hhmm("active_from", merged["active_from"])
    active_to = _hhmm("active_to", merged["active_to"])
    if active_to <= active_from:
        raise SettingsError("active_to должно быть позже active_from")
    log_level = str(merged["log_level"]).lower()
    if log_level not in LOG_LEVELS:
        raise SettingsError(f"log_level: неизвестный уровень {log_level!r}")

    if addon:
        if not mqtt_service:
            raise SettingsError("MQTT недоступен: установите и запустите брокер MQTT (например, аддон Mosquitto)")
        mqtt_host = str(mqtt_service["host"])
        mqtt_port = int(mqtt_service["port"])
        mqtt_username = str(mqtt_service.get("username") or "") or None
        mqtt_password = str(mqtt_service.get("password") or "") or None
        web_password = None
    else:
        mqtt_host = env.get("MQTT_HOST", "localhost")
        mqtt_port = _int("MQTT_PORT", env.get("MQTT_PORT", "1883"), 1, 65535)
        mqtt_username = env.get("MQTT_USERNAME") or None
        mqtt_password = env.get("MQTT_PASSWORD") or None
        web_password = env.get("WEB_PASSWORD") or None
        if not web_password:
            raise SettingsError("WEB_PASSWORD обязателен при запуске вне Home Assistant")

    return Settings(
        addon=addon,
        poll_interval=_int("poll_interval", merged["poll_interval"], 2, 60),
        active_from=active_from,
        active_to=active_to,
        active_days=_days(merged["active_days"]),
        max_event_age=_int("max_event_age", merged["max_event_age"], 5, 240),
        discovery_prefix=str(merged["discovery_prefix"]).strip("/") or "homeassistant",
        log_level=log_level,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        web_port=_int("WEB_PORT", env.get("WEB_PORT", "8099"), 1, 65535),
        web_password=web_password,
        data_dir=Path(env.get("DATA_DIR", "/data")),
    )
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_settings.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 11 passed; ruff без замечаний (если `format --check` ругается — `uv run ruff format .` и повторить).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore LICENSE mesh_passes/app/__init__.py mesh_passes/app/settings.py tests/test_settings.py
git commit -m "feat: project scaffold and add-on settings" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Эмулятор mos.ru и клиент API МЭШ

**Files:**
- Create: `mesh_passes/app/tokens.py`, `mesh_passes/app/mesh.py`, `tests/fake_mos.py`, `tests/conftest.py`
- Test: `tests/test_mesh.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `app.tokens.jwt_exp(token: str) -> datetime | None` (aware, UTC; без проверки подписи)
  - `app.mesh.SCHOOL_URL = "https://school.mos.ru"`
  - `app.mesh.MeshError(Exception)`, `MeshAuthError(MeshError)`, `MeshApiError(MeshError)` с атрибутом `retry_after: float | None`
  - `app.mesh.Child(id: int, guid: str, first_name: str, class_name: str)` (frozen)
  - `app.mesh.Visit(day: date, time_in: str, time_out: str, incomplete: bool, organization_id: int | None, school: str | None, person_in: str | None, person_out: str | None)` (frozen); `time_in`/`time_out` — `"HH:MM"` или `"-"`
  - `app.mesh.MeshClient(http: aiohttp.ClientSession, base_url: str = SCHOOL_URL)` с методами `async profile_id(token) -> int`, `async children(token, profile_id) -> list[Child]`, `async visits(token, profile_id, child, day) -> list[Visit]`
  - `tests/fake_mos.py`: `make_jwt(exp: float, marker: str = "") -> str`, `Recorded(method, path, headers: CIMultiDict, query: dict, form: dict)`, `FakeMos` (поля и поведение — в коде ниже)
  - фикстуры `fake_mos`, `api_http`, `login_http`

- [ ] **Step 1: Написать эмулятор mos.ru**

`tests/fake_mos.py`:

```python
"""Эмулятор login.mos.ru и school.mos.ru на одном локальном сервере."""

import base64
import json
import time
from dataclasses import dataclass, field

from aiohttp import web
from multidict import CIMultiDict


def make_jwt(exp: float, marker: str = "") -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(exp), 'jti': marker})}.signature"


@dataclass
class Recorded:
    method: str
    path: str
    headers: CIMultiDict
    query: dict[str, str]
    form: dict[str, str]


def default_children() -> list[dict]:
    return [{"id": 101, "contingent_guid": "guid-101", "first_name": "Иван", "class_name": "3-А"}]


@dataclass
class FakeMos:
    base_url: str = ""
    qr_commands: list[str] = field(default_factory=lambda: ["showQRCode", "askForConfirm", "needComplete"])
    qr_refreshes: int = 0
    trusted_device: bool = False
    sms_code: str = "123456"
    sso_alive: bool = True
    refresh_works: bool = False
    token_ttl: float = 24 * 3600
    profile_id: int = 777
    children: list[dict] = field(default_factory=default_children)
    visits: dict[str, list[dict]] = field(default_factory=dict)
    visits_override: object = None
    api_status: int = 200
    api_headers: dict[str, str] = field(default_factory=dict)
    issued_tokens: list[str] = field(default_factory=list)
    revoked: set[str] = field(default_factory=set)
    requests: list[Recorded] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._record])
        app.router.add_get("/sps/oauth/ae", self._ae)
        app.router.add_get("/sps/login/methods/password", self._password_page)
        app.router.add_get("/sps/login/methods/qrCode/pull", self._pull)
        app.router.add_post("/sps/login/methods/headless/qrCode/refresh", self._qr_refresh)
        app.router.add_post("/sps/login/methods/headless/qrCode/complete", self._complete)
        app.router.add_post("/sps/login/methods/headless/sms/bind", self._sms_bind)
        app.router.add_get("/sps/login/ur/askToTrust", self._trust_page)
        app.router.add_post("/sps/login/ur/askToTrust", self._trust)
        app.router.add_get("/v3/auth/sudir/callback", self._callback)
        app.router.add_get("/v2/token/refresh", self._token_refresh)
        app.router.add_post("/api/ej/acl/v1/sessions", self._sessions)
        app.router.add_get("/api/family/mobile/v1/profile", self._profile)
        app.router.add_get("/api/pass/entrances/v1/visit_durations", self._visit_durations)
        return app

    @web.middleware
    async def _record(self, request: web.Request, handler):
        form = {}
        if request.method == "POST" and request.content_type == "application/x-www-form-urlencoded":
            form = {key: str(value) for key, value in (await request.post()).items()}
        self.requests.append(
            Recorded(request.method, request.path, CIMultiDict(request.headers), dict(request.query), form)
        )
        return await handler(request)

    def _issue(self) -> str:
        count = len(self.issued_tokens)
        token = make_jwt(time.time() + self.token_ttl + count, str(count))
        self.issued_tokens.append(token)
        return token

    def _authorized(self, request: web.Request) -> bool:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return token in self.issued_tokens and token not in self.revoked

    async def _ae(self, request: web.Request) -> web.Response:
        if request.query.get("display") == "script":
            items = [
                {"inquire": "login_with_password"},
                {"inquire": "show_qr_code", "link": f"{self.base_url}/qr/{len(self.requests)}", "expires": 180},
            ]
            return web.json_response({"items": items})
        if self.sso_alive and "Ltpatoken2" in request.cookies:
            raise web.HTTPFound("/v3/auth/sudir/callback?code=silent")
        raise web.HTTPSeeOther("/sps/login/methods/password")

    async def _password_page(self, request: web.Request) -> web.Response:
        return web.Response(text="<html>password</html>", content_type="text/html")

    async def _pull(self, request: web.Request) -> web.Response:
        command = self.qr_commands.pop(0) if len(self.qr_commands) > 1 else self.qr_commands[0]
        return web.json_response({"command": command})

    async def _qr_refresh(self, request: web.Request) -> web.Response:
        self.qr_refreshes += 1
        return web.json_response({"link": f"{self.base_url}/qr/refreshed-{self.qr_refreshes}", "expires": 180})

    async def _complete(self, request: web.Request) -> web.Response:
        if self.trusted_device:
            raise web.HTTPFound("/v3/auth/sudir/callback?code=qr")
        items = [{"inquire": "ask_to_send_sms"}, {"inquire": "login_with_flashcall"}, {"inquire": "go_to_web"}]
        return web.json_response({"inquire": "choose_one", "items": items})

    async def _sms_bind(self, request: web.Request) -> web.Response:
        form = await request.post()
        if "sms-code" not in form:
            return web.json_response({"inquire": "enter_sms_code", "contact": "7900****", "remain_attempts": 5, "ttl": 300})
        if form["sms-code"] == self.sms_code:
            raise web.HTTPSeeOther("/sps/login/ur/askToTrust")
        return web.json_response({"errors": [{"code": "invalid_otp"}], "remain_attempts": 4})

    async def _trust_page(self, request: web.Request) -> web.Response:
        html = (
            "<title>Доверять этому браузеру?</title>"
            '<form id="decision" action="/sps/login/ur/askToTrust" method="POST"><input name="action" value=""></form>'
        )
        return web.Response(text=html, content_type="text/html")

    async def _trust(self, request: web.Request) -> web.Response:
        if (await request.post()).get("action") != "trust":
            raise web.HTTPNotFound()
        raise web.HTTPFound("/v3/auth/sudir/callback?code=trusted")

    async def _callback(self, request: web.Request) -> web.Response:
        response = web.Response(text="ok")
        response.set_cookie("aupd_token", self._issue(), path="/")
        response.set_cookie("aupd_refresh_token", make_jwt(time.time() + 30 * 86400, "refresh"), path="/")
        response.set_cookie("Ltpatoken2", "sso-session", path="/")
        return response

    async def _token_refresh(self, request: web.Request) -> web.Response:
        current = request.cookies.get("aupd_token")
        if not current or "aupd_refresh_token" not in request.cookies:
            raise web.HTTPForbidden()
        return web.Response(status=201, text=self._issue() if self.refresh_works else current)

    async def _sessions(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"profiles": [{"id": self.profile_id, "type": "parent"}]})

    async def _profile(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"profile": {"id": self.profile_id, "type": "parent"}, "children": self.children})

    async def _visit_durations(self, request: web.Request) -> web.Response:
        if "Cookie" in request.headers or "Auth-Token" in request.headers:
            body = {"code": 401, "description": "Found multiple bearer tokens in the request"}
            return web.json_response(body, status=401)
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        if self.api_status != 200:
            return web.Response(status=self.api_status, headers=self.api_headers)
        if self.visits_override is not None:
            return web.json_response(self.visits_override)
        return web.json_response({"payload": self.visits.get(request.query["personId"], [])})
```

`tests/conftest.py`:

```python
import aiohttp
import pytest

from fake_mos import FakeMos


@pytest.fixture
async def fake_mos(aiohttp_server):
    fake = FakeMos()
    server = await aiohttp_server(fake.app())
    fake.base_url = str(server.make_url("")).rstrip("/")
    return fake


@pytest.fixture
async def api_http():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield session


@pytest.fixture
async def login_http():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        yield session
```

- [ ] **Step 2: Написать падающие тесты клиента**

`tests/test_mesh.py`:

```python
import time
from datetime import UTC, date, datetime

import pytest

from app.mesh import Child, MeshApiError, MeshAuthError, MeshClient, Visit
from app.tokens import jwt_exp
from fake_mos import make_jwt

CHILD = Child(101, "guid-101", "Иван", "3-А")
DAY = date(2026, 9, 16)
VISIT = {
    "in": "08:07",
    "out": "14:33",
    "duration": "6 ч.26 мин.",
    "personIn": None,
    "personOut": {"lastName": "Петрова", "firstName": "Анна", "middleName": None},
    "isIncomplete": False,
    "kindId": 1,
    "organizationId": 1234,
    "organizationShortName": "ГБОУ Школа № 1",
}


@pytest.fixture
def token(fake_mos):
    value = make_jwt(time.time() + 3600)
    fake_mos.issued_tokens.append(value)
    return value


@pytest.fixture
def mesh(fake_mos, api_http):
    return MeshClient(api_http, fake_mos.base_url)


def test_jwt_exp():
    assert jwt_exp(make_jwt(1_789_558_630)) == datetime.fromtimestamp(1_789_558_630, UTC)
    assert jwt_exp("not-a-jwt") is None


async def test_profile_id(mesh, token):
    assert await mesh.profile_id(token) == 777


async def test_children(mesh, token):
    assert await mesh.children(token, 777) == [CHILD]


async def test_visits_parsed(fake_mos, mesh, token):
    fake_mos.visits["guid-101"] = [{"date": "2026-09-16", "visits": [VISIT]}]
    visits = await mesh.visits(token, 777, CHILD, DAY)
    assert visits == [Visit(DAY, "08:07", "14:33", False, 1234, "ГБОУ Школа № 1", None, "Петрова Анна")]


async def test_visits_request_carries_only_bearer(fake_mos, mesh, token):
    await mesh.visits(token, 777, CHILD, DAY)
    request = fake_mos.requests[-1]
    assert request.path == "/api/pass/entrances/v1/visit_durations"
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert request.headers["X-Mes-Subsystem"] == "familymp"
    assert request.headers["client-type"] == "diary-mobile"
    assert request.headers["Profile-Id"] == "777"
    assert "Cookie" not in request.headers
    assert "Auth-Token" not in request.headers
    assert request.query == {"personId": "guid-101", "from": "2026-09-16", "to": "2026-09-16"}


async def test_unauthorized(mesh):
    with pytest.raises(MeshAuthError):
        await mesh.profile_id("unknown-token")


async def test_retry_after(fake_mos, mesh, token):
    fake_mos.api_status = 429
    fake_mos.api_headers = {"Retry-After": "120"}
    with pytest.raises(MeshApiError) as error:
        await mesh.visits(token, 777, CHILD, DAY)
    assert error.value.retry_after == 120


async def test_unexpected_payload(fake_mos, mesh, token):
    fake_mos.visits_override = {"unexpected": True}
    with pytest.raises(MeshApiError, match="unexpected"):
        await mesh.visits(token, 777, CHILD, DAY)
```

- [ ] **Step 2a: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_mesh.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mesh'`.

- [ ] **Step 3: Реализовать `tokens.py` и `mesh.py`**

`mesh_passes/app/tokens.py`:

```python
import base64
import json
from datetime import UTC, datetime


def jwt_exp(token: str) -> datetime | None:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (IndexError, ValueError, KeyError, TypeError):
        return None
```

`mesh_passes/app/mesh.py`:

```python
"""Клиент неофициального API МЭШ (school.mos.ru)."""

from dataclasses import dataclass
from datetime import date

import aiohttp

SCHOOL_URL = "https://school.mos.ru"
MOBILE_HEADERS = {"X-Mes-Subsystem": "familymp", "client-type": "diary-mobile"}


class MeshError(Exception):
    """Ошибка API МЭШ."""


class MeshAuthError(MeshError):
    """Токен не принят (401/403)."""


class MeshApiError(MeshError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Child:
    id: int
    guid: str
    first_name: str
    class_name: str


@dataclass(frozen=True)
class Visit:
    day: date
    time_in: str
    time_out: str
    incomplete: bool
    organization_id: int | None
    school: str | None
    person_in: str | None
    person_out: str | None


def _person(value: object) -> str | None:
    if isinstance(value, dict):
        parts = [value.get(key) for key in ("lastName", "firstName", "middleName")]
        return " ".join(str(part) for part in parts if part) or None
    return str(value) if value else None


def _keys(data: object) -> list[str]:
    return sorted(data) if isinstance(data, dict) else [type(data).__name__]


def _retry_after(response: aiohttp.ClientResponse) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class MeshClient:
    def __init__(self, http: aiohttp.ClientSession, base_url: str = SCHOOL_URL) -> None:
        self._http = http
        self._base = base_url

    async def _json(
        self,
        method: str,
        path: str,
        token: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> object:
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
        try:
            async with self._http.request(
                method, self._base + path, headers=request_headers, params=params, json=body
            ) as response:
                if response.status in (401, 403):
                    raise MeshAuthError(f"{path}: {response.status}")
                if response.status >= 400:
                    raise MeshApiError(f"{path}: {response.status}", _retry_after(response))
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MeshApiError(f"{path}: {type(err).__name__}") from err
        except ValueError as err:
            raise MeshApiError(f"{path}: ответ не JSON") from err

    async def profile_id(self, token: str) -> int:
        data = await self._json(
            "POST",
            "/api/ej/acl/v1/sessions",
            token,
            headers={"X-Mes-Subsystem": "familyweb"},
            body={"auth_token": token},
        )
        try:
            profile = next((item for item in data["profiles"] if item.get("type") == "parent"), None)
            return int(profile["id"])
        except (KeyError, TypeError, ValueError) as err:
            raise MeshApiError(f"sessions: нет профиля родителя; ключи {_keys(data)}") from err

    async def children(self, token: str, profile_id: int) -> list[Child]:
        data = await self._json(
            "GET",
            "/api/family/mobile/v1/profile",
            token,
            headers={**MOBILE_HEADERS, "profile-id": str(profile_id)},
        )
        try:
            return [
                Child(int(item["id"]), str(item["contingent_guid"]), str(item["first_name"]), str(item.get("class_name") or ""))
                for item in data["children"]
            ]
        except (KeyError, TypeError, ValueError) as err:
            raise MeshApiError(f"profile: неожиданный ответ; ключи {_keys(data)}") from err

    async def visits(self, token: str, profile_id: int, child: Child, day: date) -> list[Visit]:
        data = await self._json(
            "GET",
            "/api/pass/entrances/v1/visit_durations",
            token,
            headers={**MOBILE_HEADERS, "Profile-Id": str(profile_id)},
            params={"personId": child.guid, "from": day.isoformat(), "to": day.isoformat()},
        )
        try:
            return [
                Visit(
                    day=date.fromisoformat(item["date"]),
                    time_in=str(visit.get("in") or "-"),
                    time_out=str(visit.get("out") or "-"),
                    incomplete=bool(visit.get("isIncomplete")),
                    organization_id=visit.get("organizationId"),
                    school=visit.get("organizationShortName"),
                    person_in=_person(visit.get("personIn")),
                    person_out=_person(visit.get("personOut")),
                )
                for item in data["payload"]
                for visit in item["visits"]
            ]
        except (KeyError, TypeError, ValueError) as err:
            raise MeshApiError(f"visit_durations: неожиданный ответ; ключи {_keys(data)}") from err
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_mesh.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 8 passed; ruff чистый.

- [ ] **Step 5: Commit**

```bash
git add mesh_passes/app/tokens.py mesh_passes/app/mesh.py tests/fake_mos.py tests/conftest.py tests/test_mesh.py
git commit -m "feat: MESH API client with fake mos.ru server for tests" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Хранение сессии

**Files:**
- Create: `mesh_passes/app/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: `app.mesh.Child`
- Produces:
  - `SessionData(profile_id: int | None, children: list[Child], logged_in_at: float | None)` (dataclass)
  - `write_private_json(path: Path, data: object) -> None` — атомарно, права 0600
  - `save_session(path: Path, jar: aiohttp.CookieJar, data: SessionData) -> None`
  - `load_session(path: Path, jar: aiohttp.CookieJar) -> SessionData | None` — восстанавливает cookies в `jar`; `None`, если файла нет или он повреждён
  - `cookie_value(jar: aiohttp.CookieJar, name: str) -> str | None`
  - `set_cookie(jar: aiohttp.CookieJar, name: str, value: str, fallback_url: str) -> None` — заменяет cookie с тем же именем в том же домене

- [ ] **Step 1: Написать падающие тесты**

`tests/test_session_store.py`:

```python
from http.cookies import SimpleCookie

import aiohttp
from yarl import URL

from app.mesh import Child
from app.session_store import SessionData, cookie_value, load_session, save_session, set_cookie


def cookie(name: str, value: str, domain: str | None = None) -> SimpleCookie:
    result = SimpleCookie()
    result[name] = value
    result[name]["path"] = "/"
    if domain:
        result[name]["domain"] = domain
    return result


async def test_round_trip_keeps_domains_and_profile(tmp_path):
    path = tmp_path / "session.json"
    jar = aiohttp.CookieJar()
    jar.update_cookies(cookie("aupd_token", "tok", ".mos.ru"), URL("https://school.mos.ru/"))
    jar.update_cookies(cookie("trust_marker", "yes"), URL("https://login.mos.ru/"))
    data = SessionData(777, [Child(101, "guid-101", "Иван", "3-А")], 1_789_558_630.0)

    save_session(path, jar, data)
    assert path.stat().st_mode & 0o777 == 0o600

    restored_jar = aiohttp.CookieJar()
    assert load_session(path, restored_jar) == data
    assert restored_jar.filter_cookies(URL("https://school.mos.ru/api"))["aupd_token"].value == "tok"
    assert restored_jar.filter_cookies(URL("https://login.mos.ru/sps"))["aupd_token"].value == "tok"
    assert "trust_marker" in restored_jar.filter_cookies(URL("https://login.mos.ru/sps"))
    assert "trust_marker" not in restored_jar.filter_cookies(URL("https://school.mos.ru/"))


async def test_missing_or_broken_file(tmp_path):
    jar = aiohttp.CookieJar()
    assert load_session(tmp_path / "absent.json", jar) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_session(broken, jar) is None


async def test_set_cookie_replaces_value_in_same_domain():
    jar = aiohttp.CookieJar()
    jar.update_cookies(cookie("aupd_token", "old", ".mos.ru"), URL("https://school.mos.ru/"))
    set_cookie(jar, "aupd_token", "new", "https://school.mos.ru")
    assert [morsel.value for morsel in jar if morsel.key == "aupd_token"] == ["new"]
    assert cookie_value(jar, "aupd_token") == "new"
    assert cookie_value(jar, "missing") is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_session_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.session_store'`.

- [ ] **Step 3: Реализовать хранилище**

`mesh_passes/app/session_store.py`:

```python
import json
import os
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from pathlib import Path

import aiohttp
from yarl import URL

from app.mesh import Child


@dataclass
class SessionData:
    profile_id: int | None
    children: list[Child]
    logged_in_at: float | None


def write_private_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(tmp, path)


def _add_cookie(jar: aiohttp.CookieJar, name: str, value: str, domain: str, path: str, expires: str) -> None:
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    morsel["path"] = path or "/"
    morsel["domain"] = domain
    if expires:
        morsel["expires"] = expires
    jar.update_cookies(cookie, URL(f"https://{domain.lstrip('.')}/"))


def save_session(path: Path, jar: aiohttp.CookieJar, data: SessionData) -> None:
    cookies = [
        {"name": m.key, "value": m.value, "domain": m["domain"], "path": m["path"], "expires": m["expires"]}
        for m in jar
        if m["domain"]
    ]
    write_private_json(
        path,
        {
            "cookies": cookies,
            "profile_id": data.profile_id,
            "children": [asdict(child) for child in data.children],
            "logged_in_at": data.logged_in_at,
        },
    )


def load_session(path: Path, jar: aiohttp.CookieJar) -> SessionData | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw.get("cookies", []):
            _add_cookie(jar, item["name"], item["value"], item["domain"], item.get("path", "/"), item.get("expires", ""))
        return SessionData(
            raw.get("profile_id"),
            [Child(**child) for child in raw.get("children", [])],
            raw.get("logged_in_at"),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def cookie_value(jar: aiohttp.CookieJar, name: str) -> str | None:
    return next((morsel.value for morsel in jar if morsel.key == name), None)


def set_cookie(jar: aiohttp.CookieJar, name: str, value: str, fallback_url: str) -> None:
    existing = next((morsel for morsel in jar if morsel.key == name), None)
    if existing is not None and existing["domain"]:
        _add_cookie(jar, name, value, existing["domain"], existing["path"] or "/", "")
        return
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    jar.update_cookies(cookie, URL(fallback_url))
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_session_store.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 3 passed; ruff чистый.

- [ ] **Step 5: Commit**

```bash
git add mesh_passes/app/session_store.py tests/test_session_store.py
git commit -m "feat: persistent mos.ru session store" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Вход по QR, SMS и «доверию устройству»

**Files:**
- Create: `mesh_passes/app/auth.py`, `tests/helpers.py`
- Modify: `tests/conftest.py` (фикстура `auth`)
- Test: `tests/test_auth_login.py`

**Interfaces:**
- Consumes: `MeshClient.profile_id`, `MeshClient.children`, `MeshError` (Task 2); `SessionData`, `save_session`, `load_session`, `cookie_value` (Task 3); `jwt_exp` (Task 2)
- Produces:
  - `app.auth.LOGIN_URL = "https://login.mos.ru"`, `SCHOOL_URL = "https://school.mos.ru"`, `SESSION_COOKIES`
  - `app.auth.LoginState(StrEnum)`: `LOGGED_OUT="logged_out"`, `QR="qr"`, `CONFIRM="confirm"`, `SMS="sms"`, `LOGGED_IN="logged_in"`, `AUTH_REQUIRED="auth_required"`
  - `app.auth.NotLoggedIn(Exception)`
  - `app.auth.SmsPrompt(deadline: float, attempts: int | None)`
  - `app.auth.ae_params(*, script: bool, school_url: str) -> dict[str, str]`
  - `app.auth.Auth(http, mesh, session_path, *, login_url=LOGIN_URL, school_url=SCHOOL_URL, qr_poll_seconds=3.0, qr_timeout_seconds=600.0, clock=time.time)`
    - атрибуты: `state: LoginState`, `busy: bool`, `error: str | None`, `qr_svg: str | None`, `sms: SmsPrompt | None`, `profile_id: int | None`, `children: list[Child]`, `logged_in_at: float | None`, `last_renewal: float | None`, `on_login: Callable[[], None] | None`
    - методы: `load() -> None`, `token_expires() -> datetime | None`, `async start_login()`, `async submit_sms(code: str)`, `async resend_sms()`, `async logout()`, `async close()`
  - `tests/helpers.py`: `async wait_for(predicate: Callable[[], object], timeout: float = 2.0) -> None`
  - фикстура `auth` (qr_poll_seconds=0.01, qr_timeout_seconds=1.0)

- [ ] **Step 1: Добавить помощник и фикстуру**

`tests/helpers.py`:

```python
import asyncio
from collections.abc import Callable


async def wait_for(predicate: Callable[[], object], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("условие не выполнилось вовремя")
        await asyncio.sleep(0.01)
```

В `tests/conftest.py` добавить импорты и фикстуру:

```python
from app.auth import Auth
from app.mesh import MeshClient


@pytest.fixture
async def auth(fake_mos, login_http, api_http, tmp_path):
    instance = Auth(
        login_http,
        MeshClient(api_http, fake_mos.base_url),
        tmp_path / "session.json",
        login_url=fake_mos.base_url,
        school_url=fake_mos.base_url,
        qr_poll_seconds=0.01,
        qr_timeout_seconds=1.0,
    )
    yield instance
    await instance.close()
```

- [ ] **Step 2: Написать падающие тесты входа**

`tests/test_auth_login.py`:

```python
import aiohttp

from app.auth import Auth, LoginState
from app.mesh import Child, MeshClient
from helpers import wait_for


async def test_trusted_device_logs_in_after_qr(fake_mos, auth, tmp_path):
    fake_mos.trusted_device = True
    logins = []
    auth.on_login = lambda: logins.append(True)

    await auth.start_login()
    assert auth.state is LoginState.QR
    assert auth.qr_svg.startswith("<svg")

    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)
    assert auth.profile_id == 777
    assert auth.children == [Child(101, "guid-101", "Иван", "3-А")]
    assert auth.qr_svg is None
    assert logins == [True]
    assert (tmp_path / "session.json").stat().st_mode & 0o777 == 0o600


async def test_new_device_requires_sms_then_trust(fake_mos, auth):
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.SMS)
    assert auth.sms.attempts == 5

    await auth.submit_sms("123456")

    assert auth.state is LoginState.LOGGED_IN
    trust = [r for r in fake_mos.requests if r.path == "/sps/login/ur/askToTrust" and r.method == "POST"]
    assert [r.form for r in trust] == [{"action": "trust"}]


async def test_wrong_sms_code_keeps_prompt(auth):
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.SMS)

    await auth.submit_sms("000000")

    assert auth.state is LoginState.SMS
    assert auth.error == "Неверный код"
    assert auth.sms.attempts == 4


async def test_resend_sms(fake_mos, auth):
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.SMS)
    before = len([r for r in fake_mos.requests if r.path.endswith("/sms/bind")])

    await auth.resend_sms()

    assert len([r for r in fake_mos.requests if r.path.endswith("/sms/bind")]) == before + 1
    assert auth.state is LoginState.SMS


async def test_expired_qr_is_refreshed(fake_mos, auth):
    fake_mos.trusted_device = True
    fake_mos.qr_commands = ["showQRCode", "needRefresh", "showQRCode", "needComplete"]

    await auth.start_login()

    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)
    assert fake_mos.qr_refreshes == 1


async def test_qr_timeout_returns_to_logged_out(fake_mos, auth):
    fake_mos.qr_commands = ["showQRCode"]

    await auth.start_login()

    await wait_for(lambda: auth.state is LoginState.LOGGED_OUT and auth.error, timeout=3)
    assert "10 минут" in auth.error


async def test_session_restored_from_disk(fake_mos, auth, api_http, tmp_path):
    fake_mos.trusted_device = True
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        restored = Auth(
            http,
            MeshClient(api_http, fake_mos.base_url),
            tmp_path / "session.json",
            login_url=fake_mos.base_url,
            school_url=fake_mos.base_url,
        )
        restored.load()
        assert restored.state is LoginState.LOGGED_IN
        assert restored.children == auth.children
        assert restored.token_expires() == auth.token_expires()


async def test_logout_removes_session(fake_mos, auth, tmp_path):
    fake_mos.trusted_device = True
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)

    await auth.logout()

    assert auth.state is LoginState.AUTH_REQUIRED
    assert auth.token_expires() is None
    assert not (tmp_path / "session.json").exists()
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_auth_login.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 4: Реализовать вход**

`mesh_passes/app/auth.py`:

```python
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
```

- [ ] **Step 5: Прогнать тесты и линт**

Run: `uv run pytest tests/test_auth_login.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 8 passed; ruff чистый.

- [ ] **Step 6: Commit**

```bash
git add mesh_passes/app/auth.py tests/helpers.py tests/conftest.py tests/test_auth_login.py
git commit -m "feat: mos.ru login via QR code, SMS and device trust" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Продление токена

**Files:**
- Modify: `mesh_passes/app/auth.py`, `tests/conftest.py` (фикстура `logged_in_auth`)
- Test: `tests/test_auth_renew.py`

**Interfaces:**
- Consumes: `Auth` (Task 4), `set_cookie` (Task 3), `jwt_exp` (Task 2)
- Produces (новые методы `Auth`):
  - `async token() -> str` — действующий токен; продлевает, если до `exp` < 30 мин; иначе `NotLoggedIn`
  - `async renew() -> str` — принудительное продление по цепочке SSO → refresh; при неудаче `mark_expired()` и `NotLoggedIn`
  - `mark_expired() -> None` — `LOGGED_IN` → `AUTH_REQUIRED` с текстом «Сессия mos.ru истекла — войдите заново»
  - `async refresh_children() -> list[Child]`
  - фикстура `logged_in_auth` (доверенное устройство, вход выполнен)

- [ ] **Step 1: Добавить фикстуру**

В `tests/conftest.py` добавить импорты `from app.auth import Auth, LoginState` (заменить прежний импорт `Auth`), `from helpers import wait_for` и фикстуру:

```python
@pytest.fixture
async def logged_in_auth(fake_mos, auth):
    fake_mos.trusted_device = True
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)
    return auth
```

- [ ] **Step 2: Написать падающие тесты**

`tests/test_auth_renew.py`:

```python
import pytest

from app.auth import LoginState, NotLoggedIn
from helpers import wait_for


async def test_fresh_token_is_returned_as_is(fake_mos, logged_in_auth):
    assert await logged_in_auth.token() == fake_mos.issued_tokens[-1]
    assert len(fake_mos.issued_tokens) == 1
    assert logged_in_auth.last_renewal is None


async def test_token_near_expiry_renewed_via_sso(fake_mos, auth):
    fake_mos.token_ttl = 600
    fake_mos.trusted_device = True
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)

    token = await auth.token()

    assert len(fake_mos.issued_tokens) == 2
    assert token == fake_mos.issued_tokens[-1]
    assert auth.last_renewal is not None


async def test_dead_sso_falls_back_to_refresh_token(fake_mos, logged_in_auth):
    old = await logged_in_auth.token()
    fake_mos.sso_alive = False
    fake_mos.refresh_works = True

    token = await logged_in_auth.renew()

    assert token != old
    assert token == fake_mos.issued_tokens[-1]


async def test_no_renewal_path_requires_login(fake_mos, logged_in_auth):
    fake_mos.sso_alive = False

    with pytest.raises(NotLoggedIn):
        await logged_in_auth.renew()

    assert logged_in_auth.state is LoginState.AUTH_REQUIRED
    assert "войдите заново" in logged_in_auth.error
    with pytest.raises(NotLoggedIn):
        await logged_in_auth.token()


async def test_refresh_children(fake_mos, logged_in_auth):
    fake_mos.children.append({"id": 102, "contingent_guid": "guid-102", "first_name": "Мария", "class_name": "1-А"})

    children = await logged_in_auth.refresh_children()

    assert [child.id for child in children] == [101, 102]
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_auth_renew.py -v`
Expected: FAIL — `AttributeError: 'Auth' object has no attribute 'token'`.

- [ ] **Step 4: Реализовать продление**

В `mesh_passes/app/auth.py`:

Импорты: `from datetime import UTC, datetime, timedelta`; `from app.session_store import SessionData, cookie_value, load_session, save_session, set_cookie`. Константа после `LOGIN_ERRORS`:

```python
RENEW_BEFORE = timedelta(minutes=30)
```

Методы класса `Auth` (после `token_expires`):

```python
    async def token(self) -> str:
        token = cookie_value(self._jar, "aupd_token")
        if self.state is not LoginState.LOGGED_IN or token is None:
            raise NotLoggedIn
        expires = jwt_exp(token)
        if expires is not None and expires - datetime.fromtimestamp(self._clock(), UTC) > RENEW_BEFORE:
            return token
        return await self.renew()

    async def renew(self) -> str:
        if self.state is not LoginState.LOGGED_IN:
            raise NotLoggedIn
        old = cookie_value(self._jar, "aupd_token")
        if await self._renew_sso(old) or await self._renew_refresh(old):
            self.last_renewal = self._clock()
            self._save()
            _LOGGER.info("Токен МЭШ продлён")
            return cookie_value(self._jar, "aupd_token")
        self.mark_expired()
        raise NotLoggedIn

    def mark_expired(self) -> None:
        if self.state is LoginState.LOGGED_IN:
            _LOGGER.warning("Сессия mos.ru истекла, нужен повторный вход")
            self.state = LoginState.AUTH_REQUIRED
            self.error = "Сессия mos.ru истекла — войдите заново"

    async def refresh_children(self) -> list[Child]:
        token = await self.token()
        self.children = await self._mesh.children(token, self.profile_id)
        self._save()
        return self.children

    async def _renew_sso(self, old: str | None) -> bool:
        params = ae_params(script=False, school_url=self._school)
        try:
            async with self._http.get(f"{self._login}/sps/oauth/ae", params=params) as response:
                await response.read()
        except (aiohttp.ClientError, TimeoutError):
            return False
        new = cookie_value(self._jar, "aupd_token")
        return new is not None and new != old

    async def _renew_refresh(self, old: str | None) -> bool:
        url = f"{self._school}/v2/token/refresh"
        try:
            async with self._http.get(url, params={"roleId": "2", "subsystem": "2"}, allow_redirects=False) as response:
                body = (await response.text()).strip().strip('"')
                ok = response.status in (200, 201)
        except (aiohttp.ClientError, TimeoutError):
            return False
        new_exp, old_exp = jwt_exp(body), jwt_exp(old or "")
        if not ok or new_exp is None or body == old or (old_exp is not None and new_exp <= old_exp):
            return False
        set_cookie(self._jar, "aupd_token", body, self._school)
        return True
```

- [ ] **Step 5: Прогнать все тесты и линт**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: все тесты зелёные (включая Task 1–4); ruff чистый.

- [ ] **Step 6: Commit**

```bash
git add mesh_passes/app/auth.py tests/conftest.py tests/test_auth_renew.py
git commit -m "feat: renew MESH token via SSO and refresh token" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: События прохода, состояние ребёнка, окно опроса

**Files:**
- Create: `mesh_passes/app/events.py`, `mesh_passes/app/schedule.py`
- Test: `tests/test_events.py`, `tests/test_schedule.py`

**Interfaces:**
- Consumes: `app.mesh.Visit` (Task 2), `app.settings.Settings`, `build_settings` (Task 1)
- Produces:
  - `app.events.MSK = ZoneInfo("Europe/Moscow")`
  - `PassEvent(id: str, child_id: int, kind: Literal["entry", "exit"], at: datetime, school: str | None, person: str | None)` (frozen)
  - `ChildState(at_school: bool, last_entry: datetime | None, last_exit: datetime | None, school: str | None, visits: list[dict[str, str]])` (frozen)
  - `visit_events(child_id: int, visits: list[Visit]) -> list[PassEvent]` — по времени; ID `"{date}|{organization_id or '-'}|in|{HH:MM}"` / `"...|out|{HH:MM}"`
  - `child_state(visits: list[Visit], last_entry: datetime | None, last_exit: datetime | None) -> ChildState`
  - `app.schedule.in_window(now: datetime, settings: Settings) -> bool`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_events.py`:

```python
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
```

`tests/test_schedule.py`:

```python
from datetime import UTC, datetime

import pytest

from app.events import MSK
from app.schedule import in_window
from app.settings import build_settings

SETTINGS = build_settings({}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 16, 7, 0, tzinfo=MSK), True),
        (datetime(2026, 9, 16, 19, 59, tzinfo=MSK), True),
        (datetime(2026, 9, 16, 20, 0, tzinfo=MSK), False),
        (datetime(2026, 9, 16, 6, 59, tzinfo=MSK), False),
        (datetime(2026, 9, 20, 12, 0, tzinfo=MSK), False),
        (datetime(2026, 9, 16, 4, 0, tzinfo=UTC), True),
    ],
)
def test_in_window(now, expected):
    assert in_window(now, SETTINGS) is expected
```

(2026-09-16 — среда, 2026-09-20 — воскресенье; 04:00 UTC = 07:00 МСК.)

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_events.py tests/test_schedule.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.events'`.

- [ ] **Step 3: Реализовать**

`mesh_passes/app/events.py`:

```python
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from app.mesh import Visit

MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class PassEvent:
    id: str
    child_id: int
    kind: Literal["entry", "exit"]
    at: datetime
    school: str | None
    person: str | None


@dataclass(frozen=True)
class ChildState:
    at_school: bool
    last_entry: datetime | None
    last_exit: datetime | None
    school: str | None
    visits: list[dict[str, str]]


def _at(day: date, hhmm: str) -> datetime | None:
    try:
        hours, minutes = hhmm.split(":")
        return datetime.combine(day, time(int(hours), int(minutes)), MSK)
    except ValueError:
        return None


def _last_activity(visit: Visit) -> str:
    return max(value for value in (visit.time_in, visit.time_out, "") if value != "-")


def visit_events(child_id: int, visits: list[Visit]) -> list[PassEvent]:
    events = []
    for visit in visits:
        prefix = f"{visit.day.isoformat()}|{visit.organization_id if visit.organization_id is not None else '-'}"
        entry_at = _at(visit.day, visit.time_in)
        if entry_at:
            events.append(
                PassEvent(f"{prefix}|in|{visit.time_in}", child_id, "entry", entry_at, visit.school, visit.person_in)
            )
        exit_at = _at(visit.day, visit.time_out)
        if exit_at and not visit.incomplete:
            events.append(
                PassEvent(f"{prefix}|out|{visit.time_out}", child_id, "exit", exit_at, visit.school, visit.person_out)
            )
    return sorted(events, key=lambda event: event.at)


def child_state(visits: list[Visit], last_entry: datetime | None, last_exit: datetime | None) -> ChildState:
    events = visit_events(0, visits)
    ordered = sorted(visits, key=_last_activity)
    current = ordered[-1] if ordered else None
    return ChildState(
        at_school=bool(current and (current.incomplete or _at(current.day, current.time_out) is None)),
        last_entry=max((e.at for e in events if e.kind == "entry"), default=last_entry),
        last_exit=max((e.at for e in events if e.kind == "exit"), default=last_exit),
        school=next((v.school for v in reversed(ordered) if v.school), None),
        visits=[{"in": v.time_in, "out": v.time_out} for v in ordered],
    )
```

`mesh_passes/app/schedule.py`:

```python
from datetime import datetime

from app.events import MSK
from app.settings import Settings


def in_window(now: datetime, settings: Settings) -> bool:
    local = now.astimezone(MSK)
    return local.weekday() in settings.active_days and settings.active_from <= local.time() < settings.active_to
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_events.py tests/test_schedule.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 14 passed; ruff чистый.

- [ ] **Step 5: Commit**

```bash
git add mesh_passes/app/events.py mesh_passes/app/schedule.py tests/test_events.py tests/test_schedule.py
git commit -m "feat: pass events, child state and polling window" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Цикл опроса

**Files:**
- Create: `mesh_passes/app/poller_state.py`, `mesh_passes/app/poller.py`
- Test: `tests/test_poller_state.py`, `tests/test_poller.py`

**Interfaces:**
- Consumes: `Auth.token/renew/mark_expired/refresh_children/state/profile_id/children/token_expires` (Task 4–5), `NotLoggedIn`, `LoginState`; `MeshClient.visits`, `MeshApiError`, `MeshAuthError`, `Child`, `Visit` (Task 2); `visit_events`, `child_state`, `ChildState`, `PassEvent`, `MSK`, `in_window` (Task 6); `Settings` (Task 1); `write_private_json` (Task 3)
- Produces:
  - `PollerState(seen: dict[str, str], last: dict[str, dict[str, str | None]])` с `load(path) -> PollerState` (classmethod), `save(path)`, `take_new(events, now, max_age: timedelta) -> list[PassEvent]`, `last_times(child_id) -> tuple[datetime | None, datetime | None]`, `set_last_times(child_id, last_entry, last_exit)`, `prune(today: date)`
  - `app.poller.Publisher` (Protocol): `async publish_account(profile_id: int, status: str, last_poll: datetime | None, token_expires: datetime | None)`, `async publish_children(profile_id: int, children: list[Child])`, `async publish_child_state(profile_id: int, child: Child, state: ChildState)`, `async publish_event(profile_id: int, child: Child, event: PassEvent)`
  - `app.poller.Poller(auth, mesh, publisher, settings, state_path, clock=lambda: datetime.now(MSK))` с `request_poll() -> None`, `async run() -> None`, `async poll_once() -> float` (секунды до следующего цикла); атрибуты `status: str`, `last_poll: datetime | None`, `child_states: dict[int, ChildState]`

- [ ] **Step 1: Написать падающие тесты состояния**

`tests/test_poller_state.py`:

```python
from datetime import date, datetime, timedelta

from app.events import MSK, PassEvent
from app.poller_state import PollerState

NOW = datetime(2026, 9, 16, 14, 35, tzinfo=MSK)


def event(event_id, at):
    return PassEvent(event_id, 101, "exit", at, "ГБОУ Школа № 1", None)


def test_take_new_marks_seen_and_skips_old():
    state = PollerState()
    fresh, old = event("fresh", NOW - timedelta(minutes=2)), event("old", NOW - timedelta(hours=2))

    assert state.take_new([fresh, old], NOW, timedelta(minutes=30)) == [fresh]
    assert state.take_new([fresh, old], NOW, timedelta(minutes=30)) == []
    assert set(state.seen) == {"fresh", "old"}


def test_prune_keeps_three_days():
    state = PollerState(seen={"old": "2026-09-12", "edge": "2026-09-13", "new": "2026-09-16"})
    state.prune(date(2026, 9, 16))
    assert set(state.seen) == {"edge", "new"}


def test_save_and_load(tmp_path):
    path = tmp_path / "state.json"
    entry = datetime(2026, 9, 16, 8, 7, tzinfo=MSK)
    state = PollerState()
    state.seen["x"] = "2026-09-16"
    state.set_last_times(101, entry, None)
    state.save(path)

    loaded = PollerState.load(path)
    assert loaded.seen == {"x": "2026-09-16"}
    assert loaded.last_times(101) == (entry, None)
    assert loaded.last_times(999) == (None, None)
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_broken_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    assert PollerState.load(path) == PollerState()
```

- [ ] **Step 2: Написать падающие тесты цикла**

`tests/test_poller.py`:

```python
import asyncio
import contextlib
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
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_poller_state.py tests/test_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.poller_state'`.

- [ ] **Step 4: Реализовать состояние опроса**

`mesh_passes/app/poller_state.py`:

```python
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from app.events import PassEvent
from app.session_store import write_private_json

KEEP_DAYS = 3


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _format(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class PollerState:
    seen: dict[str, str] = field(default_factory=dict)
    last: dict[str, dict[str, str | None]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PollerState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(dict(raw.get("seen", {})), dict(raw.get("last", {})))
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        write_private_json(path, {"seen": self.seen, "last": self.last})

    def take_new(self, events: list[PassEvent], now: datetime, max_age: timedelta) -> list[PassEvent]:
        fresh = []
        for event in events:
            if event.id in self.seen:
                continue
            self.seen[event.id] = event.at.date().isoformat()
            if now - event.at <= max_age:
                fresh.append(event)
        return fresh

    def last_times(self, child_id: int) -> tuple[datetime | None, datetime | None]:
        item = self.last.get(str(child_id), {})
        return _parse(item.get("last_entry")), _parse(item.get("last_exit"))

    def set_last_times(self, child_id: int, last_entry: datetime | None, last_exit: datetime | None) -> None:
        self.last[str(child_id)] = {"last_entry": _format(last_entry), "last_exit": _format(last_exit)}

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.seen = {event_id: day for event_id, day in self.seen.items() if day >= cutoff}
```

- [ ] **Step 5: Реализовать цикл опроса**

`mesh_passes/app/poller.py`:

```python
import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.auth import Auth, LoginState, NotLoggedIn
from app.events import MSK, ChildState, PassEvent, child_state, visit_events
from app.mesh import Child, MeshApiError, MeshAuthError, MeshClient
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

    async def publish_event(self, profile_id: int, child: Child, event: PassEvent) -> None: ...


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

    def request_poll(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        delay: float = 0
        while True:
            forced = await self._sleep(delay)
            if forced or in_window(self._clock(), self._settings):
                delay = await self.poll_once()
            else:
                await self._set_status("outside_hours")
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
        for event in fresh:
            _LOGGER.info("Проход: %s, ребёнок %d", event.kind, child.id)
            await self._publisher.publish_event(self._auth.profile_id, child, event)

    async def _set_status(self, status: str) -> None:
        self.status = status
        if self._auth.profile_id is not None:
            await self._publisher.publish_account(
                self._auth.profile_id, status, self.last_poll, self._auth.token_expires()
            )
```

- [ ] **Step 6: Прогнать тесты и линт**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: все тесты зелёные; ruff чистый.

- [ ] **Step 7: Commit**

```bash
git add mesh_passes/app/poller_state.py mesh_passes/app/poller.py tests/test_poller_state.py tests/test_poller.py
git commit -m "feat: polling loop with event deduplication and backoff" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: MQTT discovery и публикация

**Files:**
- Create: `mesh_passes/app/discovery.py`, `mesh_passes/app/publisher.py`
- Test: `tests/test_discovery.py`, `tests/test_publisher.py`

**Interfaces:**
- Consumes: `app.VERSION` (Task 1), `Child` (Task 2), `ChildState`, `PassEvent`, `MSK` (Task 6)
- Produces:
  - `app.discovery`: `BASE = "mesh_passes"`, `AVAILABILITY_TOPIC = "mesh_passes/availability"`, `STATUSES`, `REPO_URL`, `slugify(name: str) -> str`, `child_slugs(children: list[Child]) -> dict[int, str]`, `account_topic(profile_id: int, key: str) -> str`, `child_topic(profile_id: int, child_id: int, key: str) -> str`, `account_discovery(prefix: str, profile_id: int) -> tuple[str, dict]`, `child_discovery(prefix: str, profile_id: int, child: Child, slug: str) -> tuple[str, dict]`
  - `app.publisher.MqttPublisher(prefix: str)` — реализует `app.poller.Publisher`; плюс `async attach(client)`, `detach()`, `async republish()`. `client` — любой объект с `async publish(topic, payload, qos=0, retain=False)` (`aiomqtt.Client`)

- [ ] **Step 1: Написать падающие тесты discovery**

`tests/test_discovery.py`:

```python
from app.discovery import account_discovery, child_discovery, child_slugs, slugify
from app.mesh import Child

IVAN = Child(101, "guid-101", "Иван", "3-А")


def test_slugify():
    assert slugify("Иван") == "ivan"
    assert slugify("Мария-Ёлка") == "mariya_yolka"
    assert slugify("") == "child"


def test_child_slugs_resolve_collisions():
    twins = [IVAN, Child(102, "guid-102", "Иван", "1-А"), Child(103, "guid-103", "Мария", "2-В")]
    assert child_slugs(twins) == {101: "ivan_101", 102: "ivan_102", 103: "mariya"}


def test_account_discovery():
    topic, config = account_discovery("homeassistant", 777)
    assert topic == "homeassistant/device/mesh_passes_777/config"
    assert config["device"]["identifiers"] == ["mesh_passes_777"]
    assert config["origin"]["name"] == "ha-mesh-passes"
    assert config["availability_topic"] == "mesh_passes/availability"
    components = config["components"]
    assert set(components) == {"status", "last_poll", "token_expires", "poll_now"}
    assert components["status"]["platform"] == "sensor"
    assert components["status"]["device_class"] == "enum"
    assert components["status"]["options"] == ["ok", "auth_required", "api_error", "outside_hours"]
    assert components["status"]["state_topic"] == "mesh_passes/777/status"
    assert components["status"]["default_entity_id"] == "sensor.mesh_account_status"
    assert components["last_poll"]["device_class"] == "timestamp"
    assert components["token_expires"]["entity_category"] == "diagnostic"
    assert components["poll_now"]["platform"] == "button"
    assert components["poll_now"]["command_topic"] == "mesh_passes/777/poll_now/set"


def test_child_discovery():
    topic, config = child_discovery("homeassistant", 777, IVAN, "ivan")
    assert topic == "homeassistant/device/mesh_passes_777_101/config"
    assert config["device"]["name"] == "Иван (3-А)"
    assert config["device"]["via_device"] == "mesh_passes_777"
    components = config["components"]
    assert components["at_school"]["platform"] == "binary_sensor"
    assert components["at_school"]["device_class"] == "presence"
    assert components["at_school"]["state_topic"] == "mesh_passes/777/child/101/state"
    assert components["at_school"]["default_entity_id"] == "binary_sensor.mesh_ivan_at_school"
    assert components["last_entry"]["value_template"] == "{{ value_json.last_entry }}"
    assert components["last_exit"]["default_entity_id"] == "sensor.mesh_ivan_last_exit"
    assert components["pass"]["platform"] == "event"
    assert components["pass"]["event_types"] == ["entry", "exit"]
    assert components["pass"]["state_topic"] == "mesh_passes/777/child/101/event"
    assert components["pass"]["default_entity_id"] == "event.mesh_ivan_pass"
```

- [ ] **Step 2: Написать падающие тесты публикации**

`tests/test_publisher.py`:

```python
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
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_discovery.py tests/test_publisher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.discovery'`.

- [ ] **Step 4: Реализовать discovery**

`mesh_passes/app/discovery.py`:

```python
import re
from collections import Counter

from app import VERSION
from app.mesh import Child

BASE = "mesh_passes"
AVAILABILITY_TOPIC = f"{BASE}/availability"
STATUSES = ["ok", "auth_required", "api_error", "outside_hours"]
REPO_URL = "https://github.com/freemandigger/ha-mesh-passes"
TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
        "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)  # fmt: skip


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().translate(TRANSLIT)).strip("_") or "child"


def child_slugs(children: list[Child]) -> dict[int, str]:
    slugs = {child.id: slugify(child.first_name) for child in children}
    counts = Counter(slugs.values())
    return {child_id: slug if counts[slug] == 1 else f"{slug}_{child_id}" for child_id, slug in slugs.items()}


def account_topic(profile_id: int, key: str) -> str:
    return f"{BASE}/{profile_id}/{key}"


def child_topic(profile_id: int, child_id: int, key: str) -> str:
    return f"{BASE}/{profile_id}/child/{child_id}/{key}"


def _origin() -> dict[str, str]:
    return {"name": "ha-mesh-passes", "sw_version": VERSION, "support_url": REPO_URL}


def account_discovery(prefix: str, profile_id: int) -> tuple[str, dict]:
    uid = f"{BASE}_{profile_id}"
    config = {
        "device": {
            "identifiers": [uid],
            "name": "МЭШ — учётная запись",
            "manufacturer": "ha-mesh-passes",
            "sw_version": VERSION,
        },
        "origin": _origin(),
        "availability_topic": AVAILABILITY_TOPIC,
        "components": {
            "status": {
                "platform": "sensor",
                "unique_id": f"{uid}_status",
                "name": "Статус",
                "device_class": "enum",
                "options": STATUSES,
                "icon": "mdi:school",
                "state_topic": account_topic(profile_id, "status"),
                "default_entity_id": "sensor.mesh_account_status",
            },
            "last_poll": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_poll",
                "name": "Последний успешный опрос",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "state_topic": account_topic(profile_id, "last_poll"),
                "default_entity_id": "sensor.mesh_account_last_poll",
            },
            "token_expires": {
                "platform": "sensor",
                "unique_id": f"{uid}_token_expires",
                "name": "Токен действует до",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "state_topic": account_topic(profile_id, "token_expires"),
                "default_entity_id": "sensor.mesh_account_token_expires",
            },
            "poll_now": {
                "platform": "button",
                "unique_id": f"{uid}_poll_now",
                "name": "Проверить сейчас",
                "command_topic": account_topic(profile_id, "poll_now/set"),
                "payload_press": "PRESS",
                "default_entity_id": "button.mesh_account_poll_now",
            },
        },
    }
    return f"{prefix}/device/{uid}/config", config


def child_discovery(prefix: str, profile_id: int, child: Child, slug: str) -> tuple[str, dict]:
    uid = f"{BASE}_{profile_id}_{child.id}"
    state_topic = child_topic(profile_id, child.id, "state")
    config = {
        "device": {
            "identifiers": [uid],
            "name": f"{child.first_name} ({child.class_name})" if child.class_name else child.first_name,
            "manufacturer": "ha-mesh-passes",
            "via_device": f"{BASE}_{profile_id}",
        },
        "origin": _origin(),
        "availability_topic": AVAILABILITY_TOPIC,
        "components": {
            "at_school": {
                "platform": "binary_sensor",
                "unique_id": f"{uid}_at_school",
                "name": "В школе",
                "device_class": "presence",
                "state_topic": state_topic,
                "value_template": "{{ value_json.at_school }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "json_attributes_topic": state_topic,
                "json_attributes_template": "{{ {'school': value_json.school, 'visits': value_json.visits} | tojson }}",
                "default_entity_id": f"binary_sensor.mesh_{slug}_at_school",
            },
            "last_entry": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_entry",
                "name": "Последний вход",
                "device_class": "timestamp",
                "icon": "mdi:login",
                "state_topic": state_topic,
                "value_template": "{{ value_json.last_entry }}",
                "default_entity_id": f"sensor.mesh_{slug}_last_entry",
            },
            "last_exit": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_exit",
                "name": "Последний выход",
                "device_class": "timestamp",
                "icon": "mdi:logout",
                "state_topic": state_topic,
                "value_template": "{{ value_json.last_exit }}",
                "default_entity_id": f"sensor.mesh_{slug}_last_exit",
            },
            "pass": {
                "platform": "event",
                "unique_id": f"{uid}_pass",
                "name": "Проход",
                "event_types": ["entry", "exit"],
                "icon": "mdi:door-open",
                "state_topic": child_topic(profile_id, child.id, "event"),
                "default_entity_id": f"event.mesh_{slug}_pass",
            },
        },
    }
    return f"{prefix}/device/{uid}/config", config
```

- [ ] **Step 5: Реализовать публикатор**

`mesh_passes/app/publisher.py`:

```python
import json
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
from app.mesh import Child

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

    async def attach(self, client: MqttClient) -> None:
        self._client = client
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

    async def publish_event(self, profile_id: int, child: Child, event: PassEvent) -> None:
        topic = child_topic(profile_id, child.id, "event")
        payload = _json(
            {
                "event_type": event.kind,
                "child": child.first_name,
                "time": event.at.isoformat(),
                "school": event.school,
                "person": event.person,
            }
        )
        if not await self._send(topic, payload, retain=False):
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
        if self._client is None:
            return False
        try:
            await self._client.publish(topic, payload, qos=1, retain=retain)
        except aiomqtt.MqttError:
            self._client = None
            return False
        return True
```

- [ ] **Step 6: Прогнать тесты и линт**

Run: `uv run pytest tests/test_discovery.py tests/test_publisher.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 11 passed; ruff чистый.

- [ ] **Step 7: Commit**

```bash
git add mesh_passes/app/discovery.py mesh_passes/app/publisher.py tests/test_discovery.py tests/test_publisher.py
git commit -m "feat: MQTT discovery and publisher with offline cache" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Подключение к MQTT-брокеру

**Files:**
- Create: `mesh_passes/app/mqtt_runner.py`
- Test: `tests/test_mqtt_runner.py`

**Interfaces:**
- Consumes: `MqttPublisher.attach/detach/republish` (Task 8), `BASE`, `AVAILABILITY_TOPIC` (Task 8), `Settings` (Task 1)
- Produces:
  - `app.mqtt_runner.handle_message(topic: str, payload: bytes, prefix: str, publisher: MqttPublisher, poll_now: Callable[[], None]) -> Awaitable[None]`
  - `app.mqtt_runner.run_mqtt(settings: Settings, publisher: MqttPublisher, poll_now: Callable[[], None]) -> Awaitable[None]` — бесконечный цикл с переподключением каждые 10 с

- [ ] **Step 1: Написать падающие тесты**

`tests/test_mqtt_runner.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_mqtt_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mqtt_runner'`.

- [ ] **Step 3: Реализовать**

`mesh_passes/app/mqtt_runner.py`:

```python
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
                async for message in client.messages:
                    payload = message.payload if isinstance(message.payload, bytes) else str(message.payload).encode()
                    await handle_message(message.topic.value, payload, settings.discovery_prefix, publisher, poll_now)
        except aiomqtt.MqttError as err:
            publisher.detach()
            _LOGGER.warning("MQTT недоступен (%s), переподключение через %d с", err, RECONNECT_SECONDS)
            await asyncio.sleep(RECONNECT_SECONDS)
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_mqtt_runner.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 3 passed; ruff чистый. Подключение к настоящему брокеру проверяется в Task 14.

- [ ] **Step 5: Commit**

```bash
git add mesh_passes/app/mqtt_runner.py tests/test_mqtt_runner.py
git commit -m "feat: MQTT connection loop with Home Assistant birth handling" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Страница аддона

**Files:**
- Create: `mesh_passes/app/web/__init__.py` (пустой), `mesh_passes/app/web/server.py`, `mesh_passes/app/web/static/index.html`, `mesh_passes/app/web/static/app.js`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Auth` и его атрибуты/методы (Task 4–5); у `poller` — `status`, `last_poll`, `child_states`, `request_poll()` (Task 7)
- Produces:
  - `app.web.server.SUPERVISOR_INGRESS_IP = "172.30.32.2"`
  - `app.web.server.status_payload(auth, poller) -> dict` с ключами `state, busy, error, qr_svg, sms{seconds_left, attempts}|null, status, last_poll, token_expires, last_renewal, children[{id, name, class_name, at_school, visits}]`
  - `app.web.server.create_app(auth, poller, *, password: str | None, ingress_only: bool) -> web.Application`; маршруты: `GET /`, `GET /app.js`, `GET /api/status`, `POST /api/login/start`, `POST /api/login/sms` (`{"code": "..."}`), `POST /api/login/sms/resend`, `POST /api/logout`, `POST /api/poll`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_web.py`:

```python
import aiohttp
import pytest

from app.auth import LoginState
from app.web.server import create_app
from helpers import wait_for


class FakePoller:
    def __init__(self):
        self.status = "ok"
        self.last_poll = None
        self.child_states = {}
        self.polls = 0

    def request_poll(self):
        self.polls += 1


@pytest.fixture
def poller():
    return FakePoller()


@pytest.fixture
async def client(aiohttp_client, auth, poller):
    return await aiohttp_client(create_app(auth, poller, password=None, ingress_only=False))


async def test_index_page(client):
    response = await client.get("/")
    assert response.status == 200
    assert "МЭШ: проходы" in await response.text()
    assert (await client.get("/app.js")).status == 200


async def test_status_logged_out(client):
    data = await (await client.get("/api/status")).json()
    assert data["state"] == "logged_out"
    assert data["children"] == []
    assert data["qr_svg"] is None
    assert data["sms"] is None


async def test_login_with_sms_via_api(client, auth):
    data = await (await client.post("/api/login/start", json={})).json()
    assert data["state"] == "qr"
    assert data["qr_svg"].startswith("<svg")

    await wait_for(lambda: auth.state is LoginState.SMS)
    data = await (await client.get("/api/status")).json()
    assert data["sms"]["attempts"] == 5
    assert 0 < data["sms"]["seconds_left"] <= 300

    data = await (await client.post("/api/login/sms", json={"code": "123456"})).json()
    assert data["state"] == "logged_in"
    assert data["children"] == [{"id": 101, "name": "Иван", "class_name": "3-А", "at_school": None, "visits": []}]


async def test_sms_requires_json_object(client):
    assert (await client.post("/api/login/sms", data="not json")).status == 400


async def test_poll_now(client, poller):
    await client.post("/api/poll", json={})
    assert poller.polls == 1


async def test_basic_auth(aiohttp_client, auth, poller):
    client = await aiohttp_client(create_app(auth, poller, password="secret", ingress_only=False))
    assert (await client.get("/api/status")).status == 401
    assert (await client.get("/api/status", auth=aiohttp.BasicAuth("any", "secret"))).status == 200


async def test_ingress_only_rejects_other_addresses(aiohttp_client, auth, poller):
    client = await aiohttp_client(create_app(auth, poller, password=None, ingress_only=True))
    assert (await client.get("/api/status")).status == 403
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.web'`.

- [ ] **Step 3: Реализовать сервер**

`mesh_passes/app/web/__init__.py` — пустой файл.

`mesh_passes/app/web/server.py`:

```python
"""Страница аддона: вход в mos.ru и состояние опроса."""

import base64
import binascii
import hmac
import time
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from app.auth import Auth

STATIC = Path(__file__).parent / "static"
SUPERVISOR_INGRESS_IP = "172.30.32.2"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def status_payload(auth: Auth, poller) -> dict:
    children = []
    for child in auth.children:
        state = poller.child_states.get(child.id)
        children.append(
            {
                "id": child.id,
                "name": child.first_name,
                "class_name": child.class_name,
                "at_school": state.at_school if state else None,
                "visits": state.visits if state else [],
            }
        )
    sms = None
    if auth.sms:
        sms = {"seconds_left": max(0, int(auth.sms.deadline - time.time())), "attempts": auth.sms.attempts}
    return {
        "state": auth.state.value,
        "busy": auth.busy,
        "error": auth.error,
        "qr_svg": auth.qr_svg,
        "sms": sms,
        "status": poller.status,
        "last_poll": _iso(poller.last_poll),
        "token_expires": _iso(auth.token_expires()),
        "last_renewal": _iso(datetime.fromtimestamp(auth.last_renewal, UTC)) if auth.last_renewal else None,
        "children": children,
    }


@web.middleware
async def _ingress_middleware(request: web.Request, handler):
    if request.remote != SUPERVISOR_INGRESS_IP:
        raise web.HTTPForbidden(text="Доступ только через интерфейс Home Assistant")
    return await handler(request)


def _basic_auth(password: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        given = ""
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                given = base64.b64decode(header[6:]).decode().partition(":")[2]
            except (binascii.Error, UnicodeDecodeError):
                given = ""
        if not hmac.compare_digest(given.encode(), password.encode()):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="mesh-passes"'})
        return await handler(request)

    return middleware


def create_app(auth: Auth, poller, *, password: str | None, ingress_only: bool) -> web.Application:
    middlewares = []
    if ingress_only:
        middlewares.append(_ingress_middleware)
    if password:
        middlewares.append(_basic_auth(password))
    app = web.Application(middlewares=middlewares)

    async def status(_: web.Request) -> web.Response:
        return web.json_response(status_payload(auth, poller))

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "index.html")

    async def script(_: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "app.js")

    async def login_start(request: web.Request) -> web.Response:
        await auth.start_login()
        return await status(request)

    async def login_sms(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Ожидается JSON-объект с полем code")
        await auth.submit_sms(str(body.get("code", "")))
        return await status(request)

    async def sms_resend(request: web.Request) -> web.Response:
        await auth.resend_sms()
        return await status(request)

    async def logout(request: web.Request) -> web.Response:
        await auth.logout()
        return await status(request)

    async def poll(request: web.Request) -> web.Response:
        poller.request_poll()
        return await status(request)

    app.router.add_get("/", index)
    app.router.add_get("/app.js", script)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/login/start", login_start)
    app.router.add_post("/api/login/sms", login_sms)
    app.router.add_post("/api/login/sms/resend", sms_resend)
    app.router.add_post("/api/logout", logout)
    app.router.add_post("/api/poll", poll)
    return app
```

- [ ] **Step 4: Написать страницу**

Все URL относительные (`api/status`, `app.js`) — страница открывается через ingress по пути `/api/hassio_ingress/<token>/`.

`mesh_passes/app/web/static/index.html`:

```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>МЭШ: проходы</title>
<style>
  :root { color-scheme: light dark; --bg: #fafafa; --fg: #1c1c1c; --muted: #6b6b6b; --card: #fff; --line: #e3e3e3; --accent: #0288d1; --bad: #c62828; }
  @media (prefers-color-scheme: dark) { :root { --bg: #111; --fg: #e6e6e6; --muted: #9a9a9a; --card: #1c1c1c; --line: #2c2c2c; --accent: #4fc3f7; --bad: #ef9a9a; } }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
  main { max-width: 640px; margin: 0 auto; padding: 16px; }
  section { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; margin-bottom: 12px; }
  h1 { font-size: 20px; margin: 4px 0 16px; }
  h2 { font-size: 16px; margin: 0 0 8px; }
  .muted { color: var(--muted); }
  .error { color: var(--bad); }
  .qr { background: #fff; padding: 12px; border-radius: 8px; display: inline-block; }
  .qr svg { width: 260px; height: 260px; display: block; }
  button { font: inherit; padding: 8px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--accent); color: #fff; cursor: pointer; margin: 4px 4px 4px 0; }
  button.secondary { background: transparent; color: var(--fg); }
  button:disabled { opacity: .5; cursor: default; }
  input { font: inherit; font-size: 22px; letter-spacing: 4px; width: 9ch; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg); color: var(--fg); }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 4px 0; border-top: 1px solid var(--line); }
  td:first-child { color: var(--muted); width: 45%; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<main>
  <h1>МЭШ: проходы</h1>
  <section id="login" hidden>
    <h2>Вход в mos.ru</h2>
    <p id="login-text" class="muted"></p>
    <div id="qr" class="qr" hidden></div>
    <form id="sms" hidden>
      <input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required>
      <button type="submit">Отправить</button>
      <button type="button" id="resend" class="secondary">Отправить SMS ещё раз</button>
      <p id="sms-info" class="muted"></p>
    </form>
    <button id="start" hidden>Войти через QR-код</button>
  </section>
  <p id="error" class="error" hidden></p>
  <section id="children" hidden></section>
  <section id="session" hidden>
    <h2>Сессия</h2>
    <table>
      <tr><td>Статус</td><td id="status"></td></tr>
      <tr><td>Последний успешный опрос</td><td id="last-poll"></td></tr>
      <tr><td>Токен действует до</td><td id="token-expires"></td></tr>
      <tr><td>Последнее продление</td><td id="last-renewal"></td></tr>
    </table>
    <p>
      <button id="poll">Проверить сейчас</button>
      <button id="logout" class="secondary">Выйти</button>
    </p>
  </section>
</main>
<script src="app.js"></script>
</body>
</html>
```

`mesh_passes/app/web/static/app.js`:

```javascript
const $ = (id) => document.getElementById(id);

const STATUS = {
  ok: "работает",
  auth_required: "нужен вход",
  api_error: "ошибка API МЭШ",
  outside_hours: "вне окна опроса",
};

const SCANNER = "«Моя Москва» или «Госуслуги Москвы»: Настройки → Безопасность → сканер QR-кода.";
const TEXT = {
  logged_out: `Нажмите кнопку и отсканируйте QR-код в приложении ${SCANNER}`,
  auth_required: "Сессия mos.ru закончилась — войдите заново.",
  qr: `Отсканируйте QR-код в приложении ${SCANNER} Код обновляется сам.`,
  confirm: "Подтвердите вход в приложении на телефоне.",
  sms: "mos.ru видит новое устройство — введите код из SMS.",
};

const moscowTime = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) : "—");

function showError(message) {
  $("error").hidden = !message;
  $("error").textContent = message || "";
}

async function call(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  render(await response.json());
}

function renderChild(child) {
  const box = document.createElement("div");
  const title = document.createElement("h2");
  const place = child.at_school === null ? "нет данных" : child.at_school ? "в школе" : "не в школе";
  title.textContent = `${child.name}${child.class_name ? ` (${child.class_name})` : ""} — ${place}`;
  const visits = document.createElement("p");
  visits.className = "muted";
  visits.textContent = child.visits.length
    ? child.visits.map((visit) => `вход ${visit.in}, выход ${visit.out}`).join("; ")
    : "Сегодня проходов нет";
  box.append(title, visits);
  return box;
}

function render(s) {
  $("login").hidden = s.state === "logged_in";
  $("login-text").textContent = TEXT[s.state] || "";
  $("qr").hidden = s.state !== "qr";
  if (s.state === "qr" && $("qr").dataset.svg !== s.qr_svg) {
    $("qr").innerHTML = s.qr_svg;
    $("qr").dataset.svg = s.qr_svg;
  }
  $("sms").hidden = s.state !== "sms";
  if (s.sms) {
    const left = `${Math.floor(s.sms.seconds_left / 60)}:${String(s.sms.seconds_left % 60).padStart(2, "0")}`;
    $("sms-info").textContent = `Код действует ещё ${left}` + (s.sms.attempts != null ? `, попыток: ${s.sms.attempts}` : "");
  }
  $("start").hidden = !["logged_out", "auth_required"].includes(s.state);
  showError(s.error);
  for (const button of document.querySelectorAll("button")) button.disabled = s.busy;
  $("session").hidden = s.state !== "logged_in" && s.children.length === 0;
  $("status").textContent = STATUS[s.status] || s.status;
  $("last-poll").textContent = moscowTime(s.last_poll);
  $("token-expires").textContent = moscowTime(s.token_expires);
  $("last-renewal").textContent = moscowTime(s.last_renewal);
  $("children").hidden = s.children.length === 0;
  $("children").replaceChildren(...s.children.map(renderChild));
}

$("start").onclick = () => call("api/login/start", {}).catch((err) => showError(err.message));
$("sms").onsubmit = (event) => {
  event.preventDefault();
  call("api/login/sms", { code: $("code").value })
    .then(() => { $("code").value = ""; })
    .catch((err) => showError(err.message));
};
$("resend").onclick = () => call("api/login/sms/resend", {}).catch((err) => showError(err.message));
$("poll").onclick = () => call("api/poll", {}).catch((err) => showError(err.message));
$("logout").onclick = () => {
  if (confirm("Выйти из mos.ru? Для нового входа понадобится QR-код и, возможно, SMS.")) {
    call("api/logout", {}).catch((err) => showError(err.message));
  }
};

async function tick() {
  try {
    await call("api/status");
  } catch (err) {
    showError(err.message);
  }
  setTimeout(tick, 2000);
}

tick();
```

`qr_svg` вставляется через `innerHTML` — это SVG, сгенерированный `segno` на сервере, не пользовательский ввод; имена и классы детей выводятся только через `textContent`.

- [ ] **Step 5: Прогнать тесты и линт**

Run: `uv run pytest tests/test_web.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 7 passed; ruff чистый.

- [ ] **Step 6: Commit**

```bash
git add mesh_passes/app/web tests/test_web.py
git commit -m "feat: ingress page for login and polling status" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Точка входа и упаковка аддона

**Files:**
- Create: `mesh_passes/app/__main__.py`, `mesh_passes/requirements.txt`, `mesh_passes/Dockerfile`, `mesh_passes/config.yaml`, `mesh_passes/translations/ru.yaml`, `mesh_passes/translations/en.yaml`, `mesh_passes/DOCS.md`, `mesh_passes/CHANGELOG.md`, `repository.yaml`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: всё из Task 1–10
- Produces: `python -m app` (рабочий каталог `/opt/mesh_passes`); `app.__main__.run() -> None` (при `SettingsError` пишет лог и завершается с кодом 1)

- [ ] **Step 1: Написать падающий тест**

`tests/test_main.py`:

```python
import pytest

from app.__main__ import run


def test_settings_error_exits_with_code_1(monkeypatch, caplog):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("WEB_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        run()

    assert exit_info.value.code == 1
    assert "WEB_PASSWORD" in caplog.text
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.__main__'`.

- [ ] **Step 3: Реализовать точку входа**

`mesh_passes/app/__main__.py`:

```python
import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from app import VERSION
from app.auth import Auth
from app.discovery import REPO_URL
from app.mesh import MeshClient
from app.mqtt_runner import run_mqtt
from app.poller import Poller
from app.publisher import MqttPublisher
from app.settings import SettingsError, build_settings
from app.web.server import create_app

_LOGGER = logging.getLogger("app")
USER_AGENT = f"ha-mesh-passes/{VERSION} (+{REPO_URL})"
TIMEOUT = aiohttp.ClientTimeout(total=30)


async def supervisor_mqtt(token: str) -> dict | None:
    async with (
        aiohttp.ClientSession(timeout=TIMEOUT) as http,
        http.get("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"}) as response,
    ):
        if response.status != 200:
            return None
        return (await response.json()).get("data")


async def main() -> None:
    env = os.environ
    addon = "SUPERVISOR_TOKEN" in env
    options = json.loads(Path("/data/options.json").read_text(encoding="utf-8")) if addon else {}
    mqtt_service = await supervisor_mqtt(env["SUPERVISOR_TOKEN"]) if addon else None
    settings = build_settings(options, env, mqtt_service)
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True
    )
    _LOGGER.info("ha-mesh-passes %s запущен (%s)", VERSION, "аддон" if addon else "Docker")

    headers = {"User-Agent": USER_AGENT}
    async with (
        aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), headers=headers, timeout=TIMEOUT) as login_http,
        aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), headers=headers, timeout=TIMEOUT) as api_http,
    ):
        mesh = MeshClient(api_http)
        auth = Auth(login_http, mesh, settings.data_dir / "session.json")
        auth.load()
        publisher = MqttPublisher(settings.discovery_prefix)
        poller = Poller(auth, mesh, publisher, settings, settings.data_dir / "state.json")
        auth.on_login = poller.request_poll

        runner = web.AppRunner(create_app(auth, poller, password=settings.web_password, ingress_only=settings.addon))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.web_port).start()
        try:
            await asyncio.gather(poller.run(), run_mqtt(settings, publisher, poller.request_poll))
        finally:
            await auth.close()
            await runner.cleanup()


def run() -> None:
    try:
        asyncio.run(main())
    except SettingsError as err:
        logging.basicConfig()
        _LOGGER.error("Ошибка настроек: %s", err)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Прогнать тест**

Run: `uv run pytest tests/test_main.py -v`
Expected: 1 passed.

- [ ] **Step 5: Файлы аддона**

`mesh_passes/requirements.txt`:

```
aiohttp>=3.10,<4
aiomqtt>=2.3,<3
segno>=1.6,<2
tzdata>=2024.1
```

`mesh_passes/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=local
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"

WORKDIR /opt/mesh_passes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app"]
```

`mesh_passes/config.yaml`:

```yaml
name: "МЭШ: проходы"
version: "0.1.0"
slug: mesh_passes
description: Проходы ребёнка через турникет школы (Москвёнок) из МЭШ в Home Assistant
url: https://github.com/freemandigger/ha-mesh-passes
image: "ghcr.io/freemandigger/mesh-passes-{arch}"
arch:
  - aarch64
  - amd64
stage: experimental
ingress: true
ingress_port: 8099
panel_icon: mdi:school
panel_title: МЭШ
services:
  - mqtt:need
options:
  poll_interval: 3
  active_from: "07:00"
  active_to: "20:00"
  active_days:
    - mon
    - tue
    - wed
    - thu
    - fri
    - sat
  max_event_age: 30
  discovery_prefix: homeassistant
  log_level: info
schema:
  poll_interval: int(2,60)
  active_from: match(^([01]\d|2[0-3]):[0-5]\d$)
  active_to: match(^([01]\d|2[0-3]):[0-5]\d$)
  active_days:
    - list(mon|tue|wed|thu|fri|sat|sun)
  max_event_age: int(5,240)
  discovery_prefix: str
  log_level: list(debug|info|warning|error)
```

`mesh_passes/translations/ru.yaml`:

```yaml
configuration:
  poll_interval:
    name: Интервал опроса, минуты
    description: Как часто запрашивать проходы в МЭШ (от 2 до 60).
  active_from:
    name: Начало окна опроса
    description: Время ЧЧ:ММ по Москве, с которого аддон опрашивает МЭШ.
  active_to:
    name: Конец окна опроса
    description: Время ЧЧ:ММ по Москве, после которого опрос прекращается.
  active_days:
    name: Дни опроса
    description: "Дни недели: mon, tue, wed, thu, fri, sat, sun."
  max_event_age:
    name: Максимальный возраст события, минуты
    description: Проход старше этого значения обновляет датчики, но не создаёт событие для уведомлений.
  discovery_prefix:
    name: Префикс MQTT discovery
    description: Обычно homeassistant.
  log_level:
    name: Уровень логирования
    description: debug, info, warning или error.
```

`mesh_passes/translations/en.yaml`:

```yaml
configuration:
  poll_interval:
    name: Poll interval, minutes
    description: How often to request passes from MESH (2 to 60).
  active_from:
    name: Polling window start
    description: HH:MM, Moscow time, when polling starts.
  active_to:
    name: Polling window end
    description: HH:MM, Moscow time, when polling stops.
  active_days:
    name: Polling days
    description: "Days of week: mon, tue, wed, thu, fri, sat, sun."
  max_event_age:
    name: Maximum event age, minutes
    description: Older passes update sensors but do not fire events for notifications.
  discovery_prefix:
    name: MQTT discovery prefix
    description: Usually homeassistant.
  log_level:
    name: Log level
    description: debug, info, warning or error.
```

`repository.yaml`:

```yaml
name: МЭШ — проходы в школу
url: https://github.com/freemandigger/ha-mesh-passes
maintainer: Dmitriy Beketov
```

`mesh_passes/CHANGELOG.md`:

```markdown
# Changelog

## 0.1.0

- Первая экспериментальная версия: вход в mos.ru по QR-коду, опрос проходов, сущности и события через MQTT.
```

`mesh_passes/DOCS.md`:

````markdown
# МЭШ: проходы

Аддон получает из Московской электронной школы (МЭШ) проходы ребёнка через турникет школы (карта «Москвёнок») и передаёт их в Home Assistant через MQTT.

> **Неофициальный проект.** Не связан с ДИТ Москвы и mos.ru. Использует неофициальный API МЭШ, который может измениться без предупреждения. Автоматизированный доступ может противоречить пользовательскому соглашению mos.ru — возможна блокировка учётной записи. Используйте только для своих детей.

## Что нужно

- MQTT-брокер (например, аддон Mosquitto) и интеграция MQTT в Home Assistant.
- Учётная запись родителя на mos.ru с доступом к МЭШ.
- Приложение «Моя Москва» или «Госуслуги Москвы» на телефоне.

## Первый вход

1. Запустите аддон и откройте панель **МЭШ** в боковом меню.
2. Нажмите «Войти через QR-код».
3. В приложении «Моя Москва» или «Госуслуги Москвы» откройте **Настройки → Безопасность → сканер QR-кода**, отсканируйте код и подтвердите вход.
4. Если mos.ru видит новое устройство, придёт SMS — введите код на странице аддона. Аддон отметит устройство доверенным, и при следующем входе SMS обычно не понадобится.

## Сущности

| Сущность | Что показывает |
|---|---|
| `sensor.mesh_account_status` | `ok`, `auth_required` (нужен вход), `api_error`, `outside_hours` |
| `sensor.mesh_account_last_poll` | время последнего успешного опроса |
| `sensor.mesh_account_token_expires` | до какого времени действует токен |
| `button.mesh_account_poll_now` | опросить сейчас |
| `binary_sensor.mesh_<имя>_at_school` | ребёнок в школе; атрибуты `school`, `visits` |
| `sensor.mesh_<имя>_last_entry`, `sensor.mesh_<имя>_last_exit` | время последнего входа и выхода |
| `event.mesh_<имя>_pass` | событие прохода `entry` / `exit`; атрибуты `child`, `time`, `school`, `person` |

`<имя>` — имя ребёнка латиницей (например, `ivan`).

## Уведомления

В репозитории есть два blueprint (см. README):

- **«МЭШ: проход ребёнка»** — действия при входе или выходе;
- **«МЭШ: нужен вход / API не отвечает»** — напоминание войти заново.

## Настройки

| Опция | По умолчанию | Описание |
|---|---|---|
| `poll_interval` | 3 | интервал опроса, минуты (2–60) |
| `active_from` / `active_to` | 07:00 / 20:00 | окно опроса по Москве |
| `active_days` | пн–сб | дни опроса |
| `max_event_age` | 30 | проход старше — без события, только датчики |
| `discovery_prefix` | homeassistant | префикс MQTT discovery |
| `log_level` | info | уровень логирования |

## Как это работает и ограничения

- Проход появляется в МЭШ с задержкой в несколько минут; с интервалом 3 минуты уведомление приходит примерно через 3–7 минут после прохода.
- Аддон сам продлевает токен, пока mos.ru это позволяет. Когда нужен повторный вход, статус становится `auth_required`.
- «Выйти» удаляет сохранённую сессию, включая отметку доверенного устройства — при следующем входе может снова понадобиться SMS.
- Если ребёнок пропал из профиля, пока аддон был выключен, его устройство останется в Home Assistant — удалите его вручную (Настройки → Устройства и службы → MQTT).

## Приватность

- Сессия mos.ru хранится только в `/data` аддона (попадает в резервные копии HA).
- Данные уходят только на mos.ru / school.mos.ru и в ваш MQTT-брокер.
- В логах нет токенов, кодов SMS и персональных данных.
````

- [ ] **Step 6: Проверить сборку образа**

Run: `docker version --format '{{.Server.Version}}'`
Expected: версия Docker. Если Docker не установлен — остановиться и спросить владельца, как проверить сборку (варианты: установить Docker Desktop / Colima или положиться на CI из Task 13).

Run: `docker build -t mesh-passes:dev mesh_passes && docker run --rm mesh-passes:dev; echo "exit=$?"`
Expected: сборка успешна; контейнер пишет `Ошибка настроек: WEB_PASSWORD обязателен при запуске вне Home Assistant` и `exit=1`.

- [ ] **Step 7: Полный прогон и commit**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: всё зелёное.

```bash
git add mesh_passes/app/__main__.py mesh_passes/requirements.txt mesh_passes/Dockerfile mesh_passes/config.yaml mesh_passes/translations mesh_passes/DOCS.md mesh_passes/CHANGELOG.md repository.yaml tests/test_main.py
git commit -m "feat: add-on entry point, Dockerfile and Home Assistant metadata" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Blueprints

**Files:**
- Create: `blueprints/automation/mesh_passes/pass_notification.yaml`, `blueprints/automation/mesh_passes/session_alert.yaml`
- Test: `tests/test_blueprints.py`

**Interfaces:**
- Consumes: сущности из Task 8 (`event.mesh_<slug>_pass` с атрибутами `event_type`, `child`, `time`, `school`, `person`; `sensor.mesh_account_status`)
- Produces: переменные для действий пользователя — `child`, `direction`, `time`, `school`, `person` (blueprint 1) и `reason` (blueprint 2)

- [ ] **Step 1: Написать падающие тесты**

`tests/test_blueprints.py`:

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "mesh_passes"


class Input(str):
    pass


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def load(name):
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=BlueprintLoader)


def test_pass_notification():
    blueprint = load("pass_notification.yaml")
    meta = blueprint["blueprint"]
    assert meta["domain"] == "automation"
    assert set(meta["input"]) == {"pass_event", "event_types", "actions"}
    assert meta["input"]["event_types"]["default"] == ["exit"]
    assert blueprint["mode"] == "queued"
    assert blueprint["triggers"] == [{"trigger": "state", "entity_id": "pass_event"}]
    assert isinstance(blueprint["triggers"][0]["entity_id"], Input)
    assert {"child", "direction", "time", "school", "person"} <= set(blueprint["variables"])
    condition = blueprint["conditions"][0]["value_template"]
    assert "total_seconds() < 60" in condition
    assert "unavailable" in condition
    assert blueprint["actions"] == "actions"


def test_session_alert():
    blueprint = load("session_alert.yaml")
    meta = blueprint["blueprint"]
    assert set(meta["input"]) == {"status_sensor", "api_error_hours", "actions"}
    assert meta["input"]["status_sensor"]["default"] == "sensor.mesh_account_status"
    assert meta["input"]["api_error_hours"]["default"] == 3
    assert [(t["to"], t["id"]) for t in blueprint["triggers"]] == [
        ("auth_required", "auth_required"),
        ("api_error", "api_error"),
    ]
    assert blueprint["triggers"][1]["for"]["hours"] == "api_error_hours"
    assert blueprint["variables"]["reason"] == "{{ trigger.id }}"
    assert blueprint["actions"] == "actions"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_blueprints.py -v`
Expected: FAIL — `FileNotFoundError` на `pass_notification.yaml`.

- [ ] **Step 3: Написать blueprints**

`blueprints/automation/mesh_passes/pass_notification.yaml`:

```yaml
blueprint:
  name: "МЭШ: проход ребёнка"
  description: >-
    Действия при входе ребёнка в школу или выходе из неё (аддон «МЭШ: проходы»).
    В действиях доступны переменные: child — имя ребёнка; direction — entry или exit;
    time — время прохода ЧЧ:ММ; school — школа; person — кто привёл или забрал, если известно.
    Пример текста уведомления: 🏫 {{ child }}: выход из школы в {{ time }}
  domain: automation
  author: ha-mesh-passes
  source_url: https://github.com/freemandigger/ha-mesh-passes/blob/main/blueprints/automation/mesh_passes/pass_notification.yaml
  input:
    pass_event:
      name: Проход
      description: Сущность «Проход» ребёнка, например event.mesh_ivan_pass
      selector:
        entity:
          filter:
            - domain: event
    event_types:
      name: События
      default:
        - exit
      selector:
        select:
          multiple: true
          options:
            - label: Вход в школу
              value: entry
            - label: Выход из школы
              value: exit
    actions:
      name: Действия
      selector:
        action: {}

mode: queued

triggers:
  - trigger: state
    entity_id: !input pass_event

variables:
  event_types: !input event_types
  child: "{{ trigger.to_state.attributes.child }}"
  direction: "{{ trigger.to_state.attributes.event_type }}"
  time: "{{ (trigger.to_state.attributes.time or '')[11:16] }}"
  school: "{{ trigger.to_state.attributes.school }}"
  person: "{{ trigger.to_state.attributes.person }}"

conditions:
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.to_state.state not in ['unavailable', 'unknown']
         and trigger.to_state.attributes.event_type in event_types
         and (now() - as_datetime(trigger.to_state.state)).total_seconds() < 60 }}

actions: !input actions
```

`blueprints/automation/mesh_passes/session_alert.yaml`:

```yaml
blueprint:
  name: "МЭШ: нужен вход / API не отвечает"
  description: >-
    Действия, когда аддону «МЭШ: проходы» нужен повторный вход в mos.ru или API МЭШ долго не отвечает.
    В действиях доступна переменная reason: auth_required или api_error.
    Пример текста: Нужно заново войти в МЭШ: откройте аддон «МЭШ: проходы» в Home Assistant.
  domain: automation
  author: ha-mesh-passes
  source_url: https://github.com/freemandigger/ha-mesh-passes/blob/main/blueprints/automation/mesh_passes/session_alert.yaml
  input:
    status_sensor:
      name: Статус аддона
      default: sensor.mesh_account_status
      selector:
        entity:
          filter:
            - domain: sensor
    api_error_hours:
      name: Ошибка API дольше, часов
      description: 0 — не уведомлять об ошибках API
      default: 3
      selector:
        number:
          min: 0
          max: 48
          unit_of_measurement: ч
    actions:
      name: Действия
      selector:
        action: {}

mode: single

triggers:
  - trigger: state
    entity_id: !input status_sensor
    to: auth_required
    for:
      minutes: 1
    id: auth_required
  - trigger: state
    entity_id: !input status_sensor
    to: api_error
    for:
      hours: !input api_error_hours
    id: api_error

variables:
  api_error_hours: !input api_error_hours
  reason: "{{ trigger.id }}"

conditions:
  - condition: template
    value_template: "{{ trigger.id == 'auth_required' or (api_error_hours | float(0)) > 0 }}"

actions: !input actions
```

- [ ] **Step 4: Прогнать тесты и линт**

Run: `uv run pytest tests/test_blueprints.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: 2 passed; ruff чистый. Проверка в настоящем HA — Task 15.

- [ ] **Step 5: Commit**

```bash
git add blueprints tests/test_blueprints.py
git commit -m "feat: notification blueprints for passes and session alerts" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: CI, релизы и README

**Files:**
- Create: `.github/workflows/ci.yaml`, `.github/workflows/release.yaml`, `README.md`

**Interfaces:**
- Consumes: команды `uv sync`, `uv run ruff ...`, `uv run pytest` (Task 1); `mesh_passes/config.yaml` с `version: "X.Y.Z"` (Task 11)
- Produces: CI на PR/push в `main`; релиз образов по тегу `vX.Y.Z`

- [ ] **Step 1: Workflows**

`.github/workflows/ci.yaml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest

  addon-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: frenck/action-addon-linter@v2
        with:
          path: ./mesh_passes
```

`.github/workflows/release.yaml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: read
  packages: write

jobs:
  image:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - arch: aarch64
            platform: linux/arm64
          - arch: amd64
            platform: linux/amd64
    steps:
      - uses: actions/checkout@v4
      - name: Версия тега совпадает с config.yaml
        run: |
          version="${GITHUB_REF_NAME#v}"
          grep -qx "version: \"${version}\"" mesh_passes/config.yaml
          echo "VERSION=${version}" >> "$GITHUB_ENV"
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: mesh_passes
          platforms: ${{ matrix.platform }}
          push: true
          provenance: false
          build-args: |
            BUILD_VERSION=${{ env.VERSION }}
            BUILD_ARCH=${{ matrix.arch }}
          tags: |
            ghcr.io/freemandigger/mesh-passes-${{ matrix.arch }}:${{ env.VERSION }}
            ghcr.io/freemandigger/mesh-passes-${{ matrix.arch }}:latest
```

- [ ] **Step 2: README**

`README.md`:

````markdown
# МЭШ: проходы — аддон Home Assistant

Проходы ребёнка через турникет школы (карта «Москвёнок») из Московской электронной школы — в Home Assistant: датчик «в школе», время входа и выхода, события для уведомлений.

> ⚠️ **Неофициальный проект.** Не связан с ДИТ Москвы и mos.ru. Использует неофициальный API МЭШ, который может измениться без предупреждения. Автоматизированный доступ может противоречить пользовательскому соглашению mos.ru — возможна блокировка учётной записи. Данные остаются в вашем Home Assistant. Используйте только для своих детей.

## Установка

[![Добавить репозиторий в Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes)

1. Добавьте репозиторий кнопкой выше (или вручную: Настройки → Дополнения → Магазин → ⋮ → Репозитории → `https://github.com/freemandigger/ha-mesh-passes`).
2. Установите аддон «МЭШ: проходы». Нужен MQTT-брокер (например, аддон Mosquitto) и интеграция MQTT.
3. Запустите аддон, откройте панель **МЭШ** и войдите по QR-коду из приложения «Моя Москва» или «Госуслуги Москвы» (Настройки → Безопасность → сканер QR-кода).

Подробности — в [документации аддона](mesh_passes/DOCS.md).

## Уведомления

[![Импорт blueprint «проход ребёнка»](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmesh_passes%2Fpass_notification.yaml)
[![Импорт blueprint «нужен вход»](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmesh_passes%2Fsession_alert.yaml)

Пример: сообщение в тему Telegram-группы, когда ребёнок вышел из школы.

```yaml
use_blueprint:
  path: mesh_passes/pass_notification.yaml
  input:
    pass_event: event.mesh_ivan_pass
    event_types: [exit]
    actions:
      - action: telegram_bot.send_message
        data:
          chat_id: -100XXXXXXXXXX
          message_thread_id: 15
          message: "🏫 {{ child }}: выход из школы в {{ time }}"
```

Для `telegram_bot` в Home Assistant 2026.x адресат указывается через `chat_id` (параметр `target` устарел), а чат должен быть в списке разрешённых чатов бота — иначе сообщение молча уйдёт в чат по умолчанию.

## Без Home Assistant OS (Docker)

```yaml
services:
  mesh-passes:
    image: ghcr.io/freemandigger/mesh-passes-amd64:latest
    restart: unless-stopped
    environment:
      MQTT_HOST: mqtt.local
      MQTT_USERNAME: mesh
      MQTT_PASSWORD: change-me
      WEB_PASSWORD: change-me
      DATA_DIR: /data
    ports:
      - "8099:8099"
    volumes:
      - ./data:/data
```

Страница входа — `http://<хост>:8099` (любое имя пользователя, пароль из `WEB_PASSWORD`). Остальные опции — переменные окружения в верхнем регистре: `POLL_INTERVAL`, `ACTIVE_FROM`, `ACTIVE_TO`, `ACTIVE_DAYS` (через запятую), `MAX_EVENT_AGE`, `DISCOVERY_PREFIX`, `LOG_LEVEL`.

## Разработка

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
docker compose -f docker-compose.dev.yaml up --build
```

## Благодарности

Код аддона написан с нуля. Как устроены вход в mos.ru и API МЭШ, помогли понять открытые проекты:

- [OctoDiary-py](https://github.com/OctoDiary/OctoDiary-py) и [форк Mag329](https://github.com/Mag329/OctoDiary-py), [OctoDiary-kt](https://github.com/OctoDiary/OctoDiary-kt) (MIT)
- [Learnify-bot](https://github.com/Mag329/Learnify-bot) (MIT)
- [mesh_expressive](https://github.com/AmetistYT/mesh_expressive) (MIT)
- [hass-mosru-water](https://github.com/kostinos/hass-mosru-water) (MIT)
- [DnevnikApi](https://github.com/RedGuyRu/DnevnikApi) (MIT)
- [libremesh](https://github.com/x3lfyn/libremesh) (MIT)
- [SchoolAPI](https://github.com/DavidZhivaev/SchoolAPI) (GPL-3.0)

## Лицензия

[MIT](LICENSE)
````

- [ ] **Step 3: Проверить YAML workflows локально**

Run: `uv run python -c "import yaml, pathlib; [yaml.safe_load(p.read_text()) for p in pathlib.Path('.github/workflows').glob('*.yaml')]; print('ok')"`
Expected: `ok`. Реальный запуск CI — в Task 16 после публикации.

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "ci: tests, add-on lint and image release; add README" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: Локальная проверка с настоящим mos.ru и Mosquitto

Ручная проверка с участием владельца (скан QR, возможно SMS). Код меняется только если проверка выявит дефект — тогда сначала тест, воспроизводящий дефект, затем исправление.

**Files:**
- Create: `docker-compose.dev.yaml`, `dev/mosquitto.conf`

**Interfaces:**
- Consumes: образ из Task 11, страница из Task 10
- Produces: подтверждение, что вход, опрос, discovery и события работают против реального mos.ru

- [ ] **Step 1: Окружение**

`dev/mosquitto.conf`:

```
listener 1883
allow_anonymous true
```

`docker-compose.dev.yaml`:

```yaml
services:
  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./dev/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro

  mesh-passes:
    build: ./mesh_passes
    depends_on:
      - mosquitto
    environment:
      MQTT_HOST: mosquitto
      WEB_PASSWORD: dev
      DATA_DIR: /data
      LOG_LEVEL: debug
      ACTIVE_FROM: "00:00"
      ACTIVE_TO: "23:59"
      ACTIVE_DAYS: mon,tue,wed,thu,fri,sat,sun
    ports:
      - "8099:8099"
    volumes:
      - ./dev/data:/data
```

Run: `docker compose -f docker-compose.dev.yaml up --build -d && docker compose -f docker-compose.dev.yaml logs mesh-passes | tail -5`
Expected: строки `ha-mesh-passes 0.1.0 запущен (Docker)` и `MQTT подключён: mosquitto:1883`.

- [ ] **Step 2: Вход**

Открыть `http://127.0.0.1:8099/` во встроенном браузере (логин любой, пароль `dev`). Попросить владельца нажать «Войти через QR-код», отсканировать и, если спросит, ввести SMS-код на странице. Код SMS вводит только владелец.

Expected: страница показывает ребёнка, класс и сегодняшние визиты; статус «работает».

- [ ] **Step 3: MQTT**

Run: `docker compose -f docker-compose.dev.yaml exec mosquitto mosquitto_sub -v -t 'homeassistant/device/#' -t 'mesh_passes/#' -W 5`
Expected: `mesh_passes/availability online`, discovery-конфиги учётной записи и ребёнка, `mesh_passes/<id>/status ok`, JSON состояния ребёнка. Реальные значения в чат и файлы репозитория не копировать.

- [ ] **Step 4: Логи без секретов**

Run: `docker compose -f docker-compose.dev.yaml logs mesh-passes | grep -cE 'eyJ|aupd_|Ltpatoken|sms-code|snils'`
Expected: `0`.

- [ ] **Step 5: Перезапуск сохраняет сессию**

Run: `docker compose -f docker-compose.dev.yaml restart mesh-passes` и через 10 секунд обновить страницу.
Expected: вход не требуется, статус «работает»; `ls -l dev/data` показывает `session.json` и `state.json` с правами `-rw-------`.

- [ ] **Step 6: Остановить**

Run: `docker compose -f docker-compose.dev.yaml down`

- [ ] **Step 7: Commit**

```bash
git add docker-compose.dev.yaml dev/mosquitto.conf
git commit -m "chore: local development stack with Mosquitto" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: Установка на домашний HA владельца и неделя наблюдения

Ручная задача. Адрес HA (`HA_HOST`), chat_id и тема Telegram берутся из личных заметок владельца и **не записываются** в этот репозиторий.

**Files:** в репозитории — только `mesh_passes/DOCS.md`, если наблюдение даст факт для документации.

**Interfaces:**
- Consumes: каталог `mesh_passes/`, blueprints (Task 12)
- Produces: работающий аддон и две автоматизации в домашнем HA; ответ на открытый вопрос Q1 спецификации (частота повторного входа)

- [ ] **Step 1: Доступ по SSH**

Run: `ssh -o BatchMode=yes -o ConnectTimeout=5 root@$HA_HOST true && echo ok`
Expected: `ok`. Если `Permission denied` — попросить владельца добавить публичный ключ Mac (`cat ~/.ssh/id_ed25519.pub`) в настройки аддона «Terminal & SSH» (`authorized_keys`) и перезапустить аддон; сам ключ в настройки не вносить.

- [ ] **Step 2: Скопировать как локальный аддон**

```bash
rsync -a --delete --exclude '__pycache__' mesh_passes/ root@$HA_HOST:/addons/mesh_passes/
ssh root@$HA_HOST "sed -i '/^image:/d' /addons/mesh_passes/config.yaml"
ssh root@$HA_HOST "ha store reload && ha addons install local_mesh_passes && ha addons start local_mesh_passes"
```

Expected: аддон собран на устройстве и запущен (`ha addons info local_mesh_passes` → `state: started`). Строка `image:` удаляется, чтобы Supervisor собрал образ локально, а не скачивал ещё не опубликованный.

- [ ] **Step 3: Вход и сущности**

Владелец открывает панель «МЭШ» в HA и входит по QR. Затем через REST API HA (токен из локальной конфигурации MCP проекта homeAssistant-home) проверить сущности:

Run: `curl -s -H "Authorization: Bearer $HA_TOKEN" http://$HA_HOST:8123/api/states | python3 -c "import json,sys; [print(s['entity_id'], s['state']) for s in json.load(sys.stdin) if 'mesh_' in s['entity_id']]"`
Expected: `sensor.mesh_account_status ok`, `sensor.mesh_account_last_poll`, `sensor.mesh_account_token_expires`, `button.mesh_account_poll_now`, `binary_sensor.mesh_<slug>_at_school`, `sensor.mesh_<slug>_last_entry`, `sensor.mesh_<slug>_last_exit`, `event.mesh_<slug>_pass`. Если entity_id другие (например, из русских имён) — `default_entity_id` не подхватился: завести тест в `tests/test_discovery.py` под фактический формат HA и исправить `discovery.py`.

- [ ] **Step 4: Blueprints и автоматизации владельца**

```bash
ssh root@$HA_HOST "mkdir -p /homeassistant/blueprints/automation/mesh_passes"
scp blueprints/automation/mesh_passes/*.yaml root@$HA_HOST:/homeassistant/blueprints/automation/mesh_passes/
```

В проекте `homeAssistant-home` создать две автоматизации на этих blueprints: выход из школы → Telegram-группа и тема из заметок владельца; «нужен вход» → личный чат владельца. Перед этим проверить, что группа есть в разрешённых чатах бота.

- [ ] **Step 5: Наблюдение**

В течение учебной недели: после выхода ребёнка сверить время уведомления с временем прохода; фиксировать, когда статус переходил в `auth_required` и сколько прошло с последнего входа. Итог — ответ на Q1 спецификации: обновить §13 спецификации и раздел «Как это работает и ограничения» в `mesh_passes/DOCS.md` фактическими цифрами (без личных данных).

- [ ] **Step 6: Commit (если документы менялись)**

```bash
git add docs/superpowers/specs/2026-09-16-mesh-passes-addon-design.md mesh_passes/DOCS.md
git commit -m "docs: session lifetime observed in real use" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: Публикация 0.1.0

Только после недели наблюдения и явного «публикуем» от владельца: это публичное действие.

- [ ] **Step 1: Проверить репозиторий на личные данные**

Run: `git grep -nE '192\.168\.|chat_id: -?[0-9]{6,}|[0-9]{8,}' -- ':!uv.lock'`
Expected: только вымышленные значения из примеров (`-100XXXXXXXXXX`, `1_789_558_630` в тестах) — ни IP, ни реальных chat_id или ID профиля.

- [ ] **Step 2: Создать публичный репозиторий**

Run: `gh repo create freemandigger/ha-mesh-passes --public --source . --remote origin --push`
Expected: репозиторий создан, `main` запушен. Затем `gh run watch` — CI зелёный. Если `frenck/action-addon-linter` ругается — исправить `config.yaml` по сообщению линтера, commit, push.

- [ ] **Step 3: Релиз**

```bash
git tag v0.1.0
git push origin v0.1.0
gh run watch
```

Expected: workflow `Release` опубликовал `ghcr.io/freemandigger/mesh-passes-aarch64:0.1.0` и `…-amd64:0.1.0`.

- [ ] **Step 4: Открыть образы**

Владелец на GitHub: Profile → Packages → `mesh-passes-aarch64` и `mesh-passes-amd64` → Package settings → Change visibility → Public (новые пакеты GHCR приватные, API для смены видимости нет).

- [ ] **Step 5: Установка из репозитория**

На домашнем HA: остановить и удалить локальный аддон (`ha addons uninstall local_mesh_passes`), добавить репозиторий `https://github.com/freemandigger/ha-mesh-passes`, установить «МЭШ: проходы», войти по QR.
Expected: Supervisor скачивает образ из GHCR (не собирает), сущности появляются с теми же entity_id, автоматизации владельца продолжают работать.
