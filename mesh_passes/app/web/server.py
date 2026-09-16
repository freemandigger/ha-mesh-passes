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
