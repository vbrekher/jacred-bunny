# Production acceptance matrix

Checked: 3 November 2026. **IN PROGRESS — NOT PRODUCTION READY.**

`FAIL` means the complete mandatory gate is not satisfied. A successful infrastructure smoke is recorded as a subcheck and never promoted to a full gate PASS. There is no evidence of an external permission blocker so far; remaining implementation work is not labelled `BLOCKED_EXTERNAL`.

| Gate | Result | Evidence / remaining work |
| --- | --- | --- |
| SOURCE | FAIL | PRs #19–21 merged after successful CI; exact CI image/digest and matching Edge bundle deployed. 169 Python, 101 Edge Node, 962 .NET and 73 web checks recorded. Complete reproducibility/security/license release evidence remains open. |
| FEATURE PARITY | FAIL | 107 immutable-snapshot local HTTP cases: 105 exact baseline matches, two build-version-only differences; all 107 local gateway/native matches. Live Edge/native: 101 JSON and six XML cases match. All required routes, richer datasets/fuzz and large-catalog service attestation remain incomplete. Protected service-computed SQL digest now matches the independent real snapshot through MC and Edge; bounded 20-row scope only. |
| REAL SQL | FAIL | Original in-cloud collector wrote 20 real SubsPlease records to Bunny SQL; native/Edge reads and all row checksums verified after image update and restart. Complete 2+ DB live fanout/failure recovery and actual attributed usage metrics remain incomplete. |
| TRACKERS 25 | FAIL | All 25 source IDs retained. Earlier HEAD observations are not parser readiness. Actual source-to-SQL and query proof exists for SubsPlease; complete per-adapter live samples, auth setup and objective readiness matrix remain incomplete. |
| DURABILITY | FAIL | Real SQL catalog backup in Bunny Storage restored into a new DB after verified source loss; query checksum preserved. SQL MC restart preserved 20 real rows. General reimport, Tracks/config/jobs recovery and all-component restart gates remain incomplete. |
| WORKERS | FAIL | Original collector runs against SQL in the owned Magic Container. Durable scheduler leases/cursors/retries/crash resume, full concurrency acceptance and 24h live soak remain incomplete. |
| SECURITY | FAIL | Live auth/spoofed-proxy denials verified. Correction: earlier 101-response no-store claim was unsupported (all captured flags false). Owned CDN header rules now preserve no-store and authenticated-then-anonymous checks passed; automatic CLI enforcement, tracker TLS/SSRF fixes and full scans/429/500/RBAC remain incomplete. |
| CLIENTS | FAIL | Actual English/Russian native and Jackett web search verified through Edge, including saved-key retry fix. Six Torznab XML protocol cases pass. Actual Lampa/Tizen/Prowlarr/Sonarr/Radarr applications and separate TorrServer integration remain incomplete. |
| INSTALLER | FAIL | Owned native staging install/update/rollback, idempotence, expiry stop, empty SQL cutover and gateway update tested. Standard public bootstrap still defaults to FileDB; fresh macOS/Linux SQL setup, all commands, general migration and full owned cleanup remain incomplete. |
| OPERATIONS | FAIL | Live warmed Edge latency measured for 101 cases (p50/p95/p99 in evidence); bounded worker stop confirmed. Actual attributed cost is COST_UNKNOWN. Complete bytes/cost telemetry, alerting, controlled cold-start measurements and full rollback/restore operations remain incomplete. |
| DOCUMENTATION | FAIL | English root README, real SQL/Edge product screenshots and dated scoped evidence updated; equivalent complete RU/EN/NL documentation and public product presentation remain incomplete. |
| RELEASE | FAIL | PRs through #21 merged with green checks; exact source/digest gateway staging tested. No tagged v1.0.0 release, complete release acceptance or public production landing page yet. |

[Machine-readable matrix](acceptance.json) · [Live Edge evidence](evidence/live-edge-gateway-2026-11-03.json) · [Local golden evidence](evidence/http-gateway-parity-2026-11-03.json) · [SQL disaster recovery](evidence/native-runtime-backup-v2-2026-11-03.json)

No tag through v1.0.0 is claimed complete by this matrix. Every mandatory gate remains FAIL until its full scope is proven; partial live checks are recorded above without promoting them to full acceptance.
