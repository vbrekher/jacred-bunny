# JacRed Bunny — installation / установка / installatie

## Автоматическая установка (RU)
На Mac/Linux нужны только `curl` и Python 3.9+. Никакого Docker на компьютере пользователя.

```bash
curl -fsSL --retry 3   https://raw.githubusercontent.com/vbrekher/jacred-bunny/main/install-bunny.sh   -o /tmp/jacred-bunny-install.sh   && sh /tmp/jacred-bunny-install.sh
```

Скрипт скрыто спрашивает Bunny Account API Key (настройки аккаунта Bunny → API). Он проверяет существующие приложения, доступный GHCR image/digest и регионы, затем предлагает выбрать регион и ввод `CREATE` перед созданием оплачиваемых ресурсов. Для GHCR, уже подключённого в Bunny, других API-ключей не требуется. Если GHCR ещё не подключён, скрипт запросит GitHub username и PAT с `read:packages` и попробует создать подключение через Bunny API. **Образ JacRed Bunny должен быть предварительно опубликован в GHCR** — публикация автоматизирована в workflow `bunny-image.yml`. До публикации установщик остановится без создания платного приложения. Приватный GHCR образ должен быть доступен подключённому registry.

Создаётся ровно один Magic Container в одном регионе, HTTPS endpoint и постоянные тома `/app/Data` (10 GB по умолчанию) и `/app/config` (1 GB). Настройки и два ключа JacRed создаются автоматически. Дополнительная официальная синхронизация FileDB — по желанию. Bunny Database/Scripting не создаются.

Команды после установки (работают даже если `~/.local/bin` не добавлен в PATH):

```bash
$HOME/.local/bin/jacred-bunny status
$HOME/.local/bin/jacred-bunny credentials
$HOME/.local/bin/jacred-bunny stop
$HOME/.local/bin/jacred-bunny start
$HOME/.local/bin/jacred-bunny doctor
```

Ключ Bunny сохраняется локально в `~/.config/jacred-bunny/config.json`, права 600. **Никогда не публикуйте этот файл.** Изменения в веб-конфигурации сохраняются в отдельном томе. Остановка `stop` останавливает вычисления, но не удаляет оплачиваемые тома. Контейнер может запускаться/создаваться асинхронно; до появления HTTPS URL и ответа `/health` запуск не считается подтверждённым.

## Automatic installation (EN)
On macOS/Linux, run the single curl command above. The installer asks for a Bunny account API key, checks the GHCR image and numeric registry ID, offers a region, creates one single-replica Magic Container, HTTPS endpoint, and two persistent volumes automatically after you type `CREATE`. JacRed API and admin keys are generated locally. If no GHCR connection exists, a GitHub `read:packages` PAT may also be required. The built image must already exist in GHCR; the dedicated GitHub Actions workflow builds it. The default FileDB is empty; optional official sync can populate it. Run `~/.local/bin/jacred-bunny status` or `credentials`. Stopping the app does not delete billed storage.

## Automatische installatie (NL)
Voer op macOS/Linux de curl-opdracht hierboven uit. Het programma vraagt om de Bunny-account-API-sleutel, controleert de GHCR-image en de numerieke register-ID, laat een regio kiezen en maakt na `CREATE` automatisch één Magic Container, HTTPS-endpoint en twee permanente volumes aan. De API- en beheerderssleutel van JacRed worden automatisch gegenereerd. Als er nog geen GHCR-verbinding is, kan ook een GitHub PAT met `read:packages` nodig zijn. De Docker-image moet al via GitHub Actions in GHCR gepubliceerd zijn. De FileDB is aanvankelijk leeg; officiële synchronisatie is optioneel. Status: `~/.local/bin/jacred-bunny status`. Stoppen verwijdert de betaalde opslag niet.

---


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
