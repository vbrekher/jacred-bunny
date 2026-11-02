# JacRed Bunny Native — план полной функциональной миграции на bunny.net

Дата решения: 2026-11-03. Статус: **ROADMAP / NOT IMPLEMENTED**. Целевой репозиторий: https://github.com/vbrekher/jacred-bunny. Исходный проект: https://github.com/jacred-fdb/jacred. Плеер: https://github.com/vbrekher/TorrServer-bunny.

## 0. Цель и техническая честность

Сохранить **функциональную совместимость JacRed**, все публичные и административные API, поиск, индекс, 25 парсеров, sync, tracks, web UI, статистику, обслуживание и использование в Lampa/Sonarr/Radarr/Prowlarr на инфраструктуре bunny.net, без пользовательского VPS.

**Отдельные физические ограничения платформы:**
- Нельзя исполнить существующий бинарник .NET 10 внутри Bunny Edge Scripting. Для Edge необходимо переписать API/поиск на TypeScript/JavaScript. Долгоживущий парсинг/ffprobe/торрент-peer networking выполняют процессы в Bunny Magic Containers. Это всё ещё **100% bunny.net hosting**, но не «всё только Edge Scripting».
- Bunny Database — SQLite/libSQL, а исходная FileDB — собственная схема JSON-секций, masterDb и FastDbIndex. Перенос возможен путём **разработки хранилища, импорта, индексов и адаптера**, а не одной сменой connection string.
- Bunny Database Public Preview: 1 GB / database и 50 databases / account по состоянию на 03.11.2026. Крупный каталог требует шардирования и мониторинга квот либо подтверждённого повышения лимитов Bunny Support.
- Database read replicas асинхронны: нельзя обещать мгновенную одинаковую выдачу на разных независимых соединениях без replication index / primary transactions / специальных гарантий.
- Публичные трекеры могут требовать авторизации, капчу, разрешённые прокси/браузерный sidecar, могут быть недоступны по сети или запрещать автоматизированный сбор. «25/25 поддерживаются» ≠ «25/25 всегда доступны». Никаких обходов запретов сайта/авторизации или фиктивного PASS.
- Автоматического backup/replication для MC Persistent Volumes нет. Будущая цель — не зависеть от их копий вообще; для аварийного восстановления экспортировать Bunny Database в Bunny Storage.
- Нельзя считать Bunny Database сильнее её документированной durability: возможна потеря последних WAL-изменений при редком failover (до 10 секунд/4096 frames). План предусмотреть повторяемые импорт/парсинг и внешний экспорт; без ложного «zero data loss».

Официальные источники: https://bunny.net/docs/database/limits ; https://bunny.net/docs/database/replication ; https://bunny.net/docs/database/durability-and-consistency ; https://bunny.net/docs/scripting/limits ; https://bunny.net/docs/magic-containers/persistent-volumes ; https://bunny.net/docs/database/connect/dotnet .

## 1. Целевая архитектура

