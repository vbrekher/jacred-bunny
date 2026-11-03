# JacRed Bunny Native v0.4.0 — SQL Storage Engine & FileDB Import

Статус: **SOURCE IMPLEMENTED / OFFLINE TESTED / BUNNY LIVE NOT TESTED / PRODUCTION CUTOVER DISABLED**.

В этом релизе исходный JacRed (.NET, cron, FileDB, 25 парсеров и текущий Bunny Magic Container) не заменяется, его производственные данные не изменяются. Новый механизм работает в отдельном каталоге как подготовка к полноценной Bunny Database.

## Что разработано

1. Схема SQL в bunny-native/migrations/0001_catalog.sql: buckets (метаданные masterDb), bucket_aliases (части оригинального ключа FastDbIndex), torrents (индексируемые столбцы + ПОЛНОЕ исходное JSON-содержимое + SHA256), track_file_inventory (имя, размер и SHA256 файла).
2. Индекс поиска FTS5 + триггеры в 0002_search_fts5.sql. Ключи оригинального FastDbIndex сохраняются, но **эквивалентность ранжирования и Unicode-нормализации пока не доказана**.
3. tools/bunny_native_import.py: чтение GZip masterDb*.bz и файлов hashed FileDB (две схемы путей), обнаружение пропавших/лишних шардов, ограничение распаковки, проверка изменений исходных файлов, перенос на отдельный SQLite stage и проверка каждого бакета. Атомарная публикация готового файла через os.replace. Отсутствие действительной базы всегда приводит к отказу, а не к успешной пустой миграции.
4. tools/bunny_native_export.py: экспорт только ранее проверенного SQLite stage в приватный JSONL-файл с правами 0600 и SHA256. Весь JSON и большие .NET fileTime сохраняются без потери точности (fileTime передаётся десятичной строкой).
5. bunny-native/remote-import.mjs: предварительная проверка SHA256/JSONL без сети. При явно заданных --apply --ack-new-database использует официальный @bunny.net/database-client@0.0.3 для отдельной **НОВОЙ ПУСТОЙ** базы Bunny. SQL-записи выполняются транзакционными пакетами до 50 операций; проверяется число записей. SDK берёт адрес и токен из BUNNY_DATABASE_URL / BUNNY_DATABASE_AUTH_TOKEN. Никаких автоматических платных ресурсов или переключения клиента JacRed не создаётся.

## Установка и локальный импорт

Нужен Python 3.9+ и оригинальная стабильная копия Data с masterDb.bz, fdb/ и, если имеются, tracks/.

~~~bash
python3 tools/bunny_native_import.py plan \
  --data-root /ABSOLUTE/PATH/TO/ORIGINAL/JACRED/Data

python3 tools/bunny_native_import.py import \
  --data-root /ABSOLUTE/PATH/TO/ORIGINAL/JACRED/Data \
  --output "$HOME/Downloads/jacred-native-staging.sqlite"

python3 tools/bunny_native_import.py verify \
  --database "$HOME/Downloads/jacred-native-staging.sqlite"

python3 tools/bunny_native_import.py search \
  --database "$HOME/Downloads/jacred-native-staging.sqlite" \
  --query "Film"
~~~

Параметр --replace разрешает атомарно заменить существующий OUTPUT **только по явному требованию**, после всех проверок. Параметр --max-json-mb увеличивает лимит распакованного master/shard сверх 128 MiB с предупреждением о потреблении RAM. Не запускайте импортер против активно меняющейся FileDB: делайте стабильный снимок источника. Контроль статуса/mtime не является полноценной гарантией snapshot isolation во время одновременной записи.

В GitHub хранится только Data/fdb/.gitignore, а не миллионы торрент-записей. Поэтому команда plan на обычном git clone **должна отказать**. Источник нельзя удалять до проверки импорта.

## Закрытый JSONL для Bunny Database

~~~bash
python3 tools/bunny_native_export.py \
  --database "$HOME/Downloads/jacred-native-staging.sqlite" \
  --output "$HOME/Downloads/jacred-native-private.jsonl"
~~~

Экспорт выводит SHA256 (64 символа). Скопируйте значение вместо YOUR_EXPORT_SHA ниже.

