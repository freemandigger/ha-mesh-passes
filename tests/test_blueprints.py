from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "mesh_passes"


class Input(str):
    pass


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def load(name):
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=BlueprintLoader)


def test_pass_notification():
    blueprint = load("pass_notification.yaml")
    meta = blueprint["blueprint"]
    assert meta["domain"] == "automation"
    assert set(meta["input"]) == {"pass_event", "event_types", "actions"}
    assert meta["input"]["event_types"]["default"] == ["exit"]
    assert blueprint["mode"] == "queued"
    assert blueprint["triggers"] == [{"trigger": "state", "entity_id": "pass_event"}]
    assert isinstance(blueprint["triggers"][0]["entity_id"], Input)
    assert {"child", "direction", "time", "school", "person"} <= set(blueprint["variables"])
    for name, value in blueprint["variables"].items():
        if name == "event_types":
            continue
        assert "if trigger.to_state" in value
    condition = blueprint["conditions"][0]["value_template"]
    assert "total_seconds() < 60" in condition
    assert "unavailable" in condition
    assert "trigger.to_state is not none" in condition
    assert blueprint["actions"] == "actions"


def test_session_alert():
    blueprint = load("session_alert.yaml")
    meta = blueprint["blueprint"]
    assert set(meta["input"]) == {"status_sensor", "api_error_hours", "actions"}
    assert meta["input"]["status_sensor"]["default"] == "sensor.mesh_account_status"
    assert meta["input"]["api_error_hours"]["default"] == 3
    assert [(t["to"], t["id"]) for t in blueprint["triggers"]] == [
        ("auth_required", "auth_required"),
        ("api_error", "api_error"),
    ]
    assert blueprint["triggers"][1]["for"]["hours"] == "api_error_hours"
    assert blueprint["variables"]["reason"] == "{{ trigger.id }}"
    assert blueprint["actions"] == "actions"
