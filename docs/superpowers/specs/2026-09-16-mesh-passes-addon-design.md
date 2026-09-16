# МЭШ: проходы — аддон Home Assistant

- **Дата:** 2026-09-16
- **Статус:** дизайн согласован, ждёт ревью спецификации
- **Репозиторий:** `github.com/freemandigger/ha-mesh-passes`, лицензия MIT

## 1. Цель и границы

Родитель московского школьника получает в Home Assistant события прохода ребёнка через турникет школы (карта «Москвёнок») и строит на них автоматизации — в первую очередь уведомление в Telegram «ребёнок вышел из школы».

Официального API для этих данных нет (data.mos.ru — только открытые справочники). Аддон использует неофициальный API МЭШ (`school.mos.ru`) под учётной записью родителя.

**Входит:**

- вход в mos.ru по QR-коду со вторым фактором SMS и «доверием устройству»;
- периодический опрос проходов всех детей из профиля родителя;
- сущности и события в HA через MQTT discovery;
- blueprints для уведомлений;
- публикация как репозиторий аддонов HA + запуск обычным Docker-контейнером.

**Не входит (сознательно):**

- вход по логину/паролю. Он требует решения proof-of-work защиты login.mos.ru от ботов — её не обходим;
- мобильный OAuth-флоу с динамической регистрацией клиента — требует секретов, извлечённых из официального приложения;
- вход звонком (flashcall), через ЕСИА/Госуслуги;
- «Моя школа» Подмосковья (school.mosreg.ru) — другая система;
- питание, баланс, оценки, расписание;
- несколько учётных записей mos.ru в одном экземпляре аддона;
- отправка уведомлений самим аддоном — это делают автоматизации HA;
- 32-битные архитектуры.

## 2. Проверенные факты об API

Проверено живым спайком 2026-09-16 на реальной учётной записи родителя (1 ребёнок). Всё, что ниже помечено «не проверено», — из исследования открытых клиентов.

### 2.1. Вход по QR

Все запросы к `login.mos.ru` идут в одной cookie-сессии.

| # | Запрос | Результат |
|---|--------|-----------|
| 1 | `GET /sps/oauth/ae` с `client_id=dnevnik.mos.ru`, `redirect_uri=https://school.mos.ru/v3/auth/sudir/callback`, `response_type=code`, `access_type=offline`, `display=script`, `code_challenge_method=S256`, `scope=birthday contacts openid profile snils blitz_change_password blitz_user_rights blitz_qr_auth`, `state=<uuid>` | JSON `items[]`, среди них `{inquire: "show_qr_code", link, expires, logo}` |
| 2 | Показать `link` как QR | — |
| 3 | `GET /sps/login/methods/qrCode/pull?_=<ms>` каждые 3 с | `command`: `showQRCode` → `askForConfirm` → `needComplete`; `needRefresh` примерно через 3 мин без скана |
| 3a | при `needRefresh`: `POST /sps/login/methods/headless/qrCode/refresh`, тело `{}`, `Content-Type: text/json`, `X-Requested-With: XMLHttpRequest` | новый `link` |
| 4 | `POST /sps/login/methods/headless/qrCode/complete` | известное устройство: cookie `aupd_token` (не проверено); новое устройство: `{inquire: "choose_one", items: [ask_to_send_sms, login_with_flashcall, go_to_web]}` |
| 5 | `POST /sps/login/methods/headless/sms/bind`, пустая форма | `{inquire: "enter_sms_code", contact (маскирован), remain_attempts: 5, ttl: 300}`, SMS отправлено |
| 6 | `POST /sps/login/methods/headless/sms/bind`, форма `sms-code=<код>`, без редиректов | `303 Location: /sps/login/ur/askToTrust`; при ошибке JSON `errors[].code`: `invalid_otp`, `expired`, `no_attempts` (коды из открытых клиентов) |
| 7 | `POST /sps/login/ur/askToTrust`, форма `action=trust` | `302` → `https://school.mos.ru/v3/auth/sudir/callback?...` → `200` |
| 8 | после callback | cookies: `aupd_token` (JWT, 24 ч), `aupd_refresh_token` (JWT, 30 дней), `Ltpatoken2` (SSO mos.ru), `Ltpaexpires` |

**Ловушка:** на шаге 7 форма с полем `trust=true` вместо `action=trust` даёт 404 и **сбрасывает незавершённый вход** — нужно начинать с QR заново.

### 2.2. Профиль и дети

