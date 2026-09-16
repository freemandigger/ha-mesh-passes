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
