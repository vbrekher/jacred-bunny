# JacRed Bunny Native v0.5.2 — Search Parity Expansion & Sharded SQL Query Foundation

Статус: SOURCE IMPLEMENTED / LOCAL SQLITE SHARD TESTS / LIVE BUNNY DB NOT RUN / ORIGINAL .NET GOLDEN NOT RUN / PRODUCTION CUTOVER DISABLED.

Главный репозиторий: https://github.com/vbrekher/jacred-bunny

## Что разработано

- tools/bunny_native_split.py превращает проверенный SQL stage v0.4 в 2–4 отдельных SQLite базы. Каждый бакет FileDB направляется по правилу int(md5,16) % shard_count. Исходная SQLite и FileDB не изменяются, глобальные счётчики сверяются, файловая публикация атомарная. Каталог результатов — 0700, SQL-файлы — 0600.
- migrations/0003_shard_identity.sql создаёт явный манифест: shard_index, shard_count и SHA256 исходного masterDb. Полное совпадение lineage обязано быть у каждой базы.
- tools/bunny_native_export.py переносит идентификатор шарда в приватный JSONL. bunny-native/remote-import.mjs сохраняет его в SQL при импорте в новую пустую Bunny Database. Финальный native_imports записывается после проверки всех вставок.
- bunny-native/edge/src/shards.mjs разрешает только 2, 3 или 4 перечисленных по порядку шарда; для каждого — отдельный Bunny Database URL и серверный токен. Неверный config, отсутствующая БД или любой неготовый shard приводят к отказу.
- SQL-поиск выполняется сразу по всем базам: глобально сначала точные alias-запросы, а только если не найдено ничего — частичное совпадение. Бюджет 200 исходных кандидатов делится между базами; превышение или сбой — отказ, никаких молчаливо усечённых ответов.
- Jackett / Torznab / Prowlarr получили дополнительное объединение release по 40-hex magnet BTIH. Данные сидов Selezen и Korsars считаются ненадёжными по оригинальной логике JacRed; голоса, языки и ffprobe сохраняются. Сортировка Seeders DESC, затем Peers DESC. Это subset, не 100% оригинального MonoTorrent merge.

## Проверка с чистого клона на Mac/Linux

Из корня репозитория:

    for t in test_bunny_native_import.py test_bunny_native_audit.py test_bunny_native_parity.py test_bunny_native_capture.py test_bunny_native_split.py; do
      python3 -m unittest discover -s tests -p "$t" -v
    done
    cd bunny-native/edge
    npm install --no-audit --no-fund
    npm test
    npm run build
    node --check dist/index.js

Эти тесты создают синтетические GZip FileDB и локальные SQLite, не требуют доступа к Bunny и не меняют production.

## Создание локальных SQL шардов

Только со СТАБИЛЬНОЙ КОПИЕЙ реальной Data из оригинального JacRed, а не с активным процессом записи:

    python3 tools/bunny_native_import.py import \
      --data-root "/ABSOLUTE/PATH/JacRed/Data" \
      --output "$HOME/Downloads/jacred-stage.sqlite"

    python3 tools/bunny_native_split.py \
      --database "$HOME/Downloads/jacred-stage.sqlite" \
      --output-dir "$HOME/Downloads/jacred-sql-shards" \
      --shards 2

Получатся shard-0.sqlite, shard-1.sqlite. Папка назначения должна до запуска отсутствовать. Данные source не удаляются. Обратите внимание: файлы Data/tracks в SQL представлены только SHA256 инвентарём, сами бинарные файлы нужно сохранить отдельно до реальной миграции Bunny Storage.

Для каждого отдельного шарда:

    python3 tools/bunny_native_export.py \
      --database "$HOME/Downloads/jacred-sql-shards/shard-0.sqlite" \
      --output "$HOME/Downloads/jacred-shard-0-private.jsonl"