- `POST https://school.mos.ru/api/ej/acl/v1/sessions`, заголовки `Authorization: Bearer <aupd_token>`, `X-Mes-Subsystem: familyweb`, тело `{"auth_token": "<aupd_token>"}` → `profiles[]` с `{id, type: "parent"}`. Это `profile_id`.
- `GET https://school.mos.ru/api/family/mobile/v1/profile`, заголовки `x-mes-subsystem: familymp`, `client-type: diary-mobile`, `profile-id: <profile_id>` → `children[]` с полями `id`, `contingent_guid`, `first_name`, `class_name`, `school` и др.

### 2.3. Проходы

`GET https://school.mos.ru/api/pass/entrances/v1/visit_durations?personId=<contingent_guid>&from=YYYY-MM-DD&to=YYYY-MM-DD`

Заголовки: `Authorization: Bearer <aupd_token>`, `X-Mes-Subsystem: familymp`, `client-type: diary-mobile`, `Profile-Id: <profile_id>`.

**Ловушка:** если токен одновременно передан в cookie `aupd_token` (или `Auth-Token`), ответ `401 "Found multiple bearer tokens in the request"`. HTTP-клиент для API не должен нести cookie-jar сессии.

Ответ:

```json
{"payload": [
  {"date": "2026-09-16", "visits": [
    {"in": "08:07", "out": "14:33", "duration": "6 ч.26 мин.",
     "isIncomplete": false, "personIn": null, "personOut": null,
     "kindId": 1, "kindName": "Общеобразовательное ОУ",
     "organizationId": 1234, "organizationName": "…",
     "organizationShortName": "ГБОУ Школа № …", "organizationAddress": "…"}
  ]}
]}
```

- Дни без проходов в `payload` отсутствуют.
- Время — местное московское, `HH:MM`.
- Пока ребёнок в школе: `out = "-"` и/или `isIncomplete = true` (по коду открытых клиентов; вживую не наблюдалось).
- Ограничение сервера — не больше 7 дней за запрос (по коду открытых клиентов).
- Задержка: выход в 14:33 был виден в API в 14:37.

### 2.4. Продление токена

- **Тихий OAuth через SSO работает:** `GET /sps/oauth/ae` с теми же параметрами, но без `display=script`, с cookies сессии → `302` на callback → новый `aupd_token` на 24 ч. Cookie `Ltpaexpires` (срок ≈ 3 ч 50 мин после входа) при этом **не продлевается**.
- `GET https://school.mos.ru/v2/token/refresh?roleId=2&subsystem=2` с cookie `aupd_token` + `aupd_refresh_token` → `201`, тело — JWT; пока токен жив, возвращается тот же токен. Только `aupd_refresh_token` → `403`. Без параметров → `499 "user roles insufficient"`.
- Поведение после смерти SSO и после истечения `aupd_token` — см. §13.

## 3. Архитектура

### 3.1. Репозиторий

```
repository.yaml                    # репозиторий аддонов HA
mesh_passes/
  config.yaml                      # опции, ingress, services: mqtt:need, arch: aarch64, amd64
  Dockerfile                       # python:3.12-slim
  DOCS.md
  CHANGELOG.md
  translations/ru.yaml, en.yaml
  app/
    main.py                        # сборка компонентов, запуск asyncio
    settings.py                    # опции аддона / переменные окружения
    auth.py                        # вход, хранение сессии, продление
    mesh.py                        # клиент API МЭШ
    poller.py                      # расписание, события
    mqtt.py                        # discovery, состояния, события
    web/                           # ingress: aiohttp-приложение, HTML, JS
blueprints/automation/mesh_passes/
  pass_notification.yaml
  session_alert.yaml
tests/
.github/workflows/ci.yaml, release.yaml
README.md
LICENSE
```

### 3.2. Компоненты

| Модуль | Ответственность | Зависит от |
|--------|-----------------|------------|
| `auth` | конечный автомат входа (§4), хранение cookies в `session.json`, выдача актуального `aupd_token`, продление (§5) | `aiohttp` |
| `mesh` | профиль, дети, `visit_durations`; ничего не знает о HA и MQTT | `auth` (получает токен) |
| `poller` | окно опроса, цикл опроса, вычисление состояний и новых событий, `state.json` | `mesh` |
| `mqtt` | discovery, публикация состояний/событий, LWT, кнопка «Проверить сейчас», реакция на `homeassistant/status` | `aiomqtt` |
| `web` | страница входа и статуса, JSON API для неё | `auth`, `poller` |

