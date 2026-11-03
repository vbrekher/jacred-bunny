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

Live testing collected 20 real SubsPlease releases inside Magic Containers, found a release through HTTPS native and Jackett APIs, and preserved every row checksum plus the selected search response across stop/start. The same canonical snapshot was exported to both original FileDB layouts for the pending HTTP golden comparison. [Live worker evidence](../docs/evidence/live-sql-worker-2026-11-03.json). UI checks found an API-key retry defect; reload recovered the real result, and fixing that defect remains required.

## Original API Edge gateway development

The Edge source includes an opt-in `JACRED_EDGE_GATEWAY_ENABLED=true` mode with `JACRED_WORKER_ORIGIN` pinned to an HTTPS `mc-*.bunny.run` origin. A verified SQL payload can select `edge_mode: original-api-gateway`; the packager exposes `--edge-mode original-api-gateway`. The staging control plane derives the origin from the verified owned worker and records the selected mode. Existing payloads default to `preview`. The existing live Edge preview remains separate and unaccepted until a gateway payload is deployed and tested.

This mode delegates the original routes, static UI, HTTP methods and application/admin authentication to the original .NET runtime with Bunny SQL as catalog authority. It forwards only client-provided application credentials; it does not inject an admin key, SQL token or account key. Incoming host, cookies and forwarded network identity are not trusted or forwarded. Requests and responses use `private, no-store`, including errors. Upstream redirects are refused rather than following a new origin. The total request/response deadline is 15 seconds; request bodies are limited to 2 MiB and response bodies to 8 MiB. These explicit resource bounds require testing against large catalog and synchronization workloads before production acceptance.

The local integration evidence in [`http-gateway-parity-2026-11-03.json`](../docs/evidence/http-gateway-parity-2026-11-03.json) compares 107 actual HTTP requests against both original FileDB layouts and the SQL runtime. All 107 gateway responses match the direct native runtime. Both baselines match 105 responses exactly; the two `/api/v1.0/conf` differences identify the distinct builds. The source dataset consists of the same 20 actual cloud-collected records; this does not establish all tracker, media, fuzz, migration, client or cloud Edge contracts. The local harness adapts the fixed upstream origin to loopback HTTP solely for integration testing. Live Bunny Edge acceptance remains required.
