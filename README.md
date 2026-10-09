# МЭШ: проходы — аддон Home Assistant

Проходы ребёнка через турникет школы (карта «Москвёнок») из Московской электронной школы — в Home Assistant: датчик «в школе», время входа и выхода, новые оценки и события для уведомлений.

> ⚠️ **Неофициальный проект.** Не связан с ДИТ Москвы и mos.ru. Использует неофициальный API МЭШ, который может измениться без предупреждения. Автоматизированный доступ может противоречить пользовательскому соглашению mos.ru — возможна блокировка учётной записи. Данные остаются в вашем Home Assistant. Используйте только для своих детей.

## Установка

[![Добавить репозиторий в Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes)

1. Добавьте репозиторий кнопкой выше (или вручную: Настройки → Дополнения → Магазин → ⋮ → Репозитории → `https://github.com/freemandigger/ha-mesh-passes`).
2. Установите аддон «МЭШ: проходы». Нужен MQTT-брокер (например, аддон Mosquitto) и интеграция MQTT.
3. Запустите аддон, откройте панель **МЭШ** и войдите по QR-коду из приложения «Моя Москва» или «Госуслуги Москвы» (Настройки → Безопасность → сканер QR-кода).

Подробности — в [документации аддона](mesh_passes/DOCS.md).

## Уведомления

Аддон сам ничего не отправляет: он создаёт события в Home Assistant, а что и куда слать (Telegram, push в приложение, колонка), решают ваши автоматизации. Для них есть три готовых blueprint — импортируйте нужные кнопкой и создайте по ним автоматизации (Настройки → Автоматизации и сцены → Blueprints). В blueprint проходов и оценок готовый текст сообщения лежит в переменной `{{ message }}`.

| Blueprint | Когда срабатывает | Пример сообщения |
|---|---|---|
| **МЭШ: проход ребёнка**<br>[![Импорт blueprint «МЭШ: проход ребёнка»](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmesh_passes%2Fpass_notification.yaml) | Ребёнок вошёл в школу или вышел из неё. К выходу добавляются оценки, выставленные, пока он был в школе. | 🏫 Иван: выход из школы в 14:40<br>Русский язык: 2 (Домашнее задание)<br>Математика: НВ → 4 (Цифровое домашнее задание) |
| **МЭШ: оценка**<br>[![Импорт blueprint «МЭШ: оценка»](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmesh_passes%2Fmark_notification.yaml) | Новая или исправленная оценка, когда ребёнок не в школе — например, учитель выставил её вечером или за прошлые дни. | 📘 Иван, Математика: 3 (Контрольная работа, за 16.09) |
| **МЭШ: нужен вход / API не отвечает**<br>[![Импорт blueprint «МЭШ: нужен вход»](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ffreemandigger%2Fha-mesh-passes%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmesh_passes%2Fsession_alert.yaml) | Сессия mos.ru закончилась и нужно заново войти по QR в панели аддона, или API МЭШ долго не отвечает. | Нужно заново войти в МЭШ: откройте аддон «МЭШ: проходы» в Home Assistant. |

Пример автоматизации на blueprint «МЭШ: проход ребёнка»: сообщение в тему Telegram-группы, когда ребёнок вышел из школы.

```yaml
use_blueprint:
  path: mesh_passes/pass_notification.yaml
  input:
    pass_event: event.mesh_ivan_pass
    event_types: [exit]
    actions:
      - action: telegram_bot.send_message
        data:
          chat_id: -100XXXXXXXXXX
          message_thread_id: 15
          message: "{{ message }}"
```

Для `telegram_bot` в Home Assistant 2026.x адресат указывается через `chat_id` (параметр `target` устарел), а чат должен быть в списке разрешённых чатов бота — иначе сообщение молча уйдёт в чат по умолчанию.

## Без Home Assistant OS (Docker)

```yaml
services:
  mesh-passes:
    image: ghcr.io/freemandigger/mesh-passes-amd64:latest
    restart: unless-stopped
    environment:
      MQTT_HOST: mqtt.local
      MQTT_USERNAME: mesh
      MQTT_PASSWORD: change-me
      WEB_PASSWORD: change-me
      DATA_DIR: /data
    ports:
      - "8099:8099"
    volumes:
      - ./data:/data
```

Страница входа — `http://<хост>:8099` (любое имя пользователя, пароль из `WEB_PASSWORD`). Остальные опции — переменные окружения в верхнем регистре: `POLL_INTERVAL`, `ACTIVE_FROM`, `ACTIVE_TO`, `ACTIVE_DAYS` (через запятую), `MAX_EVENT_AGE`, `MARKS_INTERVAL`, `DISCOVERY_PREFIX`, `LOG_LEVEL`.

## Разработка

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
docker compose -f docker-compose.dev.yaml up --build
```

## Благодарности

Код аддона написан с нуля. Как устроены вход в mos.ru и API МЭШ, помогли понять открытые проекты:

- [OctoDiary-py](https://github.com/OctoDiary/OctoDiary-py) и [форк Mag329](https://github.com/Mag329/OctoDiary-py), [OctoDiary-kt](https://github.com/OctoDiary/OctoDiary-kt) (MIT) — в том числе эндпоинт оценок
- [Learnify-bot](https://github.com/Mag329/Learnify-bot) (MIT)
- [mesh_expressive](https://github.com/AmetistYT/mesh_expressive) (MIT)
- [hass-mosru-water](https://github.com/kostinos/hass-mosru-water) (MIT)
- [DnevnikApi](https://github.com/RedGuyRu/DnevnikApi) (MIT)
- [libremesh](https://github.com/x3lfyn/libremesh) (MIT)
- [SchoolAPI](https://github.com/DavidZhivaev/SchoolAPI) (GPL-3.0)

## Лицензия

[MIT](LICENSE)