Один процесс asyncio. Зависимости: Python 3.12, `aiohttp`, `aiomqtt`, `segno`.

### 3.3. Хранение

Каталог `/data` (в Docker — `DATA_DIR`), файлы с правами 0600:

- `session.json` — cookies mos.ru/school.mos.ru, `profile_id`, список детей, время входа;
- `state.json` — ID уже опубликованных событий за 3 дня и последние известные `last_entry`/`last_exit` каждого ребёнка.

Оба файла попадают в резервные копии HA вместе с аддоном.

### 3.4. Режимы запуска

- **Аддон HA OS / Supervised:** опции из `/data/options.json`; MQTT из Supervisor services API (`services: mqtt:need`); веб-страница только через ingress (порт 8099 не публикуется).
- **Docker:** переменные окружения `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `WEB_PORT` (8099), `WEB_PASSWORD` (обязательна, Basic Auth; без неё процесс завершается с ошибкой), `DATA_DIR`, плюс опции §9 в верхнем регистре (`POLL_INTERVAL` и т.д.).

## 4. Вход и страница аддона

### 4.1. Конечный автомат

```
logged_out ──[Войти]──▶ qr_shown ──(askForConfirm)──▶ qr_confirming
    ▲                     │  ▲                              │
    │            (needRefresh: новый QR)             (needComplete)
    │                     │                                  ▼
    │   (10 мин без скана)│                           completing
    ├─────────────────────┘                     ┌────────┴─────────┐
    │                                 (aupd_token)        (choose_one)
    │                                           │                  ▼
    │                                           │             sms_required ──[код]──▶ sms_checking
    │                                           │                  ▲                     │
    │                                           │     (invalid_otp/expired/resend)       │ (303 askToTrust)
    │                                           │                  └─────────────────────┤
    │                                           ▼                                        ▼
    │                                      loading_profile ◀──────(callback)────── trusting
    │                                           │
    │                                           ▼
    ├────────────[Выйти]────────────────── logged_in ──(продление не удалось)──▶ auth_required
    └───────────────────────────────────────────────────────────────[Войти]────────────┘
