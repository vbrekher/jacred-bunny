# Private legacy byte export — development mode

This separate process mode prepares a private archive of original FileDB bytes. It does **not** import a catalog into SQL or complete an installer upgrade. Owned-worker lifecycle automation, nonempty cloud migration and restore acceptance remain incomplete.

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
