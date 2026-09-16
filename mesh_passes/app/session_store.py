import json
import os
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from pathlib import Path

import aiohttp
from yarl import URL

from app.mesh import Child


@dataclass
class SessionData:
    profile_id: int | None
    children: list[Child]
    logged_in_at: float | None


def write_private_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(tmp, path)


def _add_cookie(jar: aiohttp.CookieJar, name: str, value: str, domain: str, path: str, expires: str) -> None:
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    morsel["path"] = path or "/"
    morsel["domain"] = domain
    if expires:
        morsel["expires"] = expires
    jar.update_cookies(cookie, URL(f"https://{domain.lstrip('.')}/"))


def save_session(path: Path, jar: aiohttp.CookieJar, data: SessionData) -> None:
    cookies = [
        {"name": m.key, "value": m.value, "domain": m["domain"], "path": m["path"], "expires": m["expires"]}
        for m in jar
        if m["domain"]
    ]
    write_private_json(
        path,
        {
            "cookies": cookies,
            "profile_id": data.profile_id,
            "children": [asdict(child) for child in data.children],
            "logged_in_at": data.logged_in_at,
        },
    )


def load_session(path: Path, jar: aiohttp.CookieJar) -> SessionData | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw.get("cookies", []):
            _add_cookie(
                jar, item["name"], item["value"], item["domain"], item.get("path", "/"), item.get("expires", "")
            )
        return SessionData(
            raw.get("profile_id"),
            [Child(**child) for child in raw.get("children", [])],
            raw.get("logged_in_at"),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def cookie_value(jar: aiohttp.CookieJar, name: str) -> str | None:
    return next((morsel.value for morsel in jar if morsel.key == name), None)


def set_cookie(jar: aiohttp.CookieJar, name: str, value: str, fallback_url: str) -> None:
    existing = next((morsel for morsel in jar if morsel.key == name), None)
    if existing is not None and existing["domain"]:
        _add_cookie(jar, name, value, existing["domain"], existing["path"] or "/", "")
        return
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    jar.update_cookies(cookie, URL(fallback_url))