```

- Любая сетевая ошибка или неожиданный ответ во время входа → `logged_out` (или `auth_required`, если вход был) с понятным текстом на странице.
- Шаги с mos.ru выполняет аддон; страница только отображает состояние и отправляет действия.

### 4.2. Страница (ingress)

- Опрашивает `GET api/status` раз в 2 с; действия — `POST api/login/start`, `api/login/sms` (`{code}`), `api/login/sms/resend`, `api/logout`, `api/poll`.
- **QR:** SVG от `segno`; подсказка: «Моя Москва» / «Госуслуги Москвы» → Настройки → Безопасность → сканер QR.
- **SMS:** поле кода, таймер по `ttl`, остаток попыток, «Отправить ещё раз».
- **Главный экран:** дети (имя, класс, в школе / не в школе, сегодняшние визиты); сессия (токен до, последнее продление, последний успешный опрос, статус); кнопки «Проверить сейчас» и «Выйти» (удаляет `session.json`, сущности остаются со статусом `auth_required`).
- Код SMS не сохраняется нигде, сразу уходит на mos.ru.

## 5. Сессия и продление

Перед каждым циклом опроса и при ответе 401/403 `auth` проверяет токен. Если до `exp` меньше 30 минут или пришёл 401/403, выполняется цепочка — до первого успеха:

1. **Тихий OAuth через SSO** (§2.4). Успех — в cookies появился новый `aupd_token`.
2. **Refresh-токен:** `GET /v2/token/refresh?roleId=2&subsystem=2` с cookie `aupd_token` + `aupd_refresh_token`. Успех — в теле JWT с `exp` позже текущего.
3. Иначе → состояние `auth_required`, опрос останавливается, статус в HA `auth_required`.

После успешного продления запрос, получивший 401, повторяется один раз. Все cookies из ответов сохраняются в `session.json`.

## 6. Опрос и события

### 6.1. Расписание

Цикл запускается каждые `poll_interval` минут, если текущее московское время в окне `active_from`–`active_to` и день в `active_days`. Вне окна — статус `outside_hours`, запросов нет. Кнопка «Проверить сейчас» запускает цикл вне расписания (и вне окна).

Список детей перезапрашивается после входа и в первом цикле каждого дня.

### 6.2. Цикл

Для каждого ребёнка:

1. `visit_durations` с `from = to = сегодня`.
2. Для каждого визита за сегодня сформировать события:
   - `entry`, если `in` не `-`: ID `"{date}|{organizationId}|in|{in}"`, время `date + in`;
   - `exit`, если `out` не `-` и `isIncomplete` не `true`: ID `"{date}|{organizationId}|out|{out}"`, время `date + out`.
3. Событие публикуется, если ID нет в `state.json` **и** `now − время ≤ max_event_age`. В обоих случаях ID записывается в `state.json`.
4. Состояние ребёнка:
   - `at_school` = последний по `in` визит сегодня имеет `out == "-"` или `isIncomplete == true`;
   - `last_entry`, `last_exit` — максимальные времена за сегодня; если сегодня проходов нет — значения из `state.json` за прошлые дни (не сбрасываются в `unknown`);
   - атрибуты: `school` (`organizationShortName`), `visits` (список `{in, out, duration}` за сегодня).
5. Из `state.json` удаляются ID старше 3 дней.

Порядок визитов в ответе не важен; несколько визитов в день поддерживаются.

### 6.3. Ошибки

| Ситуация | Действие | Статус |
|----------|----------|--------|
| 401/403 | цепочка продления §5, один повтор | `ok` или `auth_required` |
| таймаут, 5xx, 429 | следующий цикл не через `poll_interval`, а через паузу: `poll_interval`, затем ×2 при каждой новой ошибке, не больше 15 мин; если есть `Retry-After` и он больше — ждать его | `api_error` |
| неожиданная структура JSON | лог: только ключи, без значений; пауза как выше | `api_error` |
| успех | пауза сбрасывается к `poll_interval` | `ok` |

## 7. Сущности в HA (MQTT)

### 7.1. Discovery

Device-based discovery: одно retained-сообщение на устройство в `<prefix>/device/mesh_passes_<account_id>/config` и `<prefix>/device/mesh_passes_<account_id>_<child_id>/config`, где `account_id` = `profile_id`, `child_id` = `children[].id`. `unique_id` компонентов строятся из этих ID. Английские `default_entity_id`: `slug` = транслитерированное `first_name` в нижнем регистре (своя таблица транслитерации кириллицы, без внешних зависимостей); при совпадении у двух детей добавляется `_<child_id>`.

### 7.2. Устройство учётной записи

| Компонент | Ключ | Тип | Топик | Retain |
|-----------|------|-----|-------|--------|
| Статус | `status` | `sensor`, `device_class: enum`, options `ok`, `auth_required`, `api_error`, `outside_hours` | `mesh_passes/<account_id>/status` | да |
| Последний успешный опрос | `last_poll` | `sensor`, `timestamp`, diagnostic | `mesh_passes/<account_id>/last_poll` | да |
| Токен действует до | `token_expires` | `sensor`, `timestamp`, diagnostic | `mesh_passes/<account_id>/token_expires` | да |
| Проверить сейчас | `poll_now` | `button` | команда `mesh_passes/<account_id>/poll_now/set` | — |

`default_entity_id`: `sensor.mesh_account_status`, `sensor.mesh_account_last_poll`, `sensor.mesh_account_token_expires`, `button.mesh_account_poll_now`.

### 7.3. Устройство ребёнка

Имя устройства: `"<first_name> (<class_name>)"`.

| Компонент | Тип | Источник | Retain |
|-----------|-----|----------|--------|
| В школе | `binary_sensor`, `device_class: presence` | `mesh_passes/<account_id>/child/<child_id>/state` → `at_school`; атрибуты `school`, `visits` | да |
| Последний вход | `sensor`, `timestamp` | тот же топик → `last_entry` | да |
| Последний выход | `sensor`, `timestamp` | тот же топик → `last_exit` | да |
| Проход | `event`, `event_types: [entry, exit]` | `mesh_passes/<account_id>/child/<child_id>/event`, JSON `{event_type, child, time, school, person}` | **нет** |

`default_entity_id`: `binary_sensor.mesh_<slug>_at_school`, `sensor.mesh_<slug>_last_entry`, `sensor.mesh_<slug>_last_exit`, `event.mesh_<slug>_pass`.

Поля события: `child` — `first_name`; `time` — время прохода в ISO 8601 с часовым поясом Москвы; `school` — `organizationShortName`; `person` — ФИО из `personIn` (для `entry`) или `personOut` (для `exit`), если не `null`. HA кладёт все поля, кроме `event_type`, в атрибуты сущности.

### 7.4. Доступность и жизненный цикл

- LWT `mesh_passes/availability` — общий для экземпляра аддона, потому что LWT задаётся при подключении к брокеру, когда профиль может быть ещё неизвестен: `online` / `offline`, retained; все компоненты ссылаются на него.
- При `homeassistant/status = online` аддон заново публикует discovery и состояния.
- Выход из учётной записи: устройства и состояния остаются, статус `auth_required`.
- Ребёнок исчез из профиля: пустое retained-сообщение в его discovery-топик и топик состояния. Работает, пока аддон запущен; если ребёнок пропал, пока аддон был выключен, устройство удаляется вручную в HA (описано в DOCS.md).
- До первого входа `account_id` неизвестен — discovery не публикуется.

## 8. Уведомления

### 8.1. Blueprint `pass_notification.yaml` — «МЭШ: проход ребёнка»

- **Входы:** `pass_event` (entity, domain `event`); `event_types` (select multiple `entry`/`exit`, по умолчанию `exit`); `actions` (selector `action`).
- **Триггер:** изменение состояния `pass_event`.
- **Условия:** `trigger.from_state` существует; `trigger.to_state.state` не `unavailable`/`unknown`; `trigger.to_state.attributes.event_type` в `event_types`; состояние сущности (момент получения события HA) не старше 1 минуты. Проверка свежести отсекает восстановление старого состояния после перезапуска аддона или HA, но не теряет первое событие после установки (переход из `unknown`) и проходы, опубликованные с задержкой после ошибок API (их возраст уже ограничил аддон через `max_event_age`).
- **Переменные для действий:** `child`, `direction` (`event_type`), `time` (`HH:MM`, из атрибута `time`), `school`, `person`.
- **Текст-пример в описании:** `🏫 {{ child }}: выход из школы в {{ time }}`.
- **Режим:** `queued`.

### 8.2. Blueprint `session_alert.yaml` — «МЭШ: нужен вход / API не отвечает»

- **Входы:** `status_sensor` (entity, по умолчанию `sensor.mesh_account_status`); `api_error_hours` (число, по умолчанию 3, 0 — выключено); `actions`.
- **Триггеры:** `auth_required` в течение 1 мин; `api_error` в течение `api_error_hours` ч.
- **Переменная:** `reason` (`auth_required` / `api_error`).
- **Текст-пример:** «Нужно заново войти в МЭШ: откройте аддон „МЭШ: проходы“ в Home Assistant».

### 8.3. Пример для Telegram-группы с темами (README)

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
          message: "🏫 {{ child }}: выход из школы в {{ time }}"
```

