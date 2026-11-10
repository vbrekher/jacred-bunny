# Production acceptance matrix

Checked: 10 November 2026. **IN PROGRESS — NOT PRODUCTION READY.**

`FAIL` means the complete mandatory gate is not satisfied. A successful infrastructure smoke is recorded as a subcheck and never promoted to a full gate PASS. There is no evidence of an external permission blocker so far; remaining implementation work is not labelled `BLOCKED_EXTERNAL`.

| Gate | Result | Evidence / remaining work |
| --- | --- | --- |
| SOURCE | FAIL | PRs through #42 merged with green checks. Actual Linux CI: 1104 .NET tests, 245 Python tests and actual HTTP acceptance. Pinned image 0878/f44 passed protected-source/UID 1000 image acceptance. Complete release reproducibility, licensing and security acceptance remain open. |
| FEATURE PARITY | FAIL | 107 local immutable-golden HTTP cases and scoped live 101 JSON/six XML gateway cases remain verified. Primary 22 MC/Edge snapshot checks passed previously. The new20-record original-data candidate passes actual native DTO/snapshot reads; migrated HTTP/Edge, every original route, richer media/fuzz and full deployed capacity remain incomplete. |
| REAL SQL | FAIL | Primary 22-row collector catalog is unchanged. A separate NEW owned empty Bunny Database now contains 20 original-volume records: all seven tables match the local stage, independent Python/.NET framed snapshots agree and FTS matches. This is an operator candidate, not authority cutover or 2+ DB query fanout. Full fanout/failure/capacity/cost acceptance remains open. |
| TRACKERS 25 | FAIL | All 25 original source IDs retained. Actual collector/SQL/search proof exists for SubsPlease. Complete per-adapter live samples, credentials/auth/challenge setup and objective readiness evidence remain incomplete. |
| DURABILITY | FAIL | Prior real private Storage SQL backup/restore and restart slices remain verified. New original 30-file archive has a matching shutdown receipt and complete byte/hash verification; original 20-row SQL reimport into a new candidate is verified. Full config/Tracks/jobs recovery, executable/ownership restoration and all-component restart acceptance remain incomplete. |
| WORKERS | FAIL | Original collector runs against SQL in owned Magic Containers. Durable scheduler leases/cursors/retries/crash resume, full concurrency acceptance and 24h live soak remain incomplete. |
| SECURITY | FAIL | Actual admin-key/no-store/TLS/announce-boundary slices and protected-reader image checks pass within their recorded scopes. npm production audit found 0 issues; development audit found 19 low/moderate issues; NuGet reported no affected packages. Full scans, RBAC/CORS/error/logging review, key rotation and privileged cron review remain incomplete. |
| CLIENTS | FAIL | Actual English/Russian native and Jackett web search plus six Torznab XML protocol cases remain verified. Actual Lampa/Tizen/Prowlarr/Sonarr/Radarr applications and separate TorrServer integration remain incomplete. |
| INSTALLER | FAIL | Owned staging install/update/rollback and empty SQL cutover remain verified. Actual nonempty original-volume export now passes; local runtime preparation and operator import of20 original records into a new SQL candidate pass. Public bootstrap still defaults to FileDB; fresh macOS/Linux SQL setup, all commands, resumable public migration, HTTP authority cutover and full cleanup remain incomplete. |
| OPERATIONS | FAIL | Both owned workers stopped, original environments restored and primary 22 snapshot independently unchanged. Prior scoped live latency evidence retained. Actual attributed cost is COST_UNKNOWN; complete byte/cost telemetry, alerts, controlled cold starts and full rollback/restore operations remain incomplete. |
| DOCUMENTATION | FAIL | Real product screenshots and scoped original-volume/SQL-candidate evidence exist. Equivalent complete RU/EN/NL documentation, Dutch UI translation and professional public product presentation remain incomplete. |
| RELEASE | FAIL | PRs through #42 merged with green checks. Legacy 0878/f44 export and separate original-data SQL candidate verified; primary ef2/22 retained. No production HTTP cutover, always-on deployment, v1.0.0 tag or complete production acceptance. |

[Machine-readable matrix](acceptance.json) · [Live Edge evidence](evidence/live-edge-gateway-2026-11-03.json) · [Local golden evidence](evidence/http-gateway-parity-2026-11-03.json) · [SQL disaster recovery](evidence/native-runtime-backup-v2-2026-11-03.json)

No tag through v1.0.0 is claimed complete by this matrix. Every mandatory gate remains FAIL until its full scope is proven; partial live checks are recorded above without promoting them to full acceptance.

[Historical recovery and failed cloud export evidence](evidence/historical-payload-reconstruction-2026-11-04.json). The exact published Alpine image exports synthetic bytes locally; this does not establish owned-volume export acceptance.

[Read-only source preflight evidence](evidence/legacy-source-preflight-2026-11-04.json) records exact-source local HTTP and a separately scoped published Alpine synthetic lifecycle. Neither is an owned-volume cloud export PASS.

[Actual original-volume archive](evidence/live-original-legacy-export-2026-11-10.json) and [new SQL candidate verification](evidence/live-original-sql-candidate-2026-11-10.json) supersede the earlier failed-export observations for those scoped subchecks. They do not complete public migration/cutover, restore or production gates.
