from datetime import time
from pathlib import Path

import pytest

from app.settings import SettingsError, build_settings

ADDON_ENV = {"SUPERVISOR_TOKEN": "x"}
MQTT = {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "secret"}


def test_addon_defaults():
    settings = build_settings({}, ADDON_ENV, MQTT)
    assert settings.addon is True
    assert settings.poll_interval == 3
    assert (settings.active_from, settings.active_to) == (time(7, 0), time(20, 0))
    assert settings.active_days == frozenset({0, 1, 2, 3, 4, 5})
    assert settings.max_event_age == 30
    assert settings.discovery_prefix == "homeassistant"
    assert settings.log_level == "info"
    assert (settings.mqtt_host, settings.mqtt_port) == ("core-mosquitto", 1883)
    assert (settings.mqtt_username, settings.mqtt_password) == ("addons", "secret")
    assert settings.web_password is None
    assert settings.web_port == 8099
    assert settings.data_dir == Path("/data")


def test_addon_options_override_defaults():
    settings = build_settings({"poll_interval": 5, "active_days": ["sun"], "active_from": "08:30"}, ADDON_ENV, MQTT)
    assert settings.poll_interval == 5
    assert settings.active_days == frozenset({6})
    assert settings.active_from == time(8, 30)


def test_addon_requires_mqtt_service():
    with pytest.raises(SettingsError, match="MQTT"):
        build_settings({}, ADDON_ENV, None)


def test_docker_reads_environment():
    env = {
        "WEB_PASSWORD": "pw",
        "MQTT_HOST": "broker",
        "MQTT_PORT": "1884",
        "POLL_INTERVAL": "10",
        "ACTIVE_DAYS": "mon,fri",
        "DATA_DIR": "/tmp/mesh",
    }
    settings = build_settings({}, env, None)
    assert settings.addon is False
    assert (settings.mqtt_host, settings.mqtt_port) == ("broker", 1884)
    assert settings.poll_interval == 10
    assert settings.active_days == frozenset({0, 4})
    assert settings.web_password == "pw"
    assert settings.data_dir == Path("/tmp/mesh")


def test_docker_requires_web_password():
    with pytest.raises(SettingsError, match="WEB_PASSWORD"):
        build_settings({}, {}, None)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"poll_interval": 1}, "poll_interval"),
        ({"max_event_age": 500}, "max_event_age"),
        ({"active_from": "7:00"}, "active_from"),
        ({"active_from": "20:00", "active_to": "07:00"}, "active_to"),
        ({"active_days": ["funday"]}, "active_days"),
        ({"log_level": "loud"}, "log_level"),
    ],
)
def test_invalid_options(options, message):
    with pytest.raises(SettingsError, match=message):
        build_settings(options, ADDON_ENV, MQTT)


def test_docker_web_port_from_environment():
    env = {"WEB_PASSWORD": "pw", "WEB_PORT": "9000"}
    settings = build_settings({}, env, None)
    assert settings.web_port == 9000


def test_docker_web_port_invalid():
    env = {"WEB_PASSWORD": "pw", "WEB_PORT": "0"}
    with pytest.raises(SettingsError, match="WEB_PORT"):
        build_settings({}, env, None)


def test_addon_discovery_prefix_normalization():
    settings = build_settings({"discovery_prefix": "homeassistant/"}, ADDON_ENV, MQTT)
    assert settings.discovery_prefix == "homeassistant"


def test_addon_discovery_prefix_slash_only():
    settings = build_settings({"discovery_prefix": "/"}, ADDON_ENV, MQTT)
    assert settings.discovery_prefix == "homeassistant"