В README отметить особенности `telegram_bot` в HA 2026.x: адресат — `chat_id` (не устаревший `target`); чат должен быть в разрешённых чатах бота, иначе сообщение молча уходит в чат по умолчанию.

Конкретные автоматизации владельца (его группа, тема, личный чат) — не часть этого репозитория; настраиваются в проекте его домашнего HA.

## 9. Настройки аддона

| Опция | Тип | По умолчанию | Ограничения |
|-------|-----|--------------|-------------|
| `poll_interval` | int, минуты | 3 | 2–60 |
| `active_from` | str `HH:MM` | `07:00` | — |
| `active_to` | str `HH:MM` | `20:00` | позже `active_from` |
| `active_days` | list | `[mon, tue, wed, thu, fri, sat]` | значения `mon`…`sun` |
| `max_event_age` | int, минуты | 30 | 5–240 |
| `discovery_prefix` | str | `homeassistant` | — |
| `log_level` | list | `info` | `debug`, `info`, `warning`, `error` |

Окно и возраст событий считаются по `Europe/Moscow` независимо от часового пояса контейнера.

## 10. Безопасность и приватность

- В логах только шаги входа, HTTP-статусы, ключи JSON. Никогда: токены, cookies, коды SMS, ФИО, СНИЛС, телефоны, email, адреса.
- Код SMS не сохраняется.
- `session.json`, `state.json` — права 0600.
- Аддон: страница только через ingress (авторизация HA). Docker: обязательный `WEB_PASSWORD`.
- HTTP-клиент для `school.mos.ru/api` — отдельная сессия без cookie-jar (§2.3).
- Данные уходят только на mos.ru/school.mos.ru и в локальный MQTT.
- Если mos.ru вводит новую защиту от ботов (капча, proof-of-work на QR/SMS) — аддон её не обходит, показывает ошибку входа.

## 11. Тесты, CI, публикация

### 11.1. Тесты