~~~bash
cd bunny-native
npm install --omit=dev --ignore-scripts

node remote-import.mjs \
  --file "$HOME/Downloads/jacred-native-private.jsonl" \
  --sha256 YOUR_EXPORT_SHA
~~~

Ожидаемый результат проверки без сети: BUNNY_REMOTE_DRY_RUN_NO_NETWORK.

Для действительно новой, специально созданной пустой Bunny Database, **только после проверки её вместимости и возможных расходов**:

~~~bash
export BUNNY_DATABASE_URL='libsql://YOUR-EMPTY-BUNNY-DATABASE'
read -s 'BUNNY_DATABASE_AUTH_TOKEN?Enter Bunny DB token: '
export BUNNY_DATABASE_AUTH_TOKEN

node remote-import.mjs \
  --file "$HOME/Downloads/jacred-native-private.jsonl" \
  --sha256 YOUR_EXPORT_SHA \
  --apply --ack-new-database

unset BUNNY_DATABASE_AUTH_TOKEN
~~~

Команда read -s выше предназначена для zsh на macOS. В Bash используйте read -rsp 'Enter Bunny DB token: ' BUNNY_DATABASE_AUTH_TOKEN. Не размещайте Bunny API ключи или токены в командной строке, GitHub Issues, открытых логах или пользовательском браузере.

**Важно:** JSONL может включать приватные magnet, passkeys, URL и метаданные — храните его конфиденциально и не публикуйте. Хранить необходимо не только SQL: сами Data/tracks бинарные объекты в v0.4.0 НЕ импортируются; перенесён только их инвентарь с SHA256. Не допускайте production cutover до загрузки фактических объектов в Bunny Storage и сверки.

## Пределы реализации и безопасный отказ

- Bunny Database Public Preview: 1GB на одну базу, 50 баз на аккаунт по умолчанию. Это прототип одной базы: полноценный шардированный кластер и автоматическая маршрутизация запросов запланированы дальше. 900MB JSONL — грубый клиентский стоп-лимит, а не гарантия, что SQL + FTS поместятся в 1GB.
- Требуется отдельная проверка поддержки FTS5 в Bunny libSQL: SQLite-совместимость не гарантирует идентичные версии или расширения.
- Если сеть/БД отказывает после создания схемы и нескольких пакетов, возможно частично заполненное STAGING-хранилище. Загрузчик запрещает обновлять непустые базы, чтобы не повредить существующие таблицы: сделайте новую пустую базу и повторите. Никакого тихого удаления данных и повторного создания платных ресурсов.
- Доказаны только локальные транзакции/целостность SQLite, не сетевые транзакции Bunny на пользовательской БД, не actual remote p95 и не окончательное совпадение результатов JacRed.
- Пакеты Bunny remote сверяются по агрегированным количествам. Полная удалённая проверка хешей каждого ряда, cross-region consistency, гарантированный recovery, production cutover — отдельные acceptance gates.
- Все 25 парсеров продолжат использовать старый .NET FileDB до отдельного переключения. Edge API, распределённый Scheduler, Bunny Storage backups и интеграция TorrServer-bunny — будущие релизы.

## Контрольные тесты

~~~bash
python3 -m unittest discover -s tests -p 'test_bunny_native_import.py' -v
python3 -m unittest discover -s tests -p 'test_bunny_native_audit.py' -v
python3 -m py_compile tools/bunny_native_import.py tools/bunny_native_export.py
node --check bunny-native/remote-import.mjs
~~~

Ожидается 17 v0.4 тестов и 12 v0.3 тестов. GitHub Actions: Bunny Native v0.4 Storage Import.

Следующий релиз — v0.5.0 Edge Scripting API + Compatibility Layer. Для полной миграции перед ним необходимы настоящий snapshot FileDB, оценка размера SQL/FTS, шардирование, полное сравнение ответов старого JacRed и нового SQL-поиска, Bunny Storage track payloads и подтверждение удалённого Bunny подключения.

Источники: https://bunny.net/docs/database/limits ; https://bunny.net/blog/query-bunny-database-with-fetch-and-nothing-else/ ; https://www.npmjs.com/package/@bunny.net/database-client
