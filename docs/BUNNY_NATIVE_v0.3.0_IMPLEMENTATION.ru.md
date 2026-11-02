# JacRed Bunny Native v0.3.0 — Compatibility Freeze / Capacity Audit

Статус: **SOURCE AUDIT IMPLEMENTED / OFFLINE PASS / PRODUCTION DATA + BUNNY DB LIVE NOT TESTED**.

Ветка разработки: `feat/bunny-native-v0.3.0-source-capacity`. Исходный baseline: `4d96b86b25b856d311b2740a6819a4a9c6b96bf7`.

## Что реализовано в v0.3.0

- Read-only аудит C# контроллеров: явные route-атрибуты, методы, номер строки, файл. Результат **не равен** полной таблице ASP.NET runtime endpoint routing: conventions, dynamic routes, middleware и правила авторизации требуют отдельного запуска .NET-приложения.
- Инвентаризация всех 25 адаптеров из `Data/init.yaml`, файлов `Infrastructure/Trackers/*` и **активных, а не закомментированных** записей `Data/crontab`. Каждый источник помечен `NOT_TESTED`, пока реально не проверен.
- Метаданные **настоящей локальной FileDB**: количество файлов, размеры `Data/fdb/**`, `masterDb*.bz`, `Data/tracks/**`, фактическая gzip-magic подпись файла с расширением `.bz`.
- Необязательная проверка небольшого числа gzip JSON-файлов с ограничением 8 MB распакованных данных на файл. Аудит выводит только агрегированные числа, не названия торрентов, магнет-ссылки, cookie или реквизиты трекеров.
- Калькулятор **сценариев**, а не измеренного размера SQL: расширение 2x/4x/8x относительно исходного сжатого объёма, 750 MB usable на шард (запас 25%), до 49 шардов + 1 каталог в рамках 50 БД.
- Опциональный синтетический SQLite+FTS5 тест в локальном временном файле; это **не** тест Bunny SQL API, удалённых реплик или стоимости запросов.
- 12 независимых Python unit tests, GitHub Actions workflow и JSON-артефакт для проверки. Обычный рабочий JacRed, парсеры и производственная FileDB не изменены.

## Запуск на Mac/Linux

Из рабочей копии репозитория:

```bash
python3 -m unittest discover -s tests -p 'test_bunny_native_audit.py' -v

python3 tools/bunny_native_audit.py \
  --strict \
  --synthetic-sqlite 1500 \
  --output "$HOME/Downloads/jacred-bunny-v0.3-source-audit.json"
```

Результат без реальных данных обязан показать `NO_REAL_DATASET`, а не выдуманное количество торрентов. В v0.3.0.1 исправлена ложная детекция: `Data/fdb/.gitignore` и другие placeholder/README не считаются содержимым FileDB. Учитываются только файлы канонического MD5-формата (оба поддерживаемых уровня каталогов), `masterDb*.bz` с сигнатурой GZip и реальные файлы tracks. Наличие таких файлов ещё не означает подтверждения целостности каталога.

**Для реальной ёмкости** нужно сначала иметь локальную копию оригинальной `Data` с `fdb/`, `masterDb*.bz` и `tracks/`. Например:

```bash
python3 tools/bunny_native_audit.py \
  --strict \
  --data-root "/ABSOLUTE/PATH/TO/JACRED/Data" \
  --sample-shards 20 \
  --synthetic-sqlite 1500 \
  --output "$HOME/Downloads/jacred-bunny-v0.3-dataset-audit.json"
```

**Важно:** этот инструмент ничего не выгружает из Bunny и не получает доступ к production Data автоматически. Если данные находятся только в Magic Container, требуется отдельно организованный экспорт и безопасное копирование или запуск инструмента внутри окружения с доступом на чтение. Не отправляйте личные cookie, логины, magnet/пасскеи и дампы FileDB в публичные issues.

