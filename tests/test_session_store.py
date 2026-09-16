import json
from http.cookies import SimpleCookie

import aiohttp
from yarl import URL

from app.mesh import Child
from app.session_store import SessionData, cookie_value, load_session, save_session, set_cookie


def cookie(name: str, value: str, domain: str | None = None) -> SimpleCookie:
    result = SimpleCookie()
    result[name] = value
    result[name]["path"] = "/"
    if domain:
        result[name]["domain"] = domain
    return result


async def test_round_trip_keeps_domains_and_profile(tmp_path):
    path = tmp_path / "session.json"
    jar = aiohttp.CookieJar()
    jar.update_cookies(cookie("aupd_token", "tok", ".mos.ru"), URL("https://school.mos.ru/"))
    jar.update_cookies(cookie("trust_marker", "yes"), URL("https://login.mos.ru/"))
    data = SessionData(777, [Child(101, "guid-101", "Иван", "3-А")], 1_789_558_630.0)

    save_session(path, jar, data)
    assert path.stat().st_mode & 0o777 == 0o600

    restored_jar = aiohttp.CookieJar()
    assert load_session(path, restored_jar) == data
    assert restored_jar.filter_cookies(URL("https://school.mos.ru/api"))["aupd_token"].value == "tok"
    assert restored_jar.filter_cookies(URL("https://login.mos.ru/sps"))["aupd_token"].value == "tok"
    assert "trust_marker" in restored_jar.filter_cookies(URL("https://login.mos.ru/sps"))
    assert "trust_marker" not in restored_jar.filter_cookies(URL("https://school.mos.ru/"))


async def test_missing_or_broken_file(tmp_path):
    jar = aiohttp.CookieJar()
    assert load_session(tmp_path / "absent.json", jar) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_session(broken, jar) is None


async def test_broken_cookie_entry_leaves_jar_untouched(tmp_path):
    path = tmp_path / "session.json"
    payload = {
        "cookies": [
            {"name": "good", "value": "v1", "domain": "mos.ru", "path": "/", "expires": ""},
            {"name": "bad", "value": "v2"},
        ],
        "profile_id": 777,
        "children": [],
        "logged_in_at": None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    jar = aiohttp.CookieJar()
    assert load_session(path, jar) is None
    assert list(jar) == []


async def test_set_cookie_replaces_value_in_same_domain():
    jar = aiohttp.CookieJar()
    jar.update_cookies(cookie("aupd_token", "old", ".mos.ru"), URL("https://school.mos.ru/"))
    set_cookie(jar, "aupd_token", "new", "https://school.mos.ru")
    assert [morsel.value for morsel in jar if morsel.key == "aupd_token"] == ["new"]
    assert cookie_value(jar, "aupd_token") == "new"
    assert cookie_value(jar, "missing") is None
