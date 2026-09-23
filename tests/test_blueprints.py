from datetime import datetime
from pathlib import Path

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "mesh_passes"


class Input(str):
    pass


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def load(name):
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=BlueprintLoader)


ENV = NativeEnvironment()
NOW = datetime(2026, 9, 23, 17, 0)


def render(blueprint, inputs, attributes):
    context = {"trigger": {"to_state": {"state": "2026-09-23T14:41:00+00:00", "attributes": attributes}}}
    context["now"] = lambda: NOW
    for name, template in blueprint["variables"].items():
        if isinstance(template, Input):
            context[name] = inputs[template]
        else:
            value = ENV.from_string(template).render(**context)
            context[name] = "" if value is None else value
    return context


def test_pass_notification():
    blueprint = load("pass_notification.yaml")
    meta = blueprint["blueprint"]
    assert meta["domain"] == "automation"
    assert set(meta["input"]) == {"pass_event", "event_types", "include_marks", "skip_marks", "actions"}
    assert meta["input"]["event_types"]["default"] == ["exit"]
    assert meta["input"]["include_marks"]["default"] is True
    assert meta["input"]["skip_marks"]["default"] == ["См"]
    assert blueprint["mode"] == "queued"
    assert blueprint["triggers"] == [{"trigger": "state", "entity_id": "pass_event"}]
    assert isinstance(blueprint["triggers"][0]["entity_id"], Input)
    for name in ("child", "direction", "time", "school", "person", "marks"):
        assert "if trigger.to_state" in blueprint["variables"][name]
    assert {"marks_text", "message"} <= set(blueprint["variables"])
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


PASS_INPUTS = {"event_types": ["exit"], "include_marks": True, "skip_marks": ["См"]}
EXIT_MARKS = [
    {
        "kind": "new",
        "subject": "Русский язык",
        "value": "2",
        "previous": None,
        "date": "2026-09-23",
        "control_form": "Домашнее задание",
        "weight": 1,
        "is_exam": False,
    },
    {
        "kind": "new",
        "subject": "Математика",
        "value": "См",
        "previous": None,
        "date": "2026-09-23",
        "control_form": "Цифровое домашнее задание",
        "weight": 1,
        "is_exam": False,
    },
    {
        "kind": "changed",
        "subject": "Математика",
        "value": "4",
        "previous": "НВ",
        "date": "2026-09-23",
        "control_form": "Цифровое домашнее задание",
        "weight": 1,
        "is_exam": False,
    },
    {
        "kind": "new",
        "subject": "Математика",
        "value": "3",
        "previous": None,
        "date": "2026-09-16",
        "control_form": "Контрольная работа",
        "weight": 2,
        "is_exam": True,
    },
]


def exit_attributes(marks):
    return {
        "event_type": "exit",
        "child": "Иван",
        "time": "2026-09-23T14:40:00+03:00",
        "school": "ГБОУ Школа № 1",
        "person": None,
        "marks": marks,
    }


def test_exit_message_lists_marks():
    context = render(load("pass_notification.yaml"), PASS_INPUTS, exit_attributes(EXIT_MARKS))

    assert [mark["value"] for mark in context["marks"]] == ["2", "4", "3"]
    assert context["message"] == (
        "🏫 Иван: выход из школы в 14:40\n"
        "Русский язык: 2 (Домашнее задание)\n"
        "Математика: НВ → 4 (Цифровое домашнее задание)\n"
        "Математика: 3 (Контрольная работа, за 16.09)"
    )


def test_exit_message_without_marks_when_disabled():
    inputs = {**PASS_INPUTS, "include_marks": False}
    context = render(load("pass_notification.yaml"), inputs, exit_attributes(EXIT_MARKS))

    assert context["marks_text"] == ""
    assert context["message"] == "🏫 Иван: выход из школы в 14:40"


def test_exit_message_when_all_marks_skipped():
    context = render(load("pass_notification.yaml"), PASS_INPUTS, exit_attributes([EXIT_MARKS[1]]))
    assert context["message"] == "🏫 Иван: выход из школы в 14:40"


def test_entry_message_has_no_marks():
    attributes = {"event_type": "entry", "child": "Иван", "time": "2026-09-23T08:02:00+03:00", "school": None}
    context = render(load("pass_notification.yaml"), PASS_INPUTS, attributes)
    assert context["message"] == "🏫 Иван: вход в школу в 08:02"


def test_mark_notification():
    blueprint = load("mark_notification.yaml")
    meta = blueprint["blueprint"]
    assert meta["domain"] == "automation"
    assert set(meta["input"]) == {"mark_event", "event_types", "skip_marks", "actions"}
    assert meta["input"]["event_types"]["default"] == ["new", "changed"]
    assert meta["input"]["skip_marks"]["default"] == ["См"]
    assert blueprint["mode"] == "queued"
    assert blueprint["max"] == 50
    assert blueprint["triggers"] == [{"trigger": "state", "entity_id": "mark_event"}]
    assert isinstance(blueprint["triggers"][0]["entity_id"], Input)
    for name in ("child", "kind", "subject", "value", "previous", "date", "control_form", "weight", "is_exam"):
        assert "if trigger.to_state" in blueprint["variables"][name]
    condition = blueprint["conditions"][0]["value_template"]
    assert "total_seconds() < 60" in condition
    assert "trigger.to_state.attributes.event_type in event_types" in condition
    assert "trigger.to_state.attributes.value not in skip_marks" in condition
    assert blueprint["actions"] == "actions"


MARK_INPUTS = {"event_types": ["new", "changed"], "skip_marks": ["См"]}


def mark_attributes(**changes):
    attributes = {
        "event_type": "new",
        "child": "Иван",
        "subject": "Математика",
        "value": "3",
        "previous": None,
        "date": "2026-09-16",
        "control_form": "Контрольная работа",
        "weight": 2,
        "is_exam": True,
    }
    return {**attributes, **changes}


@pytest.mark.parametrize(
    ("attributes", "message"),
    [
        (mark_attributes(), "📘 Иван, Математика: 3 (Контрольная работа, за 16.09)"),
        (
            mark_attributes(
                event_type="changed",
                value="4",
                previous="НВ",
                date="2026-09-23",
                control_form="Цифровое домашнее задание",
            ),
            "📘 Иван, Математика: НВ → 4 (Цифровое домашнее задание)",
        ),
        (mark_attributes(subject="Музыка", value="5", date="2026-09-23", control_form=None), "📘 Иван, Музыка: 5"),
    ],
)
def test_mark_message(attributes, message):
    assert render(load("mark_notification.yaml"), MARK_INPUTS, attributes)["message"] == message
