from app.discovery import account_discovery, child_discovery, child_slugs, slugify
from app.mesh import Child

IVAN = Child(101, "guid-101", "Иван", "3-А")


def test_slugify():
    assert slugify("Иван") == "ivan"
    assert slugify("Мария-Ёлка") == "mariya_yolka"
    assert slugify("") == "child"


def test_child_slugs_resolve_collisions():
    twins = [IVAN, Child(102, "guid-102", "Иван", "1-А"), Child(103, "guid-103", "Мария", "2-В")]
    assert child_slugs(twins) == {101: "ivan_101", 102: "ivan_102", 103: "mariya"}


def test_account_discovery():
    topic, config = account_discovery("homeassistant", 777)
    assert topic == "homeassistant/device/mesh_passes_777/config"
    assert config["device"]["identifiers"] == ["mesh_passes_777"]
    assert config["origin"]["name"] == "ha-mesh-passes"
    assert config["availability_topic"] == "mesh_passes/availability"
    components = config["components"]
    assert set(components) == {"status", "last_poll", "token_expires", "poll_now"}
    assert components["status"]["platform"] == "sensor"
    assert components["status"]["device_class"] == "enum"
    assert components["status"]["options"] == ["ok", "auth_required", "api_error", "outside_hours"]
    assert components["status"]["state_topic"] == "mesh_passes/777/status"
    assert components["status"]["default_entity_id"] == "sensor.mesh_account_status"
    assert components["last_poll"]["device_class"] == "timestamp"
    assert components["token_expires"]["entity_category"] == "diagnostic"
    assert components["poll_now"]["platform"] == "button"
    assert components["poll_now"]["command_topic"] == "mesh_passes/777/poll_now/set"


def test_child_discovery():
    topic, config = child_discovery("homeassistant", 777, IVAN, "ivan")
    assert topic == "homeassistant/device/mesh_passes_777_101/config"
    assert config["device"]["name"] == "Иван (3-А)"
    assert config["device"]["via_device"] == "mesh_passes_777"
    components = config["components"]
    assert components["at_school"]["platform"] == "binary_sensor"
    assert components["at_school"]["device_class"] == "presence"
    assert components["at_school"]["state_topic"] == "mesh_passes/777/child/101/state"
    assert components["at_school"]["default_entity_id"] == "binary_sensor.mesh_ivan_at_school"
    assert components["last_entry"]["value_template"] == "{{ value_json.last_entry }}"
    assert components["last_exit"]["default_entity_id"] == "sensor.mesh_ivan_last_exit"
    assert components["pass"]["platform"] == "event"
    assert components["pass"]["event_types"] == ["entry", "exit"]
    assert components["pass"]["state_topic"] == "mesh_passes/777/child/101/event"
    assert components["pass"]["default_entity_id"] == "event.mesh_ivan_pass"
