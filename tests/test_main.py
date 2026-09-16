import pytest

from app.__main__ import run


def test_settings_error_exits_with_code_1(monkeypatch, caplog):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("WEB_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        run()

    assert exit_info.value.code == 1
    assert "WEB_PASSWORD" in caplog.text
