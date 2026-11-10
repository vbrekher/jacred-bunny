# JacRed Bunny — installation / установка / installatie

## Русский

Стандартная команда устанавливает SQL-кандидат: Bunny Database, Edge gateway оригинального API, Storage и один Magic Container в AMS. Нужны macOS/Linux, curl, Python 3.9+ и Bunny Account API Key; локальные Docker/.NET и GitHub PAT не нужны. Ключ аккаунта остаётся локально. Установщик проверяет публичный пакет и точные хеши до запроса ключа; если SQL-пакет ещё не опубликован, ресурсы не создаются.

```sh
curl -fsSL --retry 3 https://raw.githubusercontent.com/vbrekher/jacred-bunny/main/install-bunny.sh -o /tmp/jacred-bunny-install.sh
sh /tmp/jacred-bunny-install.sh install --plan
# Создание ресурсов в пределах проверенной оценки:
sh /tmp/jacred-bunny-install.sh install --hours 1 --max-estimated-usd 2
```

По умолчанию окно вычислений — один час, оценка — до $2; это не жёсткий лимит счёта. Трафик оплачивается отдельно, Storage сохраняет минимальную плату после остановки. Локальный watchdog требует работающего компьютера и сети. Повторный `install` использует сохранённый пакет и проверяет существующие ресурсы без продления окна или запуска остановленного worker. Существующая FileDB требует отдельной проверенной миграции; автоматической подмены данных нет. Полная production acceptance, перенос всех конфигураций и все 18 режимов CLI ещё не завершены.

После SQL-установки: `jacred-bunny status`, `credentials`, `doctor`, `health`, `stop`, `start`. `credentials` выводит только ключи поиска/администрирования JacRed, не ключ Bunny и не SQL-токены. Расширенные операции пока доступны через `native-staging`; см. [руководство](NATIVE_STAGING.md). `setup` — отдельный устаревший FileDB-профиль.

## English

The standard command above installs a development SQL candidate: Bunny Database, an Edge gateway for the original API, Storage and one Magic Container in AMS. macOS/Linux, curl, Python 3.9+ and a Bunny account key are required; local Docker/.NET and a GitHub PAT are unnecessary. The account key stays local. Public artifacts and exact checksums are verified before requesting credentials; an absent SQL release stops before resource creation.

Use `install --plan` first. `install --hours 1 --max-estimated-usd 2` creates resources within the checked estimate. The default one-hour compute window and $2 estimate are not a hard billing cap; traffic is separate and Storage retains its minimum charge after stopping. The local watchdog needs the computer and network to remain available. Repeated installation checks the saved immutable package/resources without extending the window or starting a stopped worker. Existing FileDB requires separately verified migration. Full production acceptance, complete configuration migration and all 18 CLI modes remain incomplete.

SQL commands: `status`, `credentials`, `doctor`, `health`, `stop`, `start`. Credentials shows only JacRed search/admin keys, never the Bunny account key or SQL tokens. Advanced operations currently use `native-staging`; see the [guide](NATIVE_STAGING.md). `setup` explicitly selects the old FileDB profile.

## Nederlands

De standaardopdracht hierboven installeert een SQL-ontwikkelkandidaat: Bunny Database, een Edge-gateway voor de oorspronkelijke API, Storage en één Magic Container in AMS. Vereist: macOS/Linux, curl, Python 3.9+ en een Bunny-accountsleutel; lokale Docker/.NET en een GitHub PAT zijn niet nodig. De accountsleutel blijft lokaal. Publieke bestanden en exacte controlesommen worden vóór het opvragen van de sleutel gecontroleerd. Zonder gepubliceerde SQL-release worden geen resources aangemaakt.

Begin met `install --plan`. `install --hours 1 --max-estimated-usd 2` maakt resources binnen de gecontroleerde schatting. Het standaardvenster van één uur en de schatting van $2 vormen geen harde factuurlimiet. Verkeer wordt apart berekend; Storage houdt na stoppen zijn minimumtarief. De lokale watchdog vereist een beschikbare computer en netwerkverbinding. Herhaald installeren controleert het opgeslagen onveranderlijke pakket en de bestaande resources zonder het venster te verlengen of een gestopte worker te starten. Een bestaande FileDB vereist afzonderlijk gecontroleerde migratie. Volledige productieacceptatie, configuratiemigratie en alle 18 CLI-modi zijn nog niet voltooid.

