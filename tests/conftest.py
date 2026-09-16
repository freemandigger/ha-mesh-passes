import aiohttp
import pytest

from app.auth import Auth, LoginState
from app.mesh import MeshClient
from fake_mos import FakeMos
from helpers import wait_for


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


@pytest.fixture
async def logged_in_auth(fake_mos, auth):
    fake_mos.trusted_device = True
    await auth.start_login()
    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)
    return auth
