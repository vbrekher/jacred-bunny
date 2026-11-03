# Native staging control plane

This is an advanced infrastructure preview inside the existing JacRed Bunny CLI. It is not a second installer and does not complete the v0.5.4 gate. The default `setup` command remains the original FileDB deployment path.

## Plan

```sh
python3 bunny/cli.py native-staging plan
```

The plan creates nothing. The displayed $1.22 initial estimate combines the published Storage monthly minimum and first Edge billing increment. Database public preview is free as checked on 3 November 2026. It is not an account bill prediction or hard cost ceiling; traffic is metered separately.

## Provision isolated infrastructure

Use a published immutable source-SHA tag of this fork and a numeric public GHCR registry ID verified in your own Bunny account. The API checks the image digest before creating resources. Do not copy another account's registry configuration blindly.

```sh
python3 bunny/cli.py native-staging provision \
  --source-sha "$REVIEWED_SOURCE_SHA" \
  --image "ghcr.io/vbrekher/jacred-bunny:sha-$REVIEWED_SOURCE_SHA" \
  --registry-id "$PUBLIC_GHCR_REGISTRY_ID" \
  --max-estimated-usd 2 --apply
python3 bunny/cli.py native-staging status
```

Authentication comes from `BUNNYNET_API_KEY` or the authorized Bunny CLI profile. An interactive terminal can prompt for a missing key. Do not pass secrets in command arguments.

The command creates one AMS-primary Database with EU storage, one DE Storage zone and one standalone Edge Script with its linked HTTPS Pull Zone. A random deployment name fits the **actual live Database limit of 16 characters**. The OpenAPI description currently advertises a different limit; live validation takes precedence.

The local deployment manifest and credentials are separate mode-600 files inside a mode-700 directory. The manifest includes owner, creator, exact source SHA, image digest, timestamps, resource identities, migration state and `production_ready: false`. The account API key is not copied into that manifest.

A repeated provision verifies saved identities instead of creating duplicates. Simultaneous mutation commands are rejected by an OS file lock. A failed or ambiguous POST leaves its intent recorded and stops; it cannot be retried blindly. Reconcile the account response before resuming. Never adopt another product's resource because it has a similar name.

## Current boundaries

This command provisions the three data-plane resources only. It does not yet install the SQL collector, apply migrations, configure a 24-hour scheduler, restore production backups, or publish a ready catalog. Those are required before the normal installer can offer native deployment as its default. `Deployment.wire_edge` is an internal checksum-checked publishing primitive; its credentials stay server side and its database token is read-only.

The manifest's expiry records intent; **this data-plane primitive does not enforce automatic teardown**. The separate operator staging run documented in the evidence used a compute watchdog. Empty data-plane resources remain in the account until deliberately cleaned up. Storage allocation and Edge request increments can remain billable. No destructive cleanup or production cutover is implemented here.

See the [acceptance matrix](../docs/ACCEPTANCE_MATRIX.md) for remaining gates.

## Unified infrastructure installer (development)

The CLI now also supports `install`, `resume`, `health`, `doctor`, `start` and `stop` for a four-service staging stack. This remains infrastructure acceptance; it does not make SQL authoritative or satisfy the complete v0.5.4 gate.

An exact-source public payload contains `manifest.json`, `edge.js` and migrations `0001_catalog.sql` / `0002_search_fts5.sql`. The manifest pins the source-SHA OCI tag, digest and every file checksum. The image workflow builds and saves this payload alongside its exact-digest SBOM. No public native release payload is claimed available until its release assets are published and tested.

For a locally verified payload:

```sh
jacred-bunny native-staging install --payload-dir /path/to/payload --apply --max-estimated-usd 2
jacred-bunny native-staging install --payload-dir /path/to/payload --apply --max-estimated-usd 2
jacred-bunny native-staging health
jacred-bunny native-staging stop
jacred-bunny native-staging start --hours 0.1 --max-estimated-usd 2
```