Команда source-only требует Python 3.9+ и SQLite с FTS5, никаких Bunny/GitHub токенов. Во время synthetic SQL создаётся только временный локальный файл. Report path должен быть вне каталога `Data`.

## Источник истины для таблицы совместимости

`Controllers/**` + `Program.cs` + `Infrastructure/Security/**` (включая middleware) = исходный контракт маршрутов. Запись `UNSPECIFIED` у метода означает, что атрибут не ограничил HTTP-глаг; это не подтверждённый список разрешённых HTTP-методов. Статический аудит не покрывает case-insensitive routing, CORS, rate limit или security side effects.

Контролируемые функциональные области:
1. API Jackett `/api/v2.0/indexers/**`, обычные торренты `/api/v1.0/**`, Torznab/Prowlarr, категории и сортировка.
2. Web UI и конфигурация, management endpoints с отдельными ключами, health, logs и статистика.
3. Sync endpoints `/sync/**`, masterDb revisions и cache freshness.
4. 25 парсеров/cron, checkpointed backfills и запуски после рестарта.
5. ffprobe/дорожки/языки, dedup, search normalization и migration maintenance.

**Golden HTTP response fixtures не захвачены**: для этого нужен запущенный оригинальный .NET JacRed с разрешённым/синтетическим тестовым каталогом. Нельзя синтезировать эталонные результаты без запуска upstream. Их создание — обязательный незакрытый gate v0.3.0.

## Технические находки

- `Infrastructure/Persistence/JsonStream.cs` использует `GZipStream`; расширение `.bz` у masterDb не отражает алгоритм. Мигратору нельзя декодировать его BZip2.
- `FileDB.masterDb` — отдельный индекс ключей, а `FastDbIndex` создаёт поисковые aliases из частей ключа. Оба должны иметь эквиваленты в SQL.
- Из `Data/init.yaml` известны 25 названий интеграций. Реальная работоспособность, токены, правила доступа и доступность третьих сторон пока не проверены.
- Исходная база с миллионами записей отсутствует в публичном git checkout. Реальная вместимость после SQL/FTS migration не измерена.
- Bunny Database documented Public Preview limits: 50 databases/account и 1 GB/database. Увеличение лимитов возможно по запросу в поддержку. Источник: https://bunny.net/docs/database/limits
- Edge Scripting: 30s CPU/request, 128MB active memory, 50 subrequests. Источник: https://bunny.net/docs/scripting/limits
- Репликация Bunny Database асинхронная, а межинстансовая read-after-write гарантия требует replication index либо чтений на primary в транзакции. Источник: https://bunny.net/docs/database/replication
- Для дорогих/высокочастотных задач индексирования Edge Scripting не используется; постоянные workers остаются в Magic Containers.

## Gate matrix

| Приёмка | Состояние |
|---|---|
| Source API attribute inventory | PASS (static only; CI report) |
| 25 named source adapters inventoried | PASS (source only) |
| GZip FileDB file format evidence | PASS (source + offline fixture) |
| Local SQLite/FTS feasibility | PASS (synthetic CI only) |
| Real JacRed FileDB size and record count | NOT RUN — private dataset unavailable |
| Upstream .NET golden HTTP fixtures | NOT RUN |
| Bunny Database live URL/auth and SQL migrations | NOT RUN |
| Live query p50/p95, cost and DB quota verification | NOT RUN |
| End-to-end source connectivity for 25 trackers | NOT RUN |
| Multi-region cross-instance consistency | NOT RUN |
| FileDB cutover or production deployment | DISABLED; no change |

## Следующий этап

**v0.4.0 — Bunny SQL Storage Engine & FileDB Import.** Начать проектировать typed schema и importer, используя этот аудит как исходный контракт; до реального authoritative cutover требуется фактическая production data audit, golden fixtures и доказанная возможность хранить весь dataset в доступных шардированных Bunny Database.

Canonical roadmap: [BUNNY_NATIVE_REWRITE_PLAN.ru.md](BUNNY_NATIVE_REWRITE_PLAN.ru.md).
