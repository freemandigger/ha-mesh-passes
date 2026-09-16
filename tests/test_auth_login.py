import asyncio

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


async def test_concurrent_start_login_keeps_single_qr_loop(fake_mos, auth):
    fake_mos.qr_commands = ["showQRCode"]

    await asyncio.gather(auth.start_login(), auth.start_login())

    loops = [
        task for task in asyncio.all_tasks() if task.get_coro().__qualname__ == "Auth._qr_loop" and not task.done()
    ]
    assert len(loops) == 1


async def test_login_completes_when_session_cannot_be_saved(fake_mos, auth, monkeypatch):
    fake_mos.trusted_device = True
    logins = []
    auth.on_login = lambda: logins.append(True)

    def _raise_disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.auth.save_session", _raise_disk_full)

    await auth.start_login()

    await wait_for(lambda: auth.state is LoginState.LOGGED_IN)
    assert logins == [True]