Without `--apply`, install is a dry-run. `resume` runs the same idempotent pipeline. Once a verified public release carries these assets, `--release VERSION_TAG` downloads them from this repository without a GitHub PAT; `--source-sha` can additionally pin the expected release source. It refuses other repositories, unsafe redirects, altered files and unreviewed image digests.

Before creating paid resources the installer fetches the current official pricing pages, validates the published billing model, checks credit/quota and verifies the image. It discovers an accessible public GHCR registry from the account; no GitHub PAT is requested. Unknown pricing or a plan above the configured estimate budget stops installation. Egress remains separate, so this is **not a hard spending guarantee**.

The default compute window is one hour. A detached watchdog receives the account key through an anonymous pipe and keeps it only in memory. Deploy is refused until its heartbeat is confirmed. At expiry it verifies the saved worker identity, requests undeploy and confirms `inactive`. It does not delete volumes. It also applies and reads back monthly byte limits on only the newly owned linked CDN zones: 10 MB Edge, 25 MB worker. Direct worker egress is not bounded by those CDN limits.

SQL migrations commit their checksum receipt atomically; rerunning skips matching receipts and rejects altered applied migrations. The worker keeps the original persistent FileDB path. Search and admin keys are distinct, Edge has read-only SQL access, and writer credentials stay private. Infrastructure health verifies the worker's own source SHA and read-only SQL schema access; it explicitly reports catalog acceptance as unestablished.

Current limitations: the runtime flow retains staged resources after errors for diagnosis; automatic owned rollback/cleanup, production upgrade/rollback, every management command and the complete fresh public bootstrap gate remain open. The expiry watchdog and retained volume charges still apply. The earlier three-resource `provision` primitive does not arm a compute watchdog because it creates no worker.

[Actual four-service installer evidence](../docs/evidence/native-installer-2026-11-03.json).

## Image update, rollback and private SQL recovery (development)

```sh
jacred-bunny native-staging update --payload-dir /path/to/verified/payload
jacred-bunny native-staging update --payload-dir /path/to/verified/payload --apply --hours 0.1 --max-estimated-usd 2
jacred-bunny native-staging rollback --apply --hours 0.1 --max-estimated-usd 2
jacred-bunny native-staging backup
jacred-bunny native-staging restore-test --backup-id CATALOG_RECEIPT_ID
```

Update defaults to a dry-run. Applied updates pin the new image digest, preserve resource IDs and volumes, require an active budget watchdog, and validate both search and admin credentials plus the worker source. Rollback uses the saved immutable payload. SQL migrations must match exactly: this implementation refuses incompatible schema changes and never reverses SQL data. Failed updates attempt compensation to the previous verified image and Edge code; an uncertain result remains journalled for reconciliation. Repeating an already configured rollback preserves the worker's lifecycle, including a stopped worker, and explicitly does not claim a new runtime health check.

`backup` exports only the five canonical staging SQL tables inside one read transaction. All page requests share the same server baton; missing interactive-session support aborts the export. Typed values preserve signed 64-bit integers, Unicode and binary data. The private Storage zone must have no public Pull Zones. The upload checksum and download checksum must agree before a local receipt is saved. Keep the private state directory and receipt files; they identify the owned database, private object and schema checksums. This development command does not implement automated retention.

`restore-test` verifies the entire archive before creating a new, isolated database. It applies only trusted matching migrations, restores rows, compares canonical content hashes, and executes an FTS query. It deletes only that verified temporary database afterwards. The source database is never a restore target. An interrupted create must be reconciled from its saved intent before retrying; a temporary database can remain billable after interrupted cleanup.

These commands **do not back up legacy FileDB, Tracks objects, runtime configuration or worker jobs**. They cannot satisfy full production backup/restore acceptance. Live tests used explicitly synthetic data and removed their probe rows. The native database fork endpoint returned HTTP 403; its absence was reconciled against the account database list, and the supported SQL transaction mechanism was tested independently. [Live operation evidence](../docs/evidence/native-operations-2026-11-03.json).