SQL-opdrachten: `status`, `credentials`, `doctor`, `health`, `stop`, `start`. Credentials toont alleen de zoek- en beheersleutels van JacRed, nooit de Bunny-accountsleutel of SQL-tokens. Geavanceerde handelingen gebruiken voorlopig `native-staging`; zie de [handleiding](NATIVE_STAGING.md). `setup` kiest uitdrukkelijk het oude FileDB-profiel.

---

The following manual FileDB deployment notes are historical and apply only to the explicit legacy profile.



**[Русский](#русский)** · **[English](#english)** · **[Nederlands](#nederlands)**

## Русский

### Сначала о главном

Это оригинальный JacRed (.NET 10 + FileDB), адаптированный для Bunny Magic Containers. Он не превращён в Bunny Edge Script и **не использует Bunny Database вместо FileDB**. Единственная серверная база раздач на этом этапе — файловый индекс на постоянном томе.

### Установка

1. Установите Docker с Buildx и авторизуйтесь в GitHub Container Registry (`docker login ghcr.io` с правом записи образов). Клонируйте репозиторий.
2. Соберите **исходники этого форка**, а не сторонний готовый образ:

   ```bash
   IMAGE=ghcr.io/YOUR_ACCOUNT/jacred-bunny:0.1.0 bash bunny/build-push.sh
   ```

3. В панели Bunny → **Magic Containers** создайте приложение: **один регион**, **одна реплика**, образ из предыдущего шага, HTTP **порт 9117**, HTTPS-публикация, health-check `/health`. Если GHCR-образ приватный, настройте доступ к реестру.
4. Создайте **два отдельных Persistent Volumes**: смонтируйте на `/app/Data` (FileDB, логи, индекс) и `/app/config` (конфигурация). Для оценки начните с 5–20 GB для FileDB и минимально допустимого размера для конфигурации; далее измерьте реальное использование. Не путайте Persistent Volume с эфемерным диском контейнера.
5. Добавьте в переменные/секреты Magic Container **два разных** ключа: `JACRED_BUNNY_API_KEY` и `JACRED_BUNNY_DEV_KEY`. Каждый 32–128 символов: латинские буквы, цифры, `-`, `_`. Сгенерировать два значения можно локально, запуская эту команду дважды:

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

6. Оставьте `JACRED_BUNNY_CRON=0` для первоначального запуска. Значение `1` включает **только** служебный checkpoint каждые 5 минут, resume каждые 15 минут и еженедельную проверку. Парсинг трекеров намеренно не запускается без явной настройки.
7. Проверьте API: `curl -fsS https://YOUR-ENDPOINT/health`; затем подключите Lampa.

### Lampa / Sonarr / Radarr

- **Lampa Jackett URL:** `https://YOUR-ENDPOINT/api/v2.0/indexers/all/results`
- **Lampa API Key:** значение `JACRED_BUNNY_API_KEY`. Никогда не передавайте `JACRED_BUNNY_DEV_KEY` клиентам.
- **Sonarr/Radarr Generic Torznab URL:** `https://YOUR-ENDPOINT/torznab/api`, ключ тот же API Key.
- **Prowlarr Generic Torznab:** `https://YOUR-ENDPOINT/api/v1/indexer/1/newznab`.

Проверка поиска:

```bash
curl -fsSG 'https://YOUR-ENDPOINT/api/v2.0/indexers/all/results' \
  -H 'X-Api-Key: YOUR_API_KEY' \
  --data-urlencode 'query=ubuntu'
```

Нулевые результаты после первого запуска **ожидаемы**, потому что FileDB изначально пустая. Настройте легальный источник синхронизации или разрешённый парсинг через JacRed. FlareSolverr/cffetch по умолчанию выключены: для сложных трекеров нужны дополнительные сервисы и проверенная сетевая схема.

### Сохранность, безопасность, ограничения

- Конфиг в Bunny-версии читается и сохраняется через `JACRED_CONFIG_DIR=/app/config`. Это исправляет ситуацию, когда настройки, сохранённые из веб-интерфейса, терялись при пересоздании контейнера.
- Не публикуйте admin API без дополнительной защиты: ограничьте Bunny Shield/edge rules для `/dev/*`, `/cron/*`, `/jsondb/*`, `/api/v1.0/config*`. Проверяйте, что промежуточные прокси **сохраняют forwarding-заголовки**. При их удалении приложение может увидеть локальный адрес proxy вместо настоящего клиента и ошибочно дать LAN-доступ.
- Не кэшируйте ответы с API-ключами/динамические результаты на CDN, если ключ кэша не учитывает авторизацию. Для первой версии отключите CDN-кэш этих путей.
- **Не запускайте две пишущие реплики**: у каждого pod свой persistent volume. Много регионов для одного FileDB — не общая база. Масштабирование требует отдельной репликации и модели single writer.
- Пока нет автоматического и проверенного backup/restore. Делайте согласованные резервные копии `/app/Data` **и** `/app/config` в период остановки записи, сохраняйте их в Bunny Storage и проверяйте восстановление на тестовом инстансе.
- JacRed — поисковый агрегатор, а не видеоплеер и не торрент-клиент. Для воспроизведения нужен отдельный совместимый сервис. Соблюдайте правообладательские права и правила источников.

## English

### Deploy

1. Install Docker Buildx, sign in to GHCR with package write access, clone this fork and build **this fork's source**:

   ```bash
   IMAGE=ghcr.io/YOUR_ACCOUNT/jacred-bunny:0.1.0 bash bunny/build-push.sh
   ```

2. In Bunny Magic Containers create **one region, one replica**, choose the image, expose HTTP **9117**, configure HTTPS and a `/health` probe.
3. Attach **two persistent volumes**, mounted at `/app/Data` and `/app/config`.
4. Set **different** 32–128 character secrets `JACRED_BUNNY_API_KEY` and `JACRED_BUNNY_DEV_KEY`. Allowed characters: letters, digits, `-`, `_`.
5. Leave `JACRED_BUNNY_CRON=0` for initial deployment. `1` enables only maintenance jobs, not tracker crawling.
6. Lampa Jackett URL: `https://YOUR-ENDPOINT/api/v2.0/indexers/all/results`; Lampa API Key: your API secret. Sonarr/Radarr Torznab URL: `https://YOUR-ENDPOINT/torznab/api`.

This profile uses **JacRed FileDB**, not Bunny Database. A new installation starts with an **empty** index. The web editor now stores configuration under the persistent `/app/config` path. Protect admin endpoints, preserve reverse-proxy forwarding headers, bypass shared CDN caching for authenticated responses, and don't use multiple independent writable volumes as a shared DB. External trackers, proxies, optional FlareSolverr/cffetch and playback require additional configuration. Live production and recovery have **not** been certified.

## Nederlands

### Installatie

1. Installeer Docker Buildx, log in op GHCR met schrijfrechten en bouw de broncode van **deze fork**:

   ```bash
   IMAGE=ghcr.io/JOUW_ACCOUNT/jacred-bunny:0.1.0 bash bunny/build-push.sh
   ```

2. Maak in Bunny Magic Containers een app in **één regio met één replica**, HTTP-poort **9117**, HTTPS en een health-check op `/health`.
3. Koppel **twee permanente volumes** aan `/app/Data` en `/app/config`.
4. Stel **verschillende** geheime sleutels `JACRED_BUNNY_API_KEY` en `JACRED_BUNNY_DEV_KEY` in (32–128 tekens: letters, cijfers, `-`, `_`).
5. Laat `JACRED_BUNNY_CRON=0` staan. `1` start alleen onderhoudstaken, niet automatisch alle trackers.
6. Lampa Jackett-URL: `https://JOUW-ENDPOINT/api/v2.0/indexers/all/results`, met de ingestelde API-sleutel. Voor Sonarr/Radarr: `https://JOUW-ENDPOINT/torznab/api`.

De oorspronkelijke **FileDB** blijft de zoekdatabase. Bunny Database is geen directe vervanging. Bij de eerste start is de index leeg. Wijzigingen via de web-editor worden nu op het permanente configuratievolume opgeslagen. Beveilig beheer-API's, bewaar forwarding-headers van de proxy en cache geen beveiligde dynamische antwoorden gezamenlijk. Meerdere schrijvende replica's delen geen bestandssysteem. Externe trackers, proxies, FlareSolverr/cffetch en videoweergave vereisen extra instellingen. Een live productiecontrole en hersteloefening zijn **nog niet uitgevoerd**.

---

**Credits:** Thank you to the developers and contributors of [original JacRed](https://github.com/jacred-fdb/jacred). This is an independent Bunny-oriented derivative. Original license and attribution: [AGPL-3.0](../LICENSE).
