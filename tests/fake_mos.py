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
    renewal_status: int = 200
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
        if self.renewal_status != 200:
            return web.Response(status=self.renewal_status)
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
            return web.json_response(
                {"inquire": "enter_sms_code", "contact": "7900****", "remain_attempts": 5, "ttl": 300}
            )
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
        if self.renewal_status != 200:
            return web.Response(status=self.renewal_status)
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