The [experimental original .NET SQL runtime](SQL_RUNTIME.md) now has a real source-to-query acceptance slice. It requires an explicit backend setting and migration `0004_catalog_commits.sql`; the current infrastructure payload does not enable it. Full HTTP parity, worker rollout and complete recovery remain separate gates.

## SQL runtime payload v2

The packager accepts `--catalog-backend bunny-sql` to produce a v2 payload with explicit SQL authority and migrations 1, 2 and 4. Optional shard migration3 is excluded. The public packager CLI requires a clean tracked source tree at its declared SHA. The default legacy v1 payload remains supported.

A fresh v2 install supplies separate reader/writer tokens through private worker environment settings, records only a configuration fingerprint, verifies its remote readback, and requires the worker's `/version` to report `catalogBackend: bunny-sql`. A live FileDB process cannot satisfy SQL runtime readiness. Install/update/rollback reject an implicit backend change; migrating an existing FileDB installation needs a separate verified data transition. This format support is not fresh-public-install or deployed-worker acceptance.

SQL backup v2 additionally includes bucket versions and commit receipts. Restore rejects unresolved temporary database intents before allocating another database and confirms cleanup with HTTP404. A real three-release SQL catalog was restored from private Storage after its original database was deleted; the original query service returned the same response checksum. This covers SQL catalog recovery only. Tracks, runtime configuration, durable worker jobs, encryption policy, retention and complete production recovery remain required. Per-step elapsed values in the evidence are not an end-to-end RTO claim.

[SQL runtime backup and loss-of-source recovery evidence](../docs/evidence/native-runtime-backup-v2-2026-11-03.json).

## Explicit empty staging transition

`native-staging cutover-empty-sql --payload-dir /path/to/v2 --apply --hours 0.1 --max-estimated-usd 2` transitions an existing **empty, owned staging** worker to SQL without replacing its resources or volumes. It verifies the bridge image, denied anonymous admin access, disabled sync/tracks/cron, no background jobs, an empty canonical catalog, a fresh read-only integrity report with no orphan shards, empty tracks and an empty SQL target. Nonempty or unknown sources/targets are rejected; this is not general legacy migration.

The command saves intent before mutation, confirms the old worker is stopped before migrations, and patches image/private SQL settings together. Bunny's live API activated the application on container PATCH even when it was previously inactive. The implementation therefore accepts only the exact approved target under an authority-aware budget watcher; an approved active target without its watcher receives an immediate stop request. Lost responses are reconciled by the saved ID and complete configuration fingerprint. Unknown results are retained for reconciliation rather than blindly replayed. Legacy payloads/history are archived separately, and legacy images are excluded from SQL rollback history.

Live testing collected 20 real SubsPlease releases inside Magic Containers, found a release through HTTPS native and Jackett APIs, and preserved every row checksum plus the selected search response across stop/start. The same canonical snapshot was exported to both original FileDB layouts for the HTTP golden comparison. [Live worker evidence](../docs/evidence/live-sql-worker-2026-11-03.json). UI checks initially found an API-key retry defect; the subsequent gateway image fixed it and was tested without reloading.

## Original API Edge gateway development

The Edge source includes an opt-in `JACRED_EDGE_GATEWAY_ENABLED=true` mode with `JACRED_WORKER_ORIGIN` pinned to an HTTPS `mc-*.bunny.run` origin. A verified SQL payload can select `edge_mode: original-api-gateway`; the packager exposes `--edge-mode original-api-gateway`. The staging control plane derives the origin from the verified owned worker and records the selected mode. Existing payloads default to `preview`. The existing owned Edge was subsequently updated to this mode and tested, as recorded below.

