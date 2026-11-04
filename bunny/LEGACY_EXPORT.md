# Private legacy byte export — development mode

This separate process mode prepares a private archive of original FileDB bytes. It does **not** import a catalog into SQL or complete an installer upgrade. The CLI lifecycle controller has source/local validation; owned-worker cloud export, nonempty SQL migration and restore acceptance remain incomplete.

## Isolation and required controller sequence

`JacRed legacy-export` branches before normal application initialization. It registers no configuration controllers, collector routes, FileDB singleton or hosted workers. In the Bunny image, `JACRED_LEGACY_EXPORT=1` also branches before config copies, volume ownership changes, cron and the regular entrypoint.

A migration controller must first identify its owned legacy app, checkpoint the original service, stop it and verify that all normal writers have stopped. It must verify a single replica/region and retain the same original volumes when replacing the process. A second replica, sidecar or external process that writes those volumes invalidates this procedure. The exporter checks source topology, lengths, modification times and SHA256 twice; those checks do not establish a distributed write fence. The archive manifest explicitly records `REQUIRES_VERIFIED_SINGLE_WORKER_REPLACEMENT`.

Never point this mode at another project's resources. Never reset the existing SQL catalog to make an import pass. The current 22-record SQL staging catalog has not been used as a migration target for this mode.

## Inputs and bounds

The process reads `JACRED_LEGACY_EXPORT_DATA_ROOT` (default `/app/Data`) and `JACRED_CONFIG_DIR` (default `/app/config`). It requires a root `masterDb*.bz` and a persistent `init.yaml` with one managed `devkey`: 24–128 ASCII letters, digits, `_` or `-`, optionally single/double quoted. It reads that current private file directly, without loading normal configuration. It refuses SQL backend mode and unsupported key formats rather than exporting a stale FileDB copy or using an account credential.

`JACRED_LEGACY_EXPORT_DEADLINE_UNIX` is mandatory: an absolute Unix expiry at most ten minutes in the future. An expired or excessive window fails before export. An unchanged expiry cannot be renewed by restarting the process. Preparation and each transfer have a 90-second deadline. Process exit is **not** a Bunny compute stop or a hard spending limit; the migration controller still needs the owned-app stop watchdog and aggregate cost policy.

The default bounds are 256 MiB of source bytes, 10,000 files and 10,000 directories, with member names no longer than 1,024 characters. Preparation streams bytes through bounded buffers. It refuses symlinks, unsafe member names and non-seekable FIFO sources; source reads use `O_NOFOLLOW` and `O_NONBLOCK` on supported macOS/Linux hosts. Parent directories must be trusted; this is not a sandbox against a malicious local filesystem owner. Larger catalogs need a separately verified streaming/chunked migration path.

The generated `legacy.tar.gz` lives in a fresh private 0700 temporary directory and has mode 0600. It contains every regular file under `Data/` and `config/`, including original compressed shards, daily masters, Tracks/probe files, checkpoints and runtime configuration. It preserves bytes and modification timestamps; archived file permissions are private 0600. `manifest.json` records the built service's source SHA, member paths, lengths, timestamps and SHA256. The mode does not select a winning daily master or rewrite any source file. It does not validate FileDB semantics, job resumability or SQL compatibility.

Archives contain credentials and potentially tracker passkeys from those original files. Keep them private; never upload them to GitHub, public CI artifacts or a public Storage namespace.

## HTTP surface

The default listener is `0.0.0.0:9117`. Local diagnostics can set `JACRED_LEGACY_EXPORT_LISTEN_IP` and `JACRED_LEGACY_EXPORT_PORT`. Use the owned Bunny HTTPS endpoint for remote transfer.

| Route | Access and behavior |
| --- | --- |
| `GET /health` | Public mode health; `productionReady: false` |
| `GET /version` | Public built source SHA and `legacy-export-only` mode |
| `GET /migration/archive` | Current `X-Dev-Key` required; no query-key, cookie, search-key or LAN bypass |

All responses request `private, no-store`; there is no CORS reflection. Normal configuration, search, collector and write routes are absent. A single transfer may run at a time. At most three authenticated download attempts are allowed per process; additional attempts return 429. Range headers do not bypass the complete archive or transfer budget.

The successful response carries `X-JacRed-Archive-Sha256` and `X-JacRed-Source-Sha`. A client must download to a new 0600 file, verify the complete compressed digest, verify the service source against the approved immutable image and verify every manifest member before considering an import. Do not extract archive paths blindly. Cleanup removes the temporary archive after graceful shutdown; a forced pod termination can leave ephemeral files until that pod's filesystem is removed.

## Verified scope

The public no-cloud reproduction harness defaults to a plan that starts nothing:

```sh
python3 tools/bunny_legacy_export_acceptance.py
```

For development acceptance, build the current exact checkout with .NET 10 and run the explicit local process check:

```sh
dotnet build JacRed.csproj --configuration Release
python3 tools/bunny_legacy_export_acceptance.py --run --app bin/Release/net10.0/JacRed.dll
```

The harness compares the running `/version` source SHA to the checkout, generates only a synthetic FileDB and dummy keys, disables proxies/redirects, verifies complete archive/member hashes and stops its processes. It rejects Python optimization that would disable assertions. The Linux CI unit job invokes the same harness after building the Release app; the local evidence does not claim that CI has already passed.

