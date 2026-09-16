import json

import pytest

from app.__main__ import read_addon_options, run, supervisor_mqtt
from app.settings import SettingsError


def test_settings_error_exits_with_code_1(monkeypatch, caplog):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("WEB_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        run()

    assert exit_info.value.code == 1
    assert "WEB_PASSWORD" in caplog.text


def test_read_addon_options_missing_and_invalid(tmp_path):
    path = tmp_path / "options.json"
    with pytest.raises(SettingsError, match="options.json"):
        read_addon_options(path)

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError):
        read_addon_options(path)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SettingsError):
        read_addon_options(path)

    path.write_text(json.dumps({"poll_interval": 5}), encoding="utf-8")
    assert read_addon_options(path) == {"poll_interval": 5}


async def test_supervisor_mqtt_unreachable_returns_none(monkeypatch):
    monkeypatch.setattr("app.__main__.SUPERVISOR_MQTT_URL", "http://127.0.0.1:9/services/mqtt")
    assert await supervisor_mqtt("token") is None