This mode delegates the original routes, static UI, HTTP methods and application/admin authentication to the original .NET runtime with Bunny SQL as catalog authority. It forwards only client-provided application credentials; it does not inject an admin key, SQL token or account key. Incoming host, cookies and forwarded network identity are not trusted or forwarded. The source handler requests `private, no-store`, including errors. The initial deployed CDN rewrote this header to `no-cache`; dedicated owned CDN response-header rules were required to preserve it. Upstream redirects are refused rather than following a new origin. The total request/response deadline is 15 seconds; request bodies are limited to 2 MiB and response bodies to 8 MiB. These explicit resource bounds require testing against large catalog and synchronization workloads before production acceptance.

The local integration evidence in [`http-gateway-parity-2026-11-03.json`](../docs/evidence/http-gateway-parity-2026-11-03.json) compares 107 actual HTTP requests against both original FileDB layouts and the SQL runtime. All 107 gateway responses match the direct native runtime. Both baselines match 105 responses exactly; the two `/api/v1.0/conf` differences identify the distinct builds. The source dataset consists of the same 20 actual cloud-collected records; this does not establish all tracker, media, fuzz, migration, client or cloud Edge contracts. The local harness adapts the fixed upstream origin to loopback HTTP solely for integration testing. The subsequent cloud run is recorded below.


## Live Edge gateway pilot

[Live HTTPS evidence](../docs/evidence/live-edge-gateway-2026-11-03.json) records the exact image/source/bundle and owned Edge release. The existing SQL worker and Edge were updated without creating a new stack. All 20 canonical row checksums remained unchanged. Across 101 authenticated live HTTP cases, Edge status and JSON bodies matched the direct original API. Six additional Torznab XML cases matched byte for byte, including actual results and zero results. Anonymous administrative/search requests, wrong keys and spoofed local proxy identity were denied. Browser checks on the Edge endpoint verified English native search, Russian Jackett search and API-key save/refetch without a page reload.

The measured warmed request sample has p50 393 ms, p95 704 ms and maximum 1119 ms (101 Edge requests); it is not a controlled cold-start measurement or 24-hour soak. An initial read-only probe encountered a connection reset; the complete subsequent run is retained separately rather than counting the failed attempt as a PASS. No complete account usage attribution is available: actual test cost is `COST_UNKNOWN`. The worker was explicitly stopped and confirmed inactive after the bounded window. These are staging services, not an always-on public demo.

This proves a scoped live gateway pilot. General nonempty catalog migration, service-computed snapshot attestation, large-catalog bounds, all clients, all 25 functional trackers, durable scheduler state, full disaster recovery and production acceptance remain open.

## Immutable payload history

The advanced CLI stores verified payloads under `payload-objects/<manifest-identity>` and records both payload identities before update mutations. The identity includes the catalog backend and Edge mode, so two configurations of the same source/image/bundle cannot overwrite each other. Existing source-keyed release caches and history remain readable. New rollback receipts identify the exact prior manifest; repeating a completed rollback preserves the stopped lifecycle.

A same-source Edge mode change is an update, rather than a repeated no-op. Its previous payload remains available after publish failures and interrupted updates. A gateway release is only accepted when the verified owned Edge endpoint reports the exact full worker source/backend and accepts an authenticated search. These checks remain infrastructure checks, not production or full catalog acceptance.

[Live payload-history evidence](../docs/evidence/immutable-payload-history-2026-11-03.json) records a same-source/bundle/image `gateway → preview → rollback` on the existing owned staging. Both configurations retained distinct immutable payload IDs. Repeating rollback after stopping compute returned a no-op without checking runtime health or rearming compute. An Operations-layer injected response loss after a successful mode-secret PUT was compensated by restoring and republishing the exact previous payload; actual gateway source and authenticated search were then verified. All 20 original SQL row checksums remained unchanged, and the worker was stopped after testing. This does not complete public installer or production acceptance.

## Service-computed SQL snapshot proof

