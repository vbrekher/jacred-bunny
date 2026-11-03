# Production acceptance matrix

Checked: 3 November 2026. **IN PROGRESS — NOT PRODUCTION READY.**

`FAIL` means the complete mandatory gate is not satisfied. A successful infrastructure smoke is recorded as a subcheck and never promoted to a full gate PASS. There is no evidence of an external permission blocker so far; remaining implementation work is not labelled `BLOCKED_EXTERNAL`.

| Gate | Result | Evidence / remaining work |
| --- | --- | --- |
| SOURCE | FAIL | All CI through PR #28 passed. Current local full suite has 1027 .NET tests, including paged snapshot and atomic paged catalog writes. Complete reproducibility/security/license release evidence remains open. |
| FEATURE PARITY | FAIL | 107 immutable-snapshot local HTTP cases and scoped live 101 JSON/six XML gateway cases remain verified. Paged snapshot source passes an independent >8 MiB synthetic dataset and SQLite concurrent-write isolation. Exact source 203817b5 local HTTP using real Bunny SQL matched the 22-record snapshot; concurrent proof returned 429. Deployed large-catalog proof, all routes, richer media/fuzz and complete original parity remain incomplete. Local atomic writes now pass 1200-record and >4 MiB payload cases plus later-page rollback and immutable receipt retry; full large-catalog and migration acceptance remain incomplete. |
| REAL SQL | FAIL | Original in-cloud SubsPlease collector with TLS validation increased the real catalog and commit receipts from 20 to 22; old source URLs preserved and new service snapshot matched independent SQL snapshot. Complete 2+ DB live fanout/failure recovery and attributed usage metrics remain incomplete. Exact-source isolated Bunny SQL probe additionally committed 1200 rows/4915200 payload bytes, verified late-page rollback and cleanup, and preserved all seven canonical tables. |
| TRACKERS 25 | FAIL | All 25 source IDs retained. Earlier HEAD observations are not parser readiness. Actual source-to-SQL and query proof exists for SubsPlease; complete per-adapter live samples, auth setup and objective readiness matrix remain incomplete. |
| DURABILITY | FAIL | Real SQL catalog backup in Bunny Storage restored into a new DB after verified source loss; query checksum preserved. SQL MC restart preserved 20 real rows. General reimport, Tracks/config/jobs recovery and all-component restart gates remain incomplete. |
| WORKERS | FAIL | Original collector runs against SQL in the owned Magic Container. Durable scheduler leases/cursors/retries/crash resume, full concurrency acceptance and 24h live soak remain incomplete. |
| SECURITY | FAIL | Live auth/spoofed-proxy denials and CDN no-store checks verified; earlier unsupported no-store claim remains explicitly corrected. TLS bypasses removed and exact-image SubsPlease collection verified. Announce SSRF/UDP/passkey protections have source and local transport tests only; full deployed audit, scans, 429/500/RBAC and all adapters remain incomplete. |
| CLIENTS | FAIL | Actual English/Russian native and Jackett web search verified through Edge, including saved-key retry fix. Six Torznab XML protocol cases pass. Actual Lampa/Tizen/Prowlarr/Sonarr/Radarr applications and separate TorrServer integration remain incomplete. |
| INSTALLER | FAIL | Owned native staging install/update/rollback, idempotence, expiry stop, empty SQL cutover and gateway update tested. Standard public bootstrap still defaults to FileDB; fresh macOS/Linux SQL setup, all commands, general migration and full owned cleanup remain incomplete. |
| OPERATIONS | FAIL | Live warmed Edge latency measured for 101 cases (p50/p95/p99 in evidence); bounded worker stop confirmed. Actual attributed cost is COST_UNKNOWN. Complete bytes/cost telemetry, alerting, controlled cold-start measurements and full rollback/restore operations remain incomplete. |
| DOCUMENTATION | FAIL | English root README, real SQL/Edge product screenshots and dated scoped evidence updated; equivalent complete RU/EN/NL documentation and public product presentation remain incomplete. |
| RELEASE | FAIL | PRs through #28 merged with green checks. Exact TLS-hardened staging image tested; paged snapshot and write changes are not yet deployed to MC/Edge. No tagged v1.0.0 release, complete acceptance or public production landing page yet. |

[Machine-readable matrix](acceptance.json) · [Live Edge evidence](evidence/live-edge-gateway-2026-11-03.json) · [Local golden evidence](evidence/http-gateway-parity-2026-11-03.json) · [SQL disaster recovery](evidence/native-runtime-backup-v2-2026-11-03.json)

No tag through v1.0.0 is claimed complete by this matrix. Every mandatory gate remains FAIL until its full scope is proven; partial live checks are recorded above without promoting them to full acceptance.
