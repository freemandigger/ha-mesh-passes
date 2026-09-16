from datetime import datetime

from app.events import MSK
from app.settings import Settings


def in_window(now: datetime, settings: Settings) -> bool:
    local = now.astimezone(MSK)
    return local.weekday() in settings.active_days and settings.active_from <= local.time() < settings.active_to