The administrative `GET /internal/version` source computes a SHA-256 over all seven canonical SQL tables in one read transaction. It validates each raw torrent checksum and each runtime bucket count/content receipt before accepting the proof. It accepts no operator-supplied checksum. The response includes the build's full source SHA, table counts and the algorithm name, without exposing rows, SQL credentials or database URLs. `/internal/` uses the same administrative middleware policy as `/dev/` and responses disable caching. FileDB mode returns 501; SQL errors, integrity failures and bounded transport failures return 503.

`jacred-sql-relational-snapshot/v1` uses typed length frames: one ASCII type byte (`s` UTF-8 string, `i` signed integer in invariant decimal, `n` null), eight unsigned big-endian bytes for the value's UTF-8 byte length, then the value bytes. Null has zero bytes. The stream contains the algorithm name, then the seven tables in `buckets`, `bucket_aliases`, `torrents`, `track_file_inventory`, `native_imports`, `catalog_bucket_versions`, `catalog_commits` order. Each table emits its name and row count; each row emits its column count followed by column-name/value pairs in ordinal column-name order. SQL orders rows by the corresponding primary/composite key. Unsupported scalar types fail closed. The independent `tools/bunny_snapshot_checksum.py` verifies a private canonical snapshot without uploading it.

This framed digest is intentionally distinct from SHA-256 of the snapshot's JSON serialization. The current source pages all seven tables on one serialized SQL read session, with rotating batons, verified provider HTTPS routing and bounded cleanup. Pages use keyset cursors, at most 64 rows and a conservative 1 MiB encoded-size estimate (one larger row may still use the 8 MiB response limit). No entire catalog is buffered. Per-bucket receipt URL/hash metadata still uses one bounded response to preserve original .NET ordinal sorting. The endpoint retains its 15-second deadline and allows only one proof per process; concurrent administrative requests return 429. A lost session, failed close or integrity failure never yields a partial checksum. Full large-catalog capacity and asynchronous/durable proof jobs remain required. Runtime bucket receipts must use the canonical content format; general legacy import/provenance conversion remains separate unfinished work. The deployed MC/Edge check is recorded in [service snapshot evidence](../docs/evidence/service-snapshot-attestation-2026-11-03.json). It matched the independent snapshot and denied ordinary/missing/wrong keys, including after an administrative response. The first cloud probe failed because CDN header rewriting removed `no-store`; the fix currently uses journalled rules on the existing owned zones. The subsequent CLI policy implementation and scoped live checks are recorded below.


Correction to the earlier live gateway narrative: the 101 captured `no_store` booleans were all false. They prove response parity, not no-store behavior. The new snapshot evidence records the subsequent CDN-header correction and actual positive/negative checks; the full security gate remains FAIL.


## CLI-enforced CDN response policy

Native staging install/resume, start, explicit empty cutover and actual update/rollback operations now verify or create an owned no-store response-header rule on each linked CDN zone. Saved zone/name/rule identity is checked before mutation; creation intent is persisted before POST. Lost creation responses are reconciled by reading the exact rule, with no blind POST replay. Missing or modified saved rules fail closed instead of overwriting another rule. Existing unrelated rules are preserved.

Health/doctor now require the actual authenticated worker search/admin and Edge gateway search responses to contain the no-store directive. A CDN rewrite to no-cache makes infrastructure readiness fail. This is stronger than testing the origin handler's intended headers. Native gateway installation also prepares the scoped reader credential, creates the worker, applies CDN policy and then publishes the gateway; publication requires the worker's verified endpoint.

[CLI policy evidence](../docs/evidence/cli-no-store-policy-2026-11-03.json) records two real rule creations on the existing owned linked zones, repeat without duplicate creation, verified replacement and cleanup of the earlier manual rules, actual HTTP no-store health checks and confirmed worker stop. These advanced native-staging checks do not establish a fresh public macOS/Linux bootstrap or the full production security gate.


## Tracker TLS validation source changes

