from datetime import UTC, datetime

import pytest

from app.events import MSK
from app.schedule import in_window
from app.settings import build_settings

SETTINGS = build_settings({}, {"SUPERVISOR_TOKEN": "x"}, {"host": "broker", "port": 1883})


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 16, 7, 0, tzinfo=MSK), True),
        (datetime(2026, 9, 16, 19, 59, tzinfo=MSK), True),
        (datetime(2026, 9, 16, 20, 0, tzinfo=MSK), False),
        (datetime(2026, 9, 16, 6, 59, tzinfo=MSK), False),
        (datetime(2026, 9, 20, 12, 0, tzinfo=MSK), False),
        (datetime(2026, 9, 16, 4, 0, tzinfo=UTC), True),
    ],
)
def test_in_window(now, expected):
    assert in_window(now, SETTINGS) is expected
