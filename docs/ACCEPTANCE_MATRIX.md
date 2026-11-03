# Production acceptance matrix

Checked: 3 November 2026. **IN PROGRESS — NOT PRODUCTION READY.**

`FAIL` means the complete mandatory gate is not satisfied. A successful infrastructure smoke is recorded as a subcheck and never promoted to a full gate PASS. There is no evidence of an external permission blocker so far; remaining implementation work is not labelled `BLOCKED_EXTERNAL`.

| Gate | Result | Evidence / remaining work |
| --- | --- | --- |
| SOURCE | FAIL | CI and 946 .NET tests passed for PR #10; native foundation CI pending; reproducible release artifact and scans incomplete. |
| FEATURE PARITY | FAIL | Existing 91 Edge tests pass; 100-case immutable original/live golden corpus has not run. |
| REAL SQL | FAIL | Live CRUD/FTS5/transaction/least-privilege smoke passed with synthetic rows; real catalog, multi-DB fanout and recovery untested. |
| TRACKERS 25 | FAIL | All 25 source IDs retained; 22 LIMITED / 3 UPSTREAM_UNAVAILABLE host HEAD observations; live parser samples and source-to-SQL flow not run. |
| DURABILITY | FAIL | Storage PUT/GET checksum verified; real backup restore into a new DB and restart preservation gates not complete. |
| WORKERS | FAIL | SQL collector, durable queue integration and 24h live soak incomplete. |
| SECURITY | FAIL | Live missing-key 401 and no-cache responses verified; full scans/429/SSRF/RBAC matrix incomplete. |
| CLIENTS | FAIL | Real browser UI checked; actual Lampa/Tizen/Prowlarr/Sonarr/Radarr/TorrServer integration untested. |
| INSTALLER | FAIL | Four services created by one CLI run; repeat preserves IDs, migrations skip; expiry stop/restart verified. Default public bootstrap, real catalog fixture, all operations and owned cleanup remain incomplete. |
| OPERATIONS | FAIL | 20 MC health samples, real expiry watchdog and CDN byte caps recorded; full usage accounting, alerts and DR incomplete. |
| DOCUMENTATION | FAIL | English root README and real empty-catalog screenshots added; equivalent full RU/EN/NL sets incomplete. |
| RELEASE | FAIL | PR #10 merged after CI; native foundation PR pending; no v1.0.0 release or production launch. |

[Machine-readable matrix](acceptance.json) · [Dated live evidence](evidence/2026-11-03-staging.md)

No tag through v1.0.0 is claimed complete by this matrix. Test counts describe source checks; they do not prove tracker or client availability.