[Local process evidence](../docs/evidence/legacy-export-local-2026-11-04.json) records actual loopback HTTP using a synthetic FileDB and dummy keys, independent tar/hash verification, missing/wrong/search/query-key denials, absent normal routes, download exhaustion, deadline rejection/shutdown and unchanged source bytes. Unit tests cover raw Tracks/config/checkpoint bytes, timestamps, private modes, daily masters, cancellation, bounds, symlinks and a real FIFO. Shell tests execute the actual boot dispatcher with mutation commands trapped.

This evidence is not a Bunny volume export, nonempty SQL import, restore, fresh public installation or production durability PASS. All mandatory production gates remain incomplete.

## CLI controller and verified normal shutdown

`native-staging legacy-export` defaults to a no-change plan. With `--apply`, it verifies the saved owned worker, allocation, original volumes, current source and normal-process capabilities before enabling export. The normal bridge must report both `legacyByteExportVersion: "1"` and `legacyCheckpointVersion: "1"`, with `legacyCheckpointReady: "1"`. Older images that ignore the export flag are rejected.

```sh
jacred-bunny native-staging legacy-export --state-dir /private/state
jacred-bunny native-staging legacy-export --state-dir /private/state --apply --hours 0.15 --max-estimated-usd 2
```

A new controller window is capped at nine minutes and still uses the existing pricing estimate, owned CDN policy and independent stop watchdog. The watcher must advertise `legacy_export_stop`; an older watcher is replaced only after stopping the owned worker and confirming the previous watcher's terminal state. The controller never sends the account key to the worker. Saved environment values stay in a separate 0600 private file; the deployment journal contains their fingerprints.

The ordinary `/jsondb/save` response is **not** a completed checkpoint: the original route schedules background work and its writer suppresses errors. The normal bridge instead registers a shutdown checkpoint service before the configuration watcher and other hosted workers, with sequential service shutdown enforced. After HTTP and workers stop, it waits for tracked jobs, refuses active write scopes, closes legacy write admission and flushes cached writes and the master. It verifies the exact original writer encoding with streaming hashes and fsync before writing an owned receipt. Startup invalidates the previous owned receipt; an unrecognized receipt is preserved and export readiness remains false.

The receipt binds the observed normal process ID, built source SHA, actual compressed master SHA and key count. The client verifies that receipt inside the complete private archive against the process observed before stopping it. A plain byte archive without the matching shutdown receipt cannot pass this controller's checkpoint verification. This is a single owned-worker protocol; it does not coordinate an external writer that bypasses the deployment.

The controller journals environment changes before PATCH, reconciles an uncertain result by reading the same resource, and never blindly replays an uncertain enable PATCH. Normal `start` refuses a pending export. After transfer or failure, the controller stops compute and restores the original environment while the app remains inactive. Re-running a pending export performs compensation and reports `RECOVERED_RETRY_REQUIRED`, without claiming an archive was verified. Verified archives and private recovery files stay under the installation's `legacy-exports/<export-id>/` directory. No SQL schema, catalog authority, Edge code or Storage object is changed by this operation.

The archive verifier checks every member's bytes and timestamp, complete manifest identity, gzip CRC, tar end/trailing payload, duplicate JSON fields, portable filename collisions and expansion/metadata bounds. It refuses links, sparse/special entries and non-private archive files. It never extracts the archive.

[Checkpoint/controller evidence](../docs/evidence/legacy-checkpoint-controller-2026-11-04.json) records actual local normal shutdown → exporter → Python verification, plus explicitly mocked control-plane failure tests. Cloud environment switching, nonempty SQL import, executable permissions on restore and full production migration acceptance remain incomplete.

## Latest owned staging attempt

The legacy bridge image `16d4e3ee` started normally on the owned original FileDB app, and the SubsPlease job finished with nonempty catalog assertions. The subsequent isolated exporter did not confirm its source/mode within the 90-second client window. Compensation restored the original environment and stopped compute. No real archive was verified. The same published Alpine image exports synthetic bytes successfully in local Docker; the actual owned-volume startup cause remains unconfirmed. See [dated recovery and attempt evidence](../docs/evidence/historical-payload-reconstruction-2026-11-04.json).

## Read-only source diagnosis

New source adds administrative `GET /dev/LegacyExportPreflight` for diagnosing a failed owned-volume startup before another export attempt. It uses the existing `X-Dev-Key` boundary and sends `private, no-store`. SQL authority is refused before source access. It validates the managed configuration key, bounded source inventory and actual opening/first-byte reading of regular files. It creates no archive, checkpoint or configuration file.

The response contains a fixed phase (`configuration`, `inventory`, `source_open` or `complete`), a fixed failure code and aggregate counts on success. It never returns member names, paths, keys or exception messages. `READABLE_SOURCE_METADATA` does not validate compressed FileDB semantics, full content hashes or immutability. Both `quiescenceVerified` and `productionReady` stay false. Normal writers may still be active; this diagnosis cannot authorize SQL cutover or replace the stopped-worker checkpoint protocol.

The ten-second cancellation is cooperative between filesystem operations, with the same 256 MiB inventory and 10,000-file/directory bounds. It cannot interrupt an individual blocked kernel filesystem call. Missing configuration or unsafe topology can produce `LAYOUT_REFUSED`; the phase identifies which part failed without disclosing a private filename. Source support and actual owned-volume diagnosis must be verified against the next deployed image; the current legacy cloud bridge `16d4e3ee` does not contain this route.

[Source diagnosis evidence](../docs/evidence/legacy-source-preflight-2026-11-04.json) records three missing/wrong/search-key HTTP denials, an authenticated no-store report, unchanged export bytes and a matching shutdown receipt from the exact local build. The separate published Alpine normal-boot/shutdown/export check uses synthetic data and actual image digest `a8ecf69c`; it does not diagnose the real cloud failure.