Пример для второго — shard-1.sqlite и другой выходной JSONL. Каждый JSONL приватный; он может содержать magnet и passkeys. Не загружать в GitHub, чат или публичные CI artifacts.

## Проверка удалённого Bunny — только отдельно подготовленные тестовые базы

Шардированная топология не развёртывается автоматически. На Bunny должна быть отдельная НОВАЯ ПУСТАЯ Database для каждого SQL шарда. Вначале проверьте квоты и количество строк. Выполняйте guarded JSONL loader согласно инструкции v0.4 с явно указанными параметрами --apply --ack-new-database только на тестовом экземпляре. Для каждого импортируемого файла нужны отдельные BUNNY_DATABASE_URL и BUNNY_DATABASE_AUTH_TOKEN соответствующего экземпляра.

Локальный dry-run без сети:

    node bunny-native/remote-import.mjs \
      --file "$HOME/Downloads/jacred-shard-0-private.jsonl" \
      --sha256 YOUR_EXPORTED_SHA256

Список переменных Edge Scripting при реальном отдельном staging (значения ниже лишь шаблон):

    JACRED_EDGE_PREVIEW_ENABLED=true
    JACRED_EDGE_API_KEY=<SEPARATE_RANDOM_LONG_KEY>
    JACRED_SQL_SHARD_IDS=0,1
    JACRED_SQL_SHARD_0_URL=libsql://<FIRST-TEST-DB>.lite.bunnydb.net
    JACRED_SQL_SHARD_0_TOKEN=<READ_ONLY_TOKEN_0>
    JACRED_SQL_SHARD_1_URL=libsql://<SECOND-TEST-DB>.lite.bunnydb.net
    JACRED_SQL_SHARD_1_TOKEN=<READ_ONLY_TOKEN_1>

Вместо значений заполняйте Bunny Dashboard Secrets. Не сохраняйте токены в GitHub. Для трёх баз IDS=0,1,2; для четырёх IDS=0,1,2,3. Все базы обязаны иметь манифест исходного masterDb с одинаковым SHA256, а индекс и shard_count должны совпадать с конфигурацией.

Если JACRED_SQL_SHARD_IDS не определён, старый однобазовый режим Bunny Database (BUNNY_DATABASE_URL и BUNNY_DATABASE_AUTH_TOKEN) сохраняется. Если sharding включён с ошибками, fallback к одиночной базе НЕ выполняется: API отвечает 503.

## Безопасность и границы готовности

- Не включать CDN/Lampa/Tizen/TorrServer-bunny, не переключать production DNS. Старый .NET JacRed остаётся source of truth и продолжает все 25 парсеров.
- Для доступа к БД нужны серверные URL/token. Никаких секретов в браузере.
- До 4 SQL-баз, суммарно максимум 200 candidates, нет полной query-distribution оптимизации; много-базовый fanout увеличивает read costs. Live latency и billing не измерены.
- Общая lineage защищает от смешивания разных исходных masterDB снимков, но не эквивалентна распределённой согласованной транзакции или защитой от внешних SQL-изменений после импорта.
- Только 40-hex BTIH. Magnet hash-v2/base32, точная сортировка и merge оригинальной .NET службы, Alloha IMDb/KP/TMDB resolver, 25 crawler jobs, cron/admin/sync/quality и реальные HTTP golden остаются для следующих релизов.
- Публичное превью Bunny Database может ограничивать вместимость одной БД; проверьте официальные актуальные лимиты до размещения полного каталога.

## Следующая версия

v0.5.3 — Golden API Parity & Live Multi-DB Acceptance: сравнение с реально запущенным .NET, проверка Bunny SQL, измерение стоимости и задержек, полноценная совместимость фильтров, отказоустойчивость и решение о production cutover. Следующая большая волна — v0.6.0 Tracker Automation.

Документация Bunny Database client: https://www.npmjs.com/package/@bunny.net/database-client
