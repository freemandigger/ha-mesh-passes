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
