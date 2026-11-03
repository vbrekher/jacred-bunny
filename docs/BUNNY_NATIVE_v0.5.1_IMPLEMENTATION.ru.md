# JacRed Bunny Native v0.5.1 — Compatibility Parity & Database Acceptance

Статус: SOURCE IMPLEMENTED / LOCAL SQLITE PARITY PASS / REAL BUNNY DB NOT RUN / ORIGINAL .NET GOLDEN NOT RUN / PRODUCTION CUTOVER DISABLED.

Репозиторий: https://github.com/vbrekher/jacred-bunny

## Реализовано

1. Добавлен parity.mjs: оригинальная нормализация StringConvert.SearchName (ё→е, щ→ш), выбор movie/serial/anime/docu, годы фильмов ±1, сезоны/эпизоды S02E03 и удаление дубликатов по source URL.
2. SQL поиск теперь использует мастер-алиасы таблицы bucket_aliases: сначала точный нормализованный ключ, затем поиск по фрагменту. Это ближе к оригинальному FastDbIndex, чем один FTS5. Если число кандидатов выше 200, сервер отказывает вместо молчаливой усечённой выдачи.
3. Когда TorrentDetails JSON не содержит url, он восстанавливается из ключа исходного FileDB словаря (SQL source_url).
4. Сквозные тесты создают синтетическую оригинальную GZip FileDB, импортируют её штатным v0.4 мигратором в НАСТОЯЩУЮ локальную SQLite и выполняют реальные SQL-запросы через Edge API. Это сильнее, чем подстановка mock SQL, но не эквивалентно тесту на Bunny.
5. live-acceptance.mjs проверяет реальную Bunny Database только после явного --run: манифест, счётчики, FTS5 и, по желанию, SHA256 каждой строки. Код только читает и не выводит токены, URL либо данные торрентов.
6. tools/bunny_native_capture.py и tools/bunny_native_parity.py предназначены для приватного получения настоящих HTTP golden-ответов оригинального .NET и Edge, затем строгого сравнения JSON, XML и статусов. Они не подменяют реальные ответы синтетическими.

## Проверка исходного кода на Mac/Linux

Из корня клона:

~~~bash
python3 -m unittest discover -s tests -p 'test_bunny_native_import.py' -v
python3 -m unittest discover -s tests -p 'test_bunny_native_audit.py' -v
python3 -m unittest discover -s tests -p 'test_bunny_native_parity.py' -v
python3 -m unittest discover -s tests -p 'test_bunny_native_capture.py' -v

cd bunny-native/edge
npm install --no-audit --no-fund
npm test
npm run build
node --check dist/index.js
~~~

Эти команды не подключаются к Bunny, не меняют исходную FileDB и не требуют секретов.

## Реальный Bunny Database acceptance (только отдельная тестовая база)

Создайте и импортируйте отдельный тестовый Bunny SQL каталог по инструкции v0.4.0. Сначала убедитесь, что миграция полностью завершилась и в таблице native_imports появилась запись. Используйте read-only token для тестовой БД, не общий API key аккаунта.

В bunny-native/edge:

~~~bash
node live-acceptance.mjs --help

export BUNNY_DATABASE_URL='libsql://YOUR_TEST_DATABASE'
read -s 'BUNNY_DATABASE_AUTH_TOKEN?Test DB read-only token: '
export BUNNY_DATABASE_AUTH_TOKEN

node live-acceptance.mjs --run

# Проверить хеши всех записей (дополнительные SQL-чтения и расходы):
node live-acceptance.mjs --run --hash-all --ack-full-scan

unset BUNNY_DATABASE_AUTH_TOKEN BUNNY_DATABASE_URL
~~~

Команда read -s с шаблоном VAR?Prompt рассчитана на macOS zsh; для bash используйте read -rsp 'Prompt: ' VAR. Нет переменных подключения — CLI откажет и не будет заявлять PASS. Результат READ_ONLY_REMOTE_STAGING_CHECK_PASS_NOT_PRODUCTION_CUTOVER подтверждает только проверенную удалённую базу, НЕ готовность JacRed к переключению.

## Сравнение с оригинальным .NET (два сервиса, одинаковый снимок каталога)

Нужны две работающие тестовые инсталляции: исходный .NET JacRed и отдельный Edge Script, ОБЕ на одном замороженном snapshot. До их появления настоящий HTTP golden PASS невозможен.

Из корня клона (подставьте свои тестовые домены, не production):

~~~bash
# Ознакомьтесь и при необходимости отредактируйте:
# bunny-native/edge/fixtures/http-cases.example.json

read -s 'JACRED_LEGACY_API_KEY?Original JacRed test key: '
export JACRED_LEGACY_API_KEY
read -s 'JACRED_EDGE_API_KEY?Edge test key: '
export JACRED_EDGE_API_KEY

python3 tools/bunny_native_capture.py --run \
  --base-url 'https://ORIGINAL-JACRED-TEST.example' \
  --cases bunny-native/edge/fixtures/http-cases.example.json \
  --token-env JACRED_LEGACY_API_KEY \
  --output "$HOME/Downloads/jacred-original-private.json"

python3 tools/bunny_native_capture.py --run \
  --base-url 'https://EDGE-JACRED-TEST.example' \
  --cases bunny-native/edge/fixtures/http-cases.example.json \
  --token-env JACRED_EDGE_API_KEY \
  --output "$HOME/Downloads/jacred-edge-private.json"

python3 tools/bunny_native_parity.py \
  --legacy "$HOME/Downloads/jacred-original-private.json" \
  --edge "$HOME/Downloads/jacred-edge-private.json"

unset JACRED_LEGACY_API_KEY JACRED_EDGE_API_KEY
~~~

Шаблон использует нейтральный тестовый запрос Test. Замените на известные фильмы и контрольные случаи из ваших данных. Внешние домены только HTTPS; локальный оригинал допускается через http://127.0.0.1. Скрипт сбора данных без --run не делает запросов, редиректы отключены. Файлы capture приватные (0600), включают полные magnet и возможно passkeys: НИКОГДА не отправляйте их в GitHub, чат, Issue или публичный CI. Comparator сообщает только число несовпадений, без значений ответов.

Проверка строгая: разные статусы, порядок элементов, названия и метаданные, ссылки и XML дадут FAIL. Это правильный результат до исправления оставшихся несовместимостей.

## Что пока НЕ ГОТОВО

- Full matching/ranking/dedup upstream JacRed, полный поиск по IMDb/KP/TMDB, все категории и продвинутые фильтры.
- Реальный live Bunny SQL + Edge deployment acceptance, измерение стоимости и SQL размеров, cross-region consistency.
- Масштабирование сверх 200 кандидатов, распределённое шардирование, перенос binaries Data/tracks в Bunny Storage и backup/restore.
- 25 живых парсеров, sync/admin/qualitys, реальная интеграция с TorrServer-bunny и Samsung Tizen.
- Original .NET golden HTTP сравнение не выполнялось на production данных.

Оригинальный JacRed остаётся работающим источником данных. Не переключайте Lampa, домены, CDN и production SQL до полноценного live acceptance.

## Следующий этап

v0.5.2 — Full Search Parity Expansion & Sharded SQL Query Foundation, затем v0.6.0 Tracker Automation.

Документация Bunny: https://bunny.net/blog/query-bunny-database-with-fetch-and-nothing-else/
