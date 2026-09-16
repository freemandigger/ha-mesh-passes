import time

import pytest

from app.auth import LoginState, NotLoggedIn
from app.mesh import MeshApiError
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


@pytest.mark.parametrize("outage", ["http_503", "connection_refused"])
async def test_unreachable_mos_ru_keeps_session(fake_mos, logged_in_auth, monkeypatch, outage):
    if outage == "http_503":
        fake_mos.renewal_status = 503
    else:
        monkeypatch.setattr(logged_in_auth, "_login", "http://127.0.0.1:9")
        monkeypatch.setattr(logged_in_auth, "_school", "http://127.0.0.1:9")

    with pytest.raises(MeshApiError, match="mos.ru недоступен"):
        await logged_in_auth.renew()

    assert logged_in_auth.state is LoginState.LOGGED_IN
    assert logged_in_auth.error is None


async def test_refresh_children(fake_mos, logged_in_auth):
    fake_mos.children.append({"id": 102, "contingent_guid": "guid-102", "first_name": "Мария", "class_name": "1-А"})

    children = await logged_in_auth.refresh_children()

    assert [child.id for child in children] == [101, 102]


async def test_renewal_survives_session_save_failure(fake_mos, logged_in_auth, monkeypatch):
    def _raise_disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.auth.save_session", _raise_disk_full)

    token = await logged_in_auth.renew()

    assert token == fake_mos.issued_tokens[-1]
    assert logged_in_auth.state is LoginState.LOGGED_IN


async def test_keepalive_not_due_does_nothing(fake_mos, logged_in_auth):
    await logged_in_auth.keepalive()
    assert len(fake_mos.issued_tokens) == 1


async def test_keepalive_renews_via_sso_when_due(fake_mos, logged_in_auth):
    logged_in_auth.logged_in_at = time.time() - 3700

    await logged_in_auth.keepalive()

    assert len(fake_mos.issued_tokens) == 2
    assert logged_in_auth.last_renewal is not None
    await logged_in_auth.keepalive()
    assert len(fake_mos.issued_tokens) == 2


async def test_keepalive_failure_keeps_valid_session(fake_mos, logged_in_auth):
    logged_in_auth.logged_in_at = time.time() - 3700
    fake_mos.sso_alive = False

    await logged_in_auth.keepalive()

    assert logged_in_auth.state is LoginState.LOGGED_IN
    assert await logged_in_auth.token() == fake_mos.issued_tokens[-1]
    requests_before = len(fake_mos.requests)
    await logged_in_auth.keepalive()
    assert len(fake_mos.requests) == requests_before


async def test_keepalive_tolerates_unreachable_mos_ru(fake_mos, logged_in_auth):
    logged_in_auth.logged_in_at = time.time() - 3700
    fake_mos.renewal_status = 503

    await logged_in_auth.keepalive()

    assert logged_in_auth.state is LoginState.LOGGED_IN
    assert logged_in_auth.last_renewal is None
