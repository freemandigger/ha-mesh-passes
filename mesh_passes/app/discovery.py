import re
from collections import Counter

from app import VERSION
from app.mesh import Child

BASE = "mesh_passes"
AVAILABILITY_TOPIC = f"{BASE}/availability"
STATUSES = ["ok", "auth_required", "api_error", "outside_hours"]
AT_SCHOOL = "В школе"
NOT_AT_SCHOOL = "Не в школе"
REPO_URL = "https://github.com/freemandigger/ha-mesh-passes"
TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
        "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)  # fmt: skip


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().translate(TRANSLIT)).strip("_") or "child"


def child_slugs(children: list[Child]) -> dict[int, str]:
    slugs = {child.id: slugify(child.first_name) for child in children}
    counts = Counter(slugs.values())
    return {child_id: slug if counts[slug] == 1 else f"{slug}_{child_id}" for child_id, slug in slugs.items()}


def account_topic(profile_id: int, key: str) -> str:
    return f"{BASE}/{profile_id}/{key}"


def child_topic(profile_id: int, child_id: int, key: str) -> str:
    return f"{BASE}/{profile_id}/child/{child_id}/{key}"


def _origin() -> dict[str, str]:
    return {"name": "ha-mesh-passes", "sw_version": VERSION, "support_url": REPO_URL}


def account_discovery(prefix: str, profile_id: int) -> tuple[str, dict]:
    uid = f"{BASE}_{profile_id}"
    config = {
        "device": {
            "identifiers": [uid],
            "name": "МЭШ — учётная запись",
            "manufacturer": "ha-mesh-passes",
            "sw_version": VERSION,
        },
        "origin": _origin(),
        "availability_topic": AVAILABILITY_TOPIC,
        "components": {
            "status": {
                "platform": "sensor",
                "unique_id": f"{uid}_status",
                "name": "Статус",
                "device_class": "enum",
                "options": STATUSES,
                "icon": "mdi:school",
                "state_topic": account_topic(profile_id, "status"),
                "default_entity_id": "sensor.mesh_account_status",
            },
            "last_poll": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_poll",
                "name": "Последний успешный опрос",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "state_topic": account_topic(profile_id, "last_poll"),
                "default_entity_id": "sensor.mesh_account_last_poll",
            },
            "token_expires": {
                "platform": "sensor",
                "unique_id": f"{uid}_token_expires",
                "name": "Токен действует до",
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "state_topic": account_topic(profile_id, "token_expires"),
                "default_entity_id": "sensor.mesh_account_token_expires",
            },
            "poll_now": {
                "platform": "button",
                "unique_id": f"{uid}_poll_now",
                "name": "Проверить сейчас",
                "command_topic": account_topic(profile_id, "poll_now/set"),
                "payload_press": "PRESS",
                "default_entity_id": "button.mesh_account_poll_now",
            },
        },
    }
    return f"{prefix}/device/{uid}/config", config


def child_discovery(prefix: str, profile_id: int, child: Child, slug: str) -> tuple[str, dict]:
    uid = f"{BASE}_{profile_id}_{child.id}"
    state_topic = child_topic(profile_id, child.id, "state")
    config = {
        "device": {
            "identifiers": [uid],
            "name": f"{child.first_name} ({child.class_name})" if child.class_name else child.first_name,
            "manufacturer": "ha-mesh-passes",
            "via_device": f"{BASE}_{profile_id}",
        },
        "origin": _origin(),
        "availability_topic": AVAILABILITY_TOPIC,
        "components": {
            "status": {
                "platform": "sensor",
                "unique_id": f"{uid}_status",
                "name": "Сейчас",
                "device_class": "enum",
                "options": [AT_SCHOOL, NOT_AT_SCHOOL],
                "icon": "mdi:school",
                "state_topic": state_topic,
                "value_template": f"{{{{ '{AT_SCHOOL}' if value_json.at_school == 'ON' else '{NOT_AT_SCHOOL}' }}}}",
                "json_attributes_topic": state_topic,
                "json_attributes_template": "{{ {'school': value_json.school, 'visits': value_json.visits} | tojson }}",
                "default_entity_id": f"sensor.mesh_{slug}_status",
            },
            "last_entry": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_entry",
                "name": "Последний вход",
                "device_class": "timestamp",
                "icon": "mdi:login",
                "state_topic": state_topic,
                "value_template": "{{ value_json.last_entry }}",
                "default_entity_id": f"sensor.mesh_{slug}_last_entry",
            },
            "last_exit": {
                "platform": "sensor",
                "unique_id": f"{uid}_last_exit",
                "name": "Последний выход",
                "device_class": "timestamp",
                "icon": "mdi:logout",
                "state_topic": state_topic,
                "value_template": "{{ value_json.last_exit }}",
                "default_entity_id": f"sensor.mesh_{slug}_last_exit",
            },
            "pass": {
                "platform": "event",
                "unique_id": f"{uid}_pass",
                "name": "Проход",
                "event_types": ["entry", "exit"],
                "icon": "mdi:door-open",
                "state_topic": child_topic(profile_id, child.id, "event"),
                "default_entity_id": f"event.mesh_{slug}_pass",
            },
            "mark": {
                "platform": "event",
                "unique_id": f"{uid}_mark",
                "name": "Оценка",
                "event_types": ["new", "changed"],
                "icon": "mdi:notebook-edit",
                "state_topic": child_topic(profile_id, child.id, "mark"),
                "default_entity_id": f"event.mesh_{slug}_mark",
            },
        },
    }
    return f"{prefix}/device/{uid}/config", config