pytest + pytest-asyncio, HTTP подменяется (`aioresponses`), время — инъекцией часов. Реальный mos.ru в тестах не вызывается.

- **poller:** первый вход; появление выхода; два визита за день; `out="-"`/`isIncomplete`; выход без входа; перезапуск с `state.json` без дублей; старые проходы без событий; окно и дни недели; очистка ID старше 3 дней.
- **mesh:** запрос `visit_durations` несёт только `Authorization` (без cookie/`Auth-Token`); 401 → продление → один повтор; 5xx/429 → `api_error` и пауза.
- **auth:** полный путь QR → `needRefresh` → `needComplete` → `choose_one` → SMS → `askToTrust` c `action=trust` → callback; `invalid_otp`; `expired`; повторная отправка SMS; цепочка продления §5 во всех ветках.
- **mqtt:** снапшоты discovery-конфигов; `retain` у состояний, без `retain` у событий; повторная публикация по `homeassistant/status`.
- **Фикстуры:** обезличенные ответы спайка — вымышленные имена, школа, ID.

### 11.2. Линт

`ruff check`, `ruff format --check`, `frenck/action-addon-linter`.

### 11.3. CI

- `ci.yaml` (PR, push в `main`): линт, тесты, линтер аддона.
- `release.yaml` (тег `vX.Y.Z`): `docker/build-push-action` (QEMU + buildx, по одному job на архитектуру) → образы `ghcr.io/freemandigger/mesh-passes-{aarch64,amd64}:X.Y.Z` и `:latest` с метками `io.hass.*`; `config.yaml` указывает `image: ghcr.io/freemandigger/mesh-passes-{arch}`, версия в `config.yaml` совпадает с тегом.

### 11.4. Публикация

- **README:** назначение, скриншоты, кнопка «Добавить репозиторий» (my.home-assistant.io), шаги настройки, где найти сканер QR, импорт blueprints, `docker compose`.
- **Дисклеймер:** неофициальный проект, не связан с ДИТ Москвы и mos.ru; API может измениться без предупреждения; автоматизированный доступ может противоречить пользовательскому соглашению mos.ru, возможна блокировка учётной записи; данные остаются локально; использовать только для своих детей.
- `stage: experimental`, первая версия `0.1.0`, `CHANGELOG.md`.

### 11.5. Порядок выката

1. Разработка и тесты локально; прогон в Docker на Mac с локальным Mosquitto.
2. Установка на домашний HA как локальный аддон, неделя работы; автоматизации §8.3.
3. Публичный репозиторий и релиз `0.1.0`.

## 12. Риски

| Риск | Последствие | Смягчение |
|------|-------------|-----------|
| mos.ru меняет API или поток входа | аддон перестаёт работать | статус `api_error`/`auth_required`, blueprint-уведомление, быстрый релиз фикса |
| Блокировка учётной записи за автоматизацию | родитель теряет доступ к mos.ru | умеренная частота (≥ 2 мин, только в окне), дисклеймер |
| SSO и refresh-токен живут недолго | частый повторный вход | §13; «доверенное устройство» убирает SMS при повторном входе |
| Задержка появления прохода в API | уведомление позже реального выхода | ожидаемо ≤ ~7 мин при интервале 3 мин; документировано |
| Утечка `session.json` из резервной копии | доступ к МЭШ родителя до истечения токенов | права 0600, упоминание в DOCS.md |

## 13. Открытые вопросы

**Q1. Как долго живёт вход без повторного скана.** Известно (§2.4): тихий OAuth работает, пока жива SSO-сессия; `Ltpaexpires` ≈ 3 ч 50 мин после входа и продлением не сдвигается. Неизвестно: умирает ли SSO в этот момент и продлевает ли `aupd_refresh_token` (30 дней) токен после истечения `aupd_token`.

Разрешается монитором спайка, запущенным 2026-09-16 14:41: раз в час — опрос проходов и тихий OAuth; после смерти SSO — попытки `/v2/token/refresh`. Итоги ожидаются 2026-09-16 ~18:30 и 2026-09-17 ~14:40.

Архитектура от ответа не зависит — цепочка §5 пробует оба пути. От ответа зависят:

- текст README и DOCS.md об ожидаемой частоте повторного входа;
- если ни один путь не держит сессию дольше суток — порог `auth_required` в blueprint 8.2 и подсказка на странице («вход раз в сутки») становятся частью основного сценария, и перед реализацией нужно вернуться к обсуждению.