\`\`\`text
Samsung Tizen / Lampa / Sonarr / Radarr / Prowlarr / Web
                         HTTPS
                           |
                Bunny CDN + Shield
                           |
           Bunny Edge Scripting (TypeScript)
     API gateway / auth / search / Torznab / Jackett
               |                        |
       Bunny Database (libSQL)    Bunny Storage
       - catalog + app config     - SQL exports
       - sharded torrent rows     - checkpoints
       - FTS/aliases              - restoration manifests
       - jobs + leases            - static web UI/assets
       - replication cursors      - snapshot verification
                ^                       ^
                | write batches         | backups
         Bunny Magic Containers: background workers
          - 25 tracker connectors + scheduling
          - FlareSolverr/cffetch as authorized sidecars
          - optional ffprobe/tracks analyzer
          - imports/sync/migrations/repair/backup
                           |
       Separate Bunny Magic Container: TorrServer-bunny
           torrent peer engine + playback streaming
\`\`\`

**Source of truth:** SQL data and durable job state in Bunny Database. Bunny Storage is backup/archive, not a mutable SQL DB. Ephemeral worker caches are disposable. Local FileDB becomes an *import-only legacy format*, not production authority. Several web/worker replicas share the same **logical catalog**, not the same physical FileDB directory. One distributed authoritative database service, bounded reads and version awareness; no multi-writer local disks.

**Separate concerns:** Web UI static files can live on Bunny Storage/CDN. Short request-bound logic lives in Edge Scripting. Long-running work, browser automation, ffprobe, torrent transports, and durable scheduled workers live in Bunny Magic Containers.

## 2. Current source inventory (from repo's main branch at planning)

- FileDB: \`Infrastructure/Persistence/FileDB/*\`; master index in \`Data/masterDb*.bz\`, data shards in \`Data/fdb/**\`, \`FastDbIndex\` in \`Application/Index/FastDbIndex.cs\`.
- Search: \`Application/Search/*\`; API controllers \`Controllers/JackettController.cs\`, \`Controllers/TorznabController.cs\`, \`Controllers/TorrentsController.cs\`, \`Controllers/SyncController.cs\`.
- Background: \`SyncWorker\`, \`TrackersWorker\`, \`TracksWorker\`, \`FileDbWorker\`, \`FastDbRefreshWorker\`, stats and resume workers under \`Infrastructure/Background/*\`.
- Parsed sources listed in \`Data/init.yaml\` (25): anibelka, anidub, anifilm, aniliberty, animelayer, anistar, baibako, bitru, kinozal, knaben, korsars, leproduction, lostfilm, mazepa, megapeer, nnmclub, rudub, rutor, rutracker, selezen, subsplease, toloka, torrentby, ultradox, viruseproject.
- Tracks metadata is not merely torrent title: \`Infrastructure/Tracks/*\`, magnet/infohash, ffprobe, languages, synchronization and exports must migrate too.
- Original system supports local parser credentials, Cloudflare sidecars, remote sync, maintenance, metadata, dashboards. Feature contract must be captured before replacing storage or routes.

## 3. Релизы и приёмка

### v0.3.0 — Compatibility Freeze / Source & Capacity Audit
- Generate endpoint inventory from controllers/OpenAPI: methods, paths, query params, XML/JSON content types, auth, pagination, errors, CORS.
- Generate golden corpus of original search results incl. multilingual names, quality, seasons, categories, voices, variants, magnet and empty results.
- Profile real source dataset: per-shard byte count, total size, index size, FTS footprint, records, search QPS; **do not assume** all data fit in one Bunny Database.
- Test database free capacity and requests, FTS syntax, query plans, transactions, TS edge client and .NET connector on small synthetic dataset.
- Gate: pinned upstream commit, documented 25 connectors, complete route matrix, SQL capacity plan with no unsupported claims.

### v0.4.0 — Database-Native Storage + FileDB Import
- Introduce interfaces \`ITorrentRepository\`, \`ISearchIndex\`, \`ITrackMetadataStore\`, \`ISyncCursorStore\`, \`IJobLeaseStore\` and adapters; never scatter raw SQL across parser implementations.
- SQL schema for records: \`torrents\` (stable compound source ID, source URL, magnet/infohash, size, seeds/peers, title/name, timestamps, categories, available media/quality/language metadata, original JSON when necessary); \`aliases\`, \`search_terms\`, \`track_metadata\`, \`sources\`, \`job_runs\`, \`leases\`, \`sync_cursors\`, \`tombstones\`, \`schema_migrations\`. Unique/idempotent upsert keys.
- SQLite FTS5 / BM25 or verified alternative respecting JacRed's search normalization and ranking. Reuse compatibility tests before declaring identical output.
- Dedicated catalog DB maps keys or deterministic partitioning to bounded shard DBs. Keep each database below operational watermark (e.g., 70–80% of 1GB); no silent auto-create beyond quotas; support controlled reshard.
- Versioned offline importer from \`masterDb*.bz\` + all \`Data/fdb/**\` + \`Data/tracks/**\`; resumable cursor, checksums, count/field parity, duplicates, corruption report, safe retry; rollback to legacy mode until verified.
- Initial phase = shadow/dual-read comparison with *one authoritative writer*. No double charging / double writes of the same event.
- Gate: every original record accounted for, 0 unexplained missing/changed values, golden result equivalence, restart and reimport idempotency, measured p50/p95 and cost.

### v0.5.0 — Edge-Native API / UI
- Reimplement route compatibility in TypeScript Edge Scripting backed by Bunny Database. Preserve Jackett JSON endpoints, Torznab XML/search/caps, Prowlarr indexer discovery, torrents/quality/trackers, sync where enabled, stats, health and admin/config API.
- Deploy original UI assets to Bunny Storage/CDN; route its API calls to Edge, adapt interface and auth where necessary.
- Auth: separate admin and client credentials, no anonymous admin or spoofable local-IP bypass, request validation, origin allowlist where credentials apply, CSRF protection, bounded query complexity, per-key rate limits; cache only public immutable resources.
- Return consistent API responses with documented versioning and golden differential testing against original .NET app.
- Gate: all in-scope routes 1:1 documented and passing; Edge requests stay inside 128MB, 30s CPU, 50 subrequest quotas with realistic loads; no .NET API process required for client-facing search.

### v0.6.0 — 25/25 Automatic Tracker Collector Fleet
- Preserve and refactor 25 adapters with explicit \`SourceCapability\` and \`SourceHealth\` records. Initially reuse original .NET worker in Bunny Magic Containers; optionally port parsers to TS later, but do not equate TS with Edge-only long-lived crawling.
- Distributed scheduler with bounded queues, per-tracker rate/concurrency, jitter, backoff, deadline, idempotent pages, durable checkpoints, dead-letter/poison job triage, shutdown recovery and source-specific permissions/cookies/proxy configuration.
- Sidecars (FlareSolverr/cffetch) deployed as separate MC containers **only where needed and authorized**; never invent a claim that captcha, login or third-party blocks have been bypassed.
- Test each connector with fixture unit tests plus real source smoke only when source permits; independent statuses \`PASS\`, \`AUTH_REQUIRED\`, \`BLOCKED\`, \`RATE_LIMITED\`, \`UNAVAILABLE\`, \`NOT_TESTED\`.
- Gate: all 25 adapters implemented, scheduler autonomous, no duplicate writes under concurrency, evidence per connector, no misleading universal-live claims.

### v0.7.0 — Distributed Consistency & Multi-Replica
- Multiple Edge instances use same Bunny Database catalog; no local FileDB to synchronize. For read-after-write across connections use replication index or primary transaction; cursor/version-aware consistency on sync/admin endpoints.
- Jobs/leases use single authoritative SQL primary, atomic conditional acquisition, expirations, owner fencing tokens, stale writer rejection and partition ownership. Do not rely on wall clock alone.
- Test 2+ simultaneous workers, pod restart while job active, primary failover and read lag, duplicate/reorder events, backfill replay, shard capacity alarms.
- Gate: same logical results across regions once required replication watermark has been reached; zero double-owned live jobs; no two independent disk-based sources of truth.

### v0.8.0 — Bunny Storage Backup, Restore & Disaster Recovery
- Automated, resumable, authenticated SQL export per shard + metadata + config schema + sync cursor into private Bunny Storage zone. Use transaction-consistent snapshots, cross-shard snapshot epoch or writer pause, checksums, compressed chunks and signed manifest.
- Daily or user-selected cadence, retention/lifecycle, encryption of credential-bearing backup content, backup job alerts. Never publish access keys, credentials or raw private backup objects via public CDN.
- Installer adds and validates Storage Zone via API, reuses owned zone if exact matching ID and policy, records resource IDs; reject ambiguity/duplicate purchase.
- Restore fresh catalog + shards into new env in isolated mode, verify checksums and record/search parity, then cut over endpoints. Periodic automated restore drill; recovery plan includes losing the latest WAL window documented by Bunny.
- Gate: independently restored instance passes search/track golden corpus; measured RPO/RTO, not assumed zero.

### v0.9.0 — TorrServer-bunny Integrated Playback
- Detect or install \`vbrekher/TorrServer-bunny\` by its actual API/CLI contract without touching unrelated apps. Use **Bunny Magic Containers** for the torrent peer/stream engine, never route raw sustained torrent traffic through Edge Script.
- Search results provide a \`Play\` action (magnet/.torrent URL → authorized TorrServer session). Proxy or redirect only signed short-lived session metadata; scope per user to avoid open torrent proxy or leaking the TorrServer password.
- Samsung Tizen/Lampa player and embedded browser test matrix: Range requests, seek, buffering, bitrate, subtitles, codecs, browser CORS/HTTPS, auth token refresh. No claim every codec is natively supported by TV.
- Gate: real permitted torrent playback and seek on Samsung Tizen and desktop; TorrServer continues to work independently.

### v1.0.0 — One Command Install / Full Acceptance
- Upgrade \`install-bunny.sh\` + \`bunny/cli.py\` to create/reuse databases, schema/migrations, per-db credentials, Storage Zone, Edge Script, static UI CDN and Magic Containers workers; optional TorrServer integration; supports trial quotas and reused resources.
- Preflight account limits **before billable POST**, published signed image digest, region/registry access, incremental cost estimate and explicit \`CREATE\`. Reconcile ambiguous HTTP 500, checkpoint resources one by one, rerun idempotently without duplicate apps/databases/zones.
- Commands: \`setup\`, \`status\`, \`doctor\`, \`update\`, \`backup\`, \`restore\`, \`rotate-keys\`, \`playback-test\`, \`stop\`, \`start\`, \`uninstall --dry-run\`. Full ID ownership manifest; safe cleanup must never delete shared unrelated resources.
- RU/EN/NL docs; setup wizard needs only Bunny account key and source credentials **if truly required**. Never promise hidden third-party cookies/logins available by magic.
- Final gates: API parity, 25 adapters audited, database cutover, worker failover, backups/restore, concurrent load, TS Edge quota, security/abuse controls, real Lampa/Samsung playback and charge visibility, all with evidence.
- Status \`PRODUCTION_READY=true\` only after LIVE acceptance; code quality PASS alone is insufficient.

## 4. Critical decision points

1. **Dataset capacity:** if full index exceeds 1 GB per database, choose hash/genre/source-based shards with bounded federated search. If resulting shard count or query cost exceeds 50 DB quota, obtain Bunny Support-approved capacity increase or reduce active index scope with explicit opt-in. Never silently truncate user data. Full fidelity cannot be claimed until capacity and economics pass.
2. **Edge-only boundary:** .NET binary cannot run in JavaScript edge isolate. The delivered **public functionality can be Edge-native**, while crawling, probes and torrent streaming run in separate Bunny Magic Containers.
3. **Multi-replica definition:** identical *logical SQL catalog* with version-aware reads, not byte-identical local FileDB volumes.
4. **All 25 sources:** code support/automatic attempts and transparent health reporting; no universal guarantee of access to unrelated third-party sites or private accounts.
5. **Playback:** UI feature integrates TorrServer; JacRed is not transformed into a BitTorrent streaming daemon inside an Edge script.
6. **Backward compatibility:** keep the original .NET + FileDB path as a temporary rollback in development; remove it as a requirement for production only after data parity and reliable backups.

## 5. Release artifacts and evidence

For each milestone: source diff, release notes, API parity report, migration dry-run and reconciliation, unit + integration test logs, SQL query performance/cost samples, runner logs, SHA/OCI digest, operational runbook, exact rollback/restore instructions, production-gate list. A release is not «done» on documentation alone.

## 6. Next implementation order

**Start v0.3.0:** freeze interface contracts, take real data and route inventory, benchmark Bunny Database/FTS capacity, and confirm the proposed storage model. Do not begin with an untested mass migration of 25 parsers.

---

References: [Original JacRed](https://github.com/jacred-fdb/jacred) · [TorrServer-bunny](https://github.com/vbrekher/TorrServer-bunny) · [Bunny Database limits](https://bunny.net/docs/database/limits) · [Bunny Edge Scripting limits](https://bunny.net/docs/scripting/limits) · [Bunny Persistent Volumes](https://bunny.net/docs/magic-containers/persistent-volumes) · [Bunny Database replication](https://bunny.net/docs/database/replication) · [Bunny DB durability](https://bunny.net/docs/database/durability-and-consistency).
