import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from app import VERSION
from app.auth import Auth
from app.discovery import REPO_URL
from app.mesh import MeshClient
from app.mqtt_runner import run_mqtt
from app.poller import Poller
from app.publisher import MqttPublisher
from app.settings import SettingsError, build_settings
from app.web.server import create_app

_LOGGER = logging.getLogger("app")
USER_AGENT = f"ha-mesh-passes/{VERSION} (+{REPO_URL})"
TIMEOUT = aiohttp.ClientTimeout(total=30)
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


async def supervisor_mqtt(token: str) -> dict | None:
    try:
        async with (
            aiohttp.ClientSession(timeout=TIMEOUT) as http,
            http.get(SUPERVISOR_MQTT_URL, headers={"Authorization": f"Bearer {token}"}) as response,
        ):
            if response.status != 200:
                return None
            return (await response.json()).get("data")
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None


def read_addon_options(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise SettingsError(f"Не удалось прочитать {path.name}: {type(err).__name__}") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path.name}: ожидается JSON-объект")
    return data


async def main() -> None:
    env = os.environ
    addon = "SUPERVISOR_TOKEN" in env
    options = read_addon_options(Path("/data/options.json")) if addon else {}
    mqtt_service = await supervisor_mqtt(env["SUPERVISOR_TOKEN"]) if addon else None
    settings = build_settings(options, env, mqtt_service)
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True
    )
    _LOGGER.info("ha-mesh-passes %s запущен (%s)", VERSION, "аддон" if addon else "Docker")

    headers = {"User-Agent": USER_AGENT}
    async with (
        aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True), headers=headers, timeout=TIMEOUT
        ) as login_http,
        aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), headers=headers, timeout=TIMEOUT) as api_http,
    ):
        mesh = MeshClient(api_http)
        auth = Auth(login_http, mesh, settings.data_dir / "session.json")
        auth.load()
        publisher = MqttPublisher(settings.discovery_prefix)
        poller = Poller(auth, mesh, publisher, settings, settings.data_dir / "state.json")
        auth.on_login = poller.request_poll

        runner = web.AppRunner(
            create_app(auth, poller, password=settings.web_password, ingress_only=settings.addon), access_log=None
        )
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.web_port).start()
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(poller.run())
                group.create_task(run_mqtt(settings, publisher, poller.request_poll))
        finally:
            await auth.close()
            await runner.cleanup()


def run() -> None:
    try:
        asyncio.run(main())
    except SettingsError as err:
        logging.basicConfig()
        _LOGGER.error("Ошибка настроек: %s", err)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