The tracker transports and login handlers now use the platform's certificate validation instead of accepting every certificate. Operational Python capture/probe scripts retain their default verified SSL contexts; the Kinozal helper rejects an explicit insecure option. An invalid upstream certificate must produce an objective tracker limitation, not an insecure fallback.

[Source validation evidence](../docs/evidence/tls-validation-source-2026-11-03.json) records a real local self-signed TLS server rejected by the shared .NET transport. The same test fails with the previous permissive callback. The complete 965-test .NET suite and 184 Python tests passed. A pre-existing test race was fixed by isolating the tracker-list configuration override from concurrent search tests. The Build workflow now runs the .NET regression suite in CI.

The subsequent exact-image SubsPlease run below verifies one real collector with normal TLS validation. All 25 tracker and full security readiness remain open; the separately described announce-probe changes have source/local transport evidence only.

## Verified TLS collector slice and public announce probes

The exact TLS-hardened CI image `c0d5dfd00326f41ed63b23a618355e5578316df6` was deployed to the existing SQL staging worker. The original SubsPlease collector returned `ok`, increased torrents and commit receipts from 20 to 22, and preserved the previous source URLs. The service-computed digest matched a new independent snapshot. The original 20-record golden snapshot was retained unchanged. [Live collector evidence](../docs/evidence/tls-live-collector-2026-11-03.json) covers this one public tracker; it does not establish private tracker authentication or all 25 adapters. Compute was stopped after verification.

The source announce-list probe now decodes a candidate before checking it and excludes user information, queries, fragments, nested encodings and arbitrary paths. Only `/`, `/announce` and `/announce.php` enter the public list. Private passkey endpoints are excluded, rather than publishing their credentials. UDP without an explicit port retains port 6969.

Every DNS answer must be public unicast. Local, reserved, mapped-local, transition, documentation and known metadata addresses are rejected. HTTP connects directly to a vetted numeric IP, keeps normal TLS hostname/certificate validation, disables system proxy/cookies and refuses redirects. It accepts only successful or HTTP 400 responses as reachability observations, without reading the body. This does not validate a torrent announce result. UDP uses the [BEP 15 connection exchange](https://www.bittorrent.org/beps/bep_0015.html), validates action and transaction ID, and never treats a sent datagram as proof of a reply. It does not announce a fabricated swarm.

The background cycle allows at most 128 distinct endpoints, seven seconds per endpoint and two minutes in total, and responds to service cancellation. These limits can produce a partial list. Source tests exercise private/mixed DNS rejection, actual pinned HTTP redirect refusal and a real local UDP request/reply plus silent-endpoint timeout. Full deployed announce acceptance and the complete security audit remain open.

## Paged snapshot source validation

[Local paged snapshot evidence](../docs/evidence/paged-snapshot-source-2026-11-03.json) covers a dataset with 48 synthetic records across 24 buckets and more than 8 MiB of canonical data. The old one-response transaction fails specifically at the response-size guard; the paged proof matches the independent Python verifier. Supplementary Unicode URLs preserve the existing bucket receipt ordering. A persistent SQLite protocol fixture rotates stream batons and keeps the same actual connection, proving a concurrent commit remains outside an already-read snapshot. Failed BEGIN, missing baton, unsafe routing, cancelled callers and unconfirmed close are tested. This local evidence is not a deployed large Bunny catalog acceptance claim.

The same source was then rebuilt at exact commit `203817b53b069bc741e42db2734fe9973442ecd9` and served over local HTTP with the real Bunny Database read-only token. Its paged proof matched the independent 22-record real snapshot in 6.26 seconds. Two simultaneous authenticated requests returned 200 and 429; a later request succeeded, ordinary/missing/wrong keys returned 401, and a fake query checksum was ignored. [Real SQL session evidence](../docs/evidence/paged-snapshot-real-sql-2026-11-03.json) does not claim a deployed MC/Edge image or a large live Bunny dataset. No compute window was started for this test.
