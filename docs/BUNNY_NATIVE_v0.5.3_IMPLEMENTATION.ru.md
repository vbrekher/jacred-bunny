# JacRed Bunny Native v0.5.3 — Golden API Parity & Live Multi-DB Acceptance

**Состояние: SOURCE VERIFIED / OFFLINE SQLITE VERIFIED / LIVE BUNNY DATABASE NOT RUN / PRODUCTION CUTOVER DISABLED.**

Это исходный код и набор операторских тестов. Отдельный Bunny API-ключ, БД или деньги не требуются для локальных регрессий. Никаких ресурсов на Bunny в этом релизе автоматически не создаётся.

## Реализовано

1. Исправлены предупреждения macOS ResourceWarning: unclosed file в двух Python-тестах экспорта. GitHub Actions теперь запускает splitter с флагом -W error::ResourceWarning.
2. Модуль bunny-native/edge/src/acceptance.mjs проверяет 2–4 SQL-шарда: исходный SHA256 masterDb, согласованность манифестов, полноту таблиц, FTS5 и агрегаты. Дополнительный полный аудит проверяет SHA256 записей JSON, MD5 каждого бакета и назначение шарда по int(md5,16) % shard_count. Ошибка или недоступность хотя бы одной базы означает отказ, без частичного успеха.
3. bunny-native/edge/live-acceptance.mjs поддерживает прежнюю одиночную БД и отдельные 2–4 Bunny Database по JACRED_SQL_SHARD_IDS. Команда безопасна по умолчанию: сети не будет до явного --run, полный скан требует --ack-full-scan.
4. Выводятся только счётчики, число прочитанных SQL запросов и миллисекунды выполнения. Это **измерения операторского клиента, не денежный биллинг Bunny**. Токены, URL сервера, magnet, passkey и торрент-записи не печатаются.
5. tools/bunny_native_capture.py может сохранять приватные HTTP-ответы с указанными оператором role, source master SHA256, хешем origin и запроса. tools/bunny_native_parity.py --require-evidence отвергает сравнение с самим собой, несовпадающий заявленный снимок, несовпадающие тестовые запросы и пустой поиск. PASS строго маркируется как OPERATOR_DECLARED_HTTP_GOLDEN_MATCH_NOT_LIVE_CERTIFICATION — это не независимое подтверждение живой системы.

## Локальная проверка на Mac

Из корня свежего git clone:

~~~bash
for test in \
  test_bunny_native_import.py \
  test_bunny_native_audit.py \
  test_bunny_native_parity.py \
  test_bunny_native_capture.py \
  test_bunny_native_evidence.py \
  test_bunny_native_capture_e2e.py
do
  python3 -m unittest discover -s tests -p "$test" -v
done

python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_bunny_native_split.py' -v

cd bunny-native/edge
npm install --no-audit --no-fund
npm test
npm run build
node --check dist/index.js
node live-acceptance.mjs --help
~~~

Тесты используют локальные синтетические GZip FileDB, SQLite и HTTP на 127.0.0.1. Реальная Bunny Database не вызывается и исходный JacRed не изменяется.

## Отдельная реальная Bunny Database — только тестовый аккаунт

Нужны две полностью импортированные БД из одного snapshot FileDB, согласно v0.5.2, с правами **только на чтение**. Никогда не используйте глобальный Bunny API key вместо узкого SQL-токена.

В каталоге bunny-native/edge (macOS zsh):

~~~bash
export JACRED_SQL_SHARD_IDS='0,1'
export JACRED_SQL_SHARD_0_URL='libsql://YOUR-TEST-DB-0.lite.bunnydb.net'
export JACRED_SQL_SHARD_1_URL='libsql://YOUR-TEST-DB-1.lite.bunnydb.net'

read -s 'JACRED_SQL_SHARD_0_TOKEN?First read-only token: '
export JACRED_SQL_SHARD_0_TOKEN
read -s 'JACRED_SQL_SHARD_1_TOKEN?Second read-only token: '
export JACRED_SQL_SHARD_1_TOKEN

node live-acceptance.mjs --run

# Полный SHA256 + MD5 bucket audit делает много SQL-чтений:
node live-acceptance.mjs --run \
  --hash-all --audit-routing --ack-full-scan

