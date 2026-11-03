# JacRed Bunny Native v0.5.0 — Edge Scripting API & Compatibility Preview

**Состояние: EDGE PREVIEW CODE COMPLETE / OFFLINE TESTS PASS / BUNDLE BUILD PASS / LIVE BUNNY DEPLOY NOT RUN / 1:1 PARITY NOT CERTIFIED / PRODUCTION CUTOVER OFF.**

Канонический репозиторий: https://github.com/vbrekher/jacred-bunny

## Что разработано

Создан отдельный серверный API-слой на JavaScript для Bunny Edge Scripting. Запуск в Bunny — через `@bunny.net/edgescript-sdk@0.12.1`, чтение SQL — через `@bunny.net/database-client@0.0.3`. Подтверждён реальный **build-бандл** в GitHub Actions, но не развёртывание в аккаунте Bunny.

- `bunny-native/edge/src/app.mjs`: единый Web Request/Response handler для совместимых путей, авторизация и защита.
- `bunny-native/edge/src/sql.mjs`: только подготовленные SQL-запросы с ограниченным FTS5 MATCH и лимитами 1–100 результатов. FTS-парсер не принимает необработанный синтаксис запроса.
- `bunny-native/edge/src/format.mjs`: формат JacRed Jackett `{Results, jacred}`, XML Torznab RSS/caps/indexers, release JSON Prowlarr. Экранирование XML и поля оригинальных `Models/Api/Result.cs`.
- `bunny-native/edge/src/catalog.mjs`: список 25 исходных парсеров (фактическая доступность по сети НЕ ПРОВЕРЕНА).
- `bunny-native/edge/entry.mjs`: standalone Bunny Edge SDK + SQL клиент; секреты остаются на сервере.
- `bunny-native/edge/tests/api.test.mjs`: 35 автономных тестов безопасности/совместимости со строго контролируемым mock SQL; эти тесты НЕ имитируют полноту исходного .NET-поиска.
- Улучшенный `tools/bunny_native_export.py` передаёт хеш оригинального `masterDb`. `bunny-native/remote-import.mjs` теперь соответствует реальной форме `.all()` в официальном клиенте (массив строк), а запись `native_imports` выполняется строго после проверки количества импортированных записей. Прерванный импорт не получает признак готовности.

## Маршруты v0.5.0

| Маршрут | Поведение |
|---|---|
| `GET /health` | 200 только при включённом preview + подтверждённом количестве бакетов, торрентов и инвентаре tracks; иначе 503 |
| `GET /api/v1.0/conf` | Совместимые `jacred`, `configured`, `apikey`, `version`; секрет не возвращается |
| `GET /api/v1.0/trackers` | 25 имён исходных источников |
| `GET /api/v2.0/indexers` | Jackett indexer metadata — `all` + 25 источников |
| `GET /api/v2.0/indexers/{indexer}/results` | SQL/FTS5 → Jackett JSON preview |
| `GET /torznab/api` | `t=caps`, `t=indexers`, ограниченный `t=search`/movie/tv preview |
| `GET /api/v2.0/indexers/{indexer}/results/torznab/api` | Аналог Torznab |
| `GET /api/v1/indexer/{indexer}/newznab` | Аналог Torznab |
| `GET /api/v1/indexer`, `/api/v1/indexer/1` | Prowlarr indexer list/detail |
| `GET /api/v1/search` | SQL/FTS5 → Prowlarr release JSON preview |
| `GET /api/v1.0/torrents` | SQL → исходная форма JSON результата с базовыми фильтрами (preview) |
| `GET /api/v1.0/qualitys`, `/sync/**`, `/cron/**`, `/api/v1.0/config/**` | HTTP 501 `NOT_IMPLEMENTED` вместо обманчивого пустого ответа |

**Несовпадения с оригиналом (НЕ замалчивать):** сортировка и рейтинги `FastDbIndex`, полное интеллектуальное сопоставление фильмов/сезонов, merge magnet-дубликатов, ID-resolver IMDb/KP/TMDB, категория `Category[0]` и все фильтры, трекер-специфичные правила, `qualitys`, часть конфигурации/админки/синхронизации, tracks enrichment и OCR/ffprobe, исторические статусы cron; эти возможности НЕ являются полным parity в данном релизе. Для настоящей замены API необходимы сравнительные golden fixtures с запущенным оригинальным .NET JacRed.

## Защита и безопасная работа

