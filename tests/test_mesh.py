import time
from datetime import UTC, date, datetime

import pytest

from app.mesh import Child, Mark, MeshApiError, MeshAuthError, MeshClient, Visit
from app.tokens import jwt_exp
from fake_mos import make_jwt, make_mark

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


MONTH_AGO = date(2026, 8, 18)


async def test_marks_parsed(fake_mos, mesh, token):
    fake_mos.marks["101"] = [
        make_mark(1, "5", subject="Русский язык", form="Диктант"),
        make_mark(2, "3", day="2026-09-15", form="Контрольная работа", weight=2, exam=True),
        make_mark(3, "НВ", form=None, weight=None),
        make_mark(4, ""),
    ]

    marks = await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)

    assert marks == [
        Mark(1, DAY, "Русский язык", "5", 1, "Диктант", False),
        Mark(2, date(2026, 9, 15), "Математика", "3", 2, "Контрольная работа", True),
        Mark(3, DAY, "Математика", "НВ", None, None, False),
    ]


async def test_marks_request_carries_only_bearer(fake_mos, mesh, token):
    await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)
    request = fake_mos.requests[-1]
    assert request.path == "/api/family/mobile/v1/marks"
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert request.headers["x-mes-subsystem"] == "familymp"
    assert request.headers["client-type"] == "diary-mobile"
    assert request.headers["profile-id"] == "777"
    assert "Cookie" not in request.headers
    assert "Auth-Token" not in request.headers
    assert request.query == {"student_id": "101", "from": "2026-08-18", "to": "2026-09-16"}


async def test_marks_unauthorized(fake_mos, mesh, token):
    fake_mos.marks_statuses = [401]
    with pytest.raises(MeshAuthError):
        await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)


async def test_marks_server_error(fake_mos, mesh, token):
    fake_mos.marks_statuses = [503]
    with pytest.raises(MeshApiError):
        await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)


async def test_marks_unexpected_payload(fake_mos, mesh, token):
    fake_mos.marks_override = {"unexpected": True}
    with pytest.raises(MeshApiError, match="unexpected"):
        await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)


async def test_marks_payload_item_not_a_dict(fake_mos, mesh, token):
    fake_mos.marks_override = {"payload": ["junk"]}
    with pytest.raises(MeshApiError):
        await mesh.marks(token, 777, CHILD, MONTH_AGO, DAY)