unset JACRED_SQL_SHARD_IDS \
  JACRED_SQL_SHARD_0_URL JACRED_SQL_SHARD_1_URL \
  JACRED_SQL_SHARD_0_TOKEN JACRED_SQL_SHARD_1_TOKEN
~~~

Поменяйте адреса на ваши настоящие тестовые Bunny DB URLs. Для zsh указан родной read -s; в Bash нужен read -rsp 'Prompt: ' VARIABLE. Команда принимает только явный --run, никакого выполнения в фоне нет. Ошибки приводят к безопасному отказу, а не к обещанию доступности. Результат MULTI_DB_READ_ONLY_STAGING_PASS_NOT_CUTOVER — подтверждение **только этой проверенной тестовой БД**.

## HTTP golden comparison: оригинальный .NET против Edge

Необходимо сначала самостоятельно запустить старый .NET и новый Edge Script в двух отдельных тестовых окружениях, загрузив в них **один неизменяемый каталог**. Эти тестовые окружения нами не развёрнуты.

Для source hash используйте master_sha256 из таблицы native_imports локального SQL stage, а не SHA256 сжатого masterDb.bz:

~~~bash
export JACRED_MASTER_SHA="$(
  python3 - "$HOME/Downloads/jacred-stage.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    row=db.execute("SELECT master_sha256 FROM native_imports WHERE id=1").fetchone()
    if not row or len(row[0]) != 64:
        raise SystemExit("Missing master checksum")
    print(row[0])
PY
)"
~~~

Из корня проекта (в разных полях BASE URLs поставьте свои тестовые домены):

~~~bash
read -s 'JACRED_LEGACY_API_KEY?Original JacRed test key: '
export JACRED_LEGACY_API_KEY
read -s 'JACRED_EDGE_API_KEY?Edge test key: '
export JACRED_EDGE_API_KEY

python3 tools/bunny_native_capture.py --run \
  --source-role original --master-sha256 "$JACRED_MASTER_SHA" \
  --base-url 'https://ORIGINAL-JACRED-TEST.example' \
  --cases bunny-native/edge/fixtures/http-cases.example.json \
  --token-env JACRED_LEGACY_API_KEY \
  --output "$HOME/Downloads/jacred-original-private.json"

python3 tools/bunny_native_capture.py --run \
  --source-role edge --master-sha256 "$JACRED_MASTER_SHA" \
  --base-url 'https://EDGE-JACRED-TEST.example' \
  --cases bunny-native/edge/fixtures/http-cases.example.json \
  --token-env JACRED_EDGE_API_KEY \
  --output "$HOME/Downloads/jacred-edge-private.json"

python3 tools/bunny_native_parity.py \
  --legacy "$HOME/Downloads/jacred-original-private.json" \
  --edge "$HOME/Downloads/jacred-edge-private.json" --require-evidence

unset JACRED_MASTER_SHA JACRED_LEGACY_API_KEY JACRED_EDGE_API_KEY
~~~

Замените тестовый запрос в шаблоне на гарантированно существующий в обеих базах фильм. Без хотя бы одного положительного ответа режим --require-evidence выдаст REFUSED. Обе системы должны действительно использовать одинаковый snapshot; SHA256 вводится оператором и не верифицирует живой сервер автоматически.

Файлы capture включают magnet/passkey и могут содержать частную информацию. Они сохраняются с правами 0600. **Не публикуйте файлы в GitHub, Issue, чате или общедоступных CI отчётах.**

## Acceptance gates

- Source CI + local SQLite + loopback HTTP: testable without external credentials.
- Bunny Database read-only multi-region latency, квоты, FTS5 и биллинг: NOT RUN.
- Настоящие .NET-vs-Edge golden запросы: NOT RUN.
- Полное совпадение сортировки, фильтров, MonoTorrent v2/base32, 25 парсеров, языков: NOT CERTIFIED.
- Production cutover, Bunny resources provisioning, DNS/CDN/Lampa/TorrServer: DISABLED.

**Следующая версия: v0.5.4 — One-command Bunny Staging Provisioning & Deployment Automation.** Код развертывания должен безопасно принимать Bunny API пользователя, создавать только тестовые ресурсы, проверять реальный SQL/Edge и предусматривать безопасную отмену без нарушения production.
