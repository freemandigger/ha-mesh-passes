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
                Child(
                    int(item["id"]),
                    str(item["contingent_guid"]),
                    str(item["first_name"]),
                    str(item.get("class_name") or ""),
                )
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
