<div align="center">

# JacRed Bunny

### JacRed on bunny.net — no VPS

An independent AGPL fork of [JacRed](https://github.com/jacred-fdb/jacred), adapting its .NET torrent metadata search service to bunny.net.

[![Build](https://github.com/vbrekher/jacred-bunny/actions/workflows/build.yml/badge.svg)](https://github.com/vbrekher/jacred-bunny/actions/workflows/build.yml)
[![Edge tests](https://github.com/vbrekher/jacred-bunny/actions/workflows/bunny-native-edge.yml/badge.svg)](https://github.com/vbrekher/jacred-bunny/actions/workflows/bunny-native-edge.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue)](LICENSE)

**Development preview · v1.0.0 has not passed production acceptance**

[Installation: Русский · English · Nederlands](bunny/README.md) · [Acceptance matrix](docs/ACCEPTANCE_MATRIX.md) · [Upstream documentation](https://docs.jacred.stream)

</div>

## What works today

| Component | Verified state |
| --- | --- |
| Original .NET application | 1,081 local tests passed; original collector/query verified in a SQL-backed Magic Container pilot |
| Magic Containers | HTTPS source/backend attestation, digest-pinned update and SQL persistence across stop/start verified |
| Bunny Database | 22 real SubsPlease releases collected in-cloud; typed SQL, FTS, concurrency and read-only separation tested |
| Edge API | Original API gateway deployed to owned staging; 101 live HTTP comparisons and six Torznab XML cases passed |
| Bunny Storage | Checksum-verified SQL catalog restore after source loss; Tracks/config/jobs recovery remains open |
| 25 original tracker adapters | Source roster preserved; live parser readiness is not established |
| Native worker, durable scheduler and client acceptance | In progress; no production claim |

The standard installer now selects a checksum-verified SQL gateway release and refuses a FileDB fallback. Publication and fresh-install acceptance of that new default remain pending. An explicit SQL pilot now runs the original collector and search service against Bunny Database, with 22 real releases and verified restart persistence of the original 20-record snapshot. The original API also ran behind the real Bunny Edge gateway. This is not production acceptance or a general nonempty legacy migration. [Live gateway evidence and limits](docs/evidence/live-edge-gateway-2026-11-03.json) identify the exact scope. Staging compute is stopped after each bounded test window; these endpoints are not an always-on public demo.

TLS-hardened live collection and source announce-probe safeguards are recorded in the [changelog](CHANGELOG.md). Source SQL snapshot checks now page one consistent read session; a local dataset exceeding the former 8 MiB response limit matches an independent verifier. Catalog writes also use transport pages in one atomic transaction, retaining durable commit receipts. Bucket, index and receipt reads also page one consistent transaction. Per-row transport limits, whole-bucket memory, deadlines and complete large-catalog acceptance remain open.

The owned original FileDB volume now exports a verified private 30-file archive after a matching graceful-shutdown checkpoint. The pinned managed image uses read-only privileged preparation and unprivileged HTTP. The archive and credentials remain private; compute is stopped and its normal environment restored. [Real export evidence](docs/evidence/live-original-legacy-export-2026-11-10.json).

A separate new owned Bunny Database now contains 20 original records with preserved raw rows, metadata and provenance. Independent full SQL readback, FTS and actual .NET DTO/snapshot reads match the local candidate. The primary 22-record catalog is independently unchanged. This operator transfer is not public installer migration, HTTP authority cutover or complete restore. [SQL candidate evidence](docs/evidence/live-original-sql-candidate-2026-11-10.json) · [Preparation guide](bunny/LEGACY_MIGRATION.md) · [Read-only reproduction](tests/LiveLegacyImport/README.md).

Actual Linux CI through PR #42 passes 1,104 .NET tests (zero skipped), 245 Python tests and the exporter HTTP harnesses. Full production gates remain incomplete.

The paged SQL image was deployed and its MC/Edge snapshot, auth and no-store checks matched the retained 22-record catalog. The subsequent collector call produced no fresh commits; it is not new write-path acceptance. [Current runtime evidence](docs/evidence/live-paged-runtime-2026-11-03.json).

The subsequent administrative-key hardening image also passed a real nonempty MC/Edge search and config-schema permission checks. [Latest runtime evidence](docs/evidence/live-admin-key-boundary-2026-11-04.json). Compute remains bounded staging, not an always-on demo.

## Install the SQL development candidate

You need a bunny.net account, curl and Python 3.9+ on macOS/Linux. Local Docker/.NET and a GitHub PAT are unnecessary. Download and inspect the installer, then inspect its plan:

```sh
curl -fsSL https://raw.githubusercontent.com/vbrekher/jacred-bunny/main/install-bunny.sh -o /tmp/install-jacred-bunny.sh
sh /tmp/install-jacred-bunny.sh install --plan
sh /tmp/install-jacred-bunny.sh install --hours 1 --max-estimated-usd 2
```

The bootstrap pins one public source commit and atomically installs its modules. The CLI selects a public SQL gateway payload with an immutable image digest and three verified migrations. If that release is absent, it stops before requesting account credentials or creating resources. This source path is under development; fresh macOS/Linux SQL installation is not yet accepted.

The one-hour compute window and $2 estimate are not a hard account spending limit. Traffic is separately metered and Storage retains charges after stopping. The local watchdog requires the computer/network to remain available. Repeated installation preserves the existing lifecycle and window. Existing FileDB profiles require explicit verified migration; `setup` retains the separate legacy profile.

After SQL installation use `status`, `credentials`, `doctor`, `health`, `stop` and `start`. Advanced operations currently use `native-staging`. Full public migration, all 18 CLI modes and owned cleanup remain incomplete. [RU/EN/NL installation guide](bunny/README.md) · [Control-plane reference](bunny/NATIVE_STAGING.md).

## How it works

```mermaid
flowchart LR
  Clients[Browser / Lampa / Torznab clients] --> MC[Magic Containers: original .NET API]
  MC --> FDB[Explicit legacy profile: persistent FileDB]
  Clients --> Gateway[Bunny Edge: original API gateway]
  Gateway --> Pilot[SQL candidate: original .NET collector/API]
  Pilot --> SQL[Bunny Database: SQL candidate catalog]
  SQL -. SQL catalog backup/restore verified .-> Storage[Bunny Storage]
```

The target architecture moves the authoritative catalog and job state into SQL, keeps collection and media processing in Magic Containers, and serves compatible APIs through Edge. That transition is not complete.

## Clients and trackers

JacRed exposes native, Jackett, Torznab and Prowlarr-compatible routes. The original roster contains 25 tracker IDs. Source compatibility tests do not establish live availability or successful testing with actual Lampa, Samsung Tizen, Prowlarr, Sonarr or Radarr clients. Those production gates remain open.

Playback requires a separate service such as [TorrServer-bunny](https://github.com/vbrekher/TorrServer-bunny). JacRed indexes metadata; it does not stream video. Use sources and content you are authorized to access. Tracker credentials and cookies remain private.

## Costs and operations

Hosting is metered. On 3 November 2026 the published rates include CPU at $0.02/core-hour, RAM at $0.005/GB-hour and provisioned volumes at $0.10/GB-month. Egress is separate. The first Edge request activates a $0.22 billing increment; Database is free during public preview. Check the [current Magic Containers rates](https://bunny.net/docs/magic-containers/pricing.md), [Edge rates](https://bunny.net/docs/scripting/pricing.md), [Storage rates](https://bunny.net/docs/storage/pricing.md) and [Database pricing](https://bunny.net/database/).

A single region and replica reduce exposure but do not create a hard spending limit. Stopping compute retains billable volumes. Never delete or reuse another project's resources. Staging uses a compute expiry watchdog (one hour by default, shorter windows for live checks); it is a temporary test environment, not a permanent public demo.

## Real interface

This screenshot shows the real Bunny Edge endpoint serving a cloud-collected SQL release. English native search and Russian Jackett search worked. Saving the API key retried the same query without a reload, fixing the earlier browser defect. This does not establish full client or 25-tracker acceptance.

![Real Bunny Edge JacRed search showing an indexed SQL release](docs/images/edge-live-search-en.png)

## Documentation and release status

- [Acceptance matrix](docs/ACCEPTANCE_MATRIX.md) and [machine-readable gates](docs/acceptance.json)
- [Native rewrite plan, Russian](docs/BUNNY_NATIVE_REWRITE_PLAN.ru.md)
- [Installer and client guide, RU/EN/NL](bunny/README.md)
- [Historical README and milestone notes](README.history.md)
- [Security policy](SECURITY.md)

Complete, equivalent English/Russian/Dutch documentation, native one-command installation, backup/restore acceptance, client tests and a verified v1.0.0 release remain required. No v1.0.0 tag or production-ready badge is published before those gates pass.

## License and thanks

[AGPL-3.0](LICENSE). JacRed Bunny is a fork, not an official bunny.net product. Thank you to the original JacRed authors and maintainers: the application, tracker adapters and client contracts originate from their work. Original attribution and license remain in the source. Operators of modified network services must provide the corresponding source under the AGPL.
