import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LOG_LEVELS = ("debug", "info", "warning", "error")
DEFAULT_OPTIONS: dict[str, object] = {
    "poll_interval": 3,
    "active_from": "07:00",
    "active_to": "20:00",
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "max_event_age": 30,
    "discovery_prefix": "homeassistant",
    "log_level": "info",
}


class SettingsError(ValueError):
    """Недопустимые настройки аддона."""


@dataclass(frozen=True)
class Settings:
    addon: bool
    poll_interval: int
    active_from: time
    active_to: time
    active_days: frozenset[int]
    max_event_age: int
    discovery_prefix: str
    log_level: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    web_port: int
    web_password: str | None
    data_dir: Path


def _hhmm(name: str, value: object) -> time:
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise SettingsError(f"{name}: ожидается ЧЧ:ММ, получено {value!r}")
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _int(name: str, value: object, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name}: ожидается целое число, получено {value!r}") from None
    if not low <= number <= high:
        raise SettingsError(f"{name}: допустимо {low}–{high}, получено {number}")
    return number


def _days(value: object) -> frozenset[int]:
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple) or not items:
        raise SettingsError(f"active_days: ожидается список дней, получено {value!r}")
    days = set()
    for item in items:
        day = str(item).strip().lower()
        if day not in DAYS:
            raise SettingsError(f"active_days: неизвестный день {item!r}")
        days.add(DAYS.index(day))
    return frozenset(days)


def _options_from_env(env: Mapping[str, str]) -> dict[str, object]:
    return {key: env[key.upper()] for key in DEFAULT_OPTIONS if key.upper() in env}


def build_settings(
    options: Mapping[str, object], env: Mapping[str, str], mqtt_service: Mapping[str, object] | None
) -> Settings:
    addon = "SUPERVISOR_TOKEN" in env
    merged = {**DEFAULT_OPTIONS, **(options if addon else _options_from_env(env))}
    active_from = _hhmm("active_from", merged["active_from"])
    active_to = _hhmm("active_to", merged["active_to"])
    if active_to <= active_from:
        raise SettingsError("active_to должно быть позже active_from")
    log_level = str(merged["log_level"]).lower()
    if log_level not in LOG_LEVELS:
        raise SettingsError(f"log_level: неизвестный уровень {log_level!r}")

    if addon:
        if not mqtt_service:
            raise SettingsError("MQTT недоступен: установите и запустите брокер MQTT (например, аддон Mosquitto)")
        mqtt_host = str(mqtt_service["host"])
        mqtt_port = int(mqtt_service["port"])
        mqtt_username = str(mqtt_service.get("username") or "") or None
        mqtt_password = str(mqtt_service.get("password") or "") or None
        web_password = None
    else:
        mqtt_host = env.get("MQTT_HOST", "localhost")
        mqtt_port = _int("MQTT_PORT", env.get("MQTT_PORT", "1883"), 1, 65535)
        mqtt_username = env.get("MQTT_USERNAME") or None
        mqtt_password = env.get("MQTT_PASSWORD") or None
        web_password = env.get("WEB_PASSWORD") or None
        if not web_password:
            raise SettingsError("WEB_PASSWORD обязателен при запуске вне Home Assistant")

    return Settings(
        addon=addon,
        poll_interval=_int("poll_interval", merged["poll_interval"], 2, 60),
        active_from=active_from,
        active_to=active_to,
        active_days=_days(merged["active_days"]),
        max_event_age=_int("max_event_age", merged["max_event_age"], 5, 240),
        discovery_prefix=str(merged["discovery_prefix"]),
        log_level=log_level,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        web_port=8099,
        web_password=web_password,
        data_dir=Path(env.get("DATA_DIR", "/data")),
    )