- По умолчанию **OFF**: `JACRED_EDGE_PREVIEW_ENABLED` отсутствует/не равно `true` → 503.
- `JACRED_EDGE_API_KEY` обязателен и содержит не менее 16 символов. Принимается `Authorization: Bearer`, `X-Api-Key`, для совместимости со старыми клиентами `?apikey=`. Отдельный ключ от исходного JacRed.
- Заголовки `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`. Пароли и SQL-ошибки не возвращаются клиенту.
- CORS по точному списку `JACRED_EDGE_ALLOWED_ORIGINS`, не wildcard; запрос с чужого Origin получает 403. HTTPS Origin обязателен.
- Поддерживаются только GET/HEAD/OPTIONS; не реализованы write-эндпоинты. POST получает 405. HEAD без тела.
- Превью проверяет `native_imports` (манифест после завершённого импорта) и фактические счётчики SQL, чтобы **не возвращать пустую выдачу из частично загруженной базы**.
- FTS-запросы до 180 символов, максимум 8 токенов, до 100 результатов, OFFSET до 2000. Это снижает риск перегрузки; полноценные quota/abuse protections потребуют Shield/rate limits.
- **Безопасность CORS не заменяет авторизацию**, токен БД никогда не помещается в браузер, файлы или статическую страницу.

## Локальная проверка на macOS/Linux

Нужен Node.js 22+:

~~~bash
cd bunny-native/edge
npm install --no-audit --no-fund
npm test
npm run build
node --check dist/index.js
~~~

Вы должны увидеть `dist/index.js`. Тесты приложения используют контролируемый mock SQL, **не требуют реального Bunny API key**.

Также проверьте:

~~~bash
python3 -m unittest discover -s tests -p 'test_bunny_native_import.py' -v
python3 -m unittest discover -s tests -p 'test_bunny_native_audit.py' -v
~~~

GitHub Actions отдельно сохраняет **готовый для staging bundle** как артефакт `jacred-native-v0.5-edge-bundle`. Он ещё не означает опубликованный Edge Script.

## Как подготовить Bunny Edge Script без переключения JacRed

Используйте **новый отдельный тестовый Edge Script** с отключённым публичным доступом, пока нет проверки реального Bunny Database. Не используйте Pull Zone/домен действующего JacRed или Lampa.

1. Создайте пустую тестовую Bunny Database в консоли Bunny. Примените SQL миграции/проведите импорт v0.4, предварительно убедившись, что каталог помещается в квоту. В конце должен появиться `native_imports` с корректными counts; иначе Edge ответит 503.
2. Создайте в Bunny Dashboard отдельный Edge Script. В разделе Database → Access создайте **read-only** токен для Edge Script и подключите его через «Add Secrets to Edge Script»; он должен получить `BUNNY_DATABASE_URL`, `BUNNY_DATABASE_AUTH_TOKEN`. Не используйте основной ключ аккаунта.
3. Для начала **оставьте** `JACRED_EDGE_PREVIEW_ENABLED=false`. Задайте дополнительный API ключ `JACRED_EDGE_API_KEY` через Bunny Secret, достаточно длинный и случайный. При необходимости включите `JACRED_EDGE_ALLOWED_ORIGINS` для своего Web UI по HTTPS.
4. Разверните собранный `dist/index.js` **без публикации**:

~~~bash
npm install -g @bunny.net/cli
bunny login
cd bunny-native/edge
npm install --no-audit --no-fund
npm run build

# Один раз подключите CLI к отдельно созданному Edge Script:
bunny scripts link
bunny scripts deploy dist/index.js --skip-publish
~~~

Проверьте идентификатор script в CLI, secrets и региональную SQL-совместимость. Только после готовности SQL и ручного разрешения доступа можно включить `JACRED_EDGE_PREVIEW_ENABLED=true` и **опубликовать отдельную тестовую версию**. Команда для ручного сценария — `bunny scripts deployments publish <release-id>`, после просмотра `bunny scripts deployments list`. Live acceptance в текущем релизе НЕ ПРОЙДЕНА.

**Важное правило CDN:** auth-запросы не должны обслуживаться публичным cached-ответом без повторной авторизации. Выключите CDN caching для защищённых API путей либо настройте перед-кешевой запуск Edge Script по документации Bunny. Ответы имеют no-store, но поведение выбранного Pull Zone должно быть проверено отдельно.

Официальные источники:
- https://bunny.net/blog/query-bunny-database-with-fetch-and-nothing-else/
- https://bunny.net/blog/build-and-deploy-scripts-at-the-edge-with-the-bunny-net-cli/
- https://github.com/BunnyWay/edge-script-sdk

## Acceptance matrix

- Source v0.4 regression (29 Python tests): PASS in GitHub Actions.
- Node Edge app security/compatibility mock tests (35): PASS in GitHub Actions.
- SDK install + standalone JS bundle build: PASS in GitHub Actions.
- Actual remote Bunny Database FTS + stage import row-level checksum: **NOT RUN**.
- Real Edge Script deployment and access to real Bunny URL: **NOT RUN**.
- Golden JSON/XML/API differential vs .NET: **NOT RUN**.
- 25 live tracker crawls, media/Tizen/TorrServer acceptance: **NOT RUN**.
- Production cutover: **NOT PERMITTED** in v0.5.0.

### Следующий этап
**v0.5.1 — Compatibility Parity & Live Database Acceptance**, устраняем расхождения оригинального поиска и фильтров, создаём golden fixtures и подтверждаем live SQL/Edge. Только затем — v0.6.0 Tracker Automation.
