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
