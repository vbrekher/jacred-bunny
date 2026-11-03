# Native SQL catalog runtime — development

The original .NET collector merge path and query services can now use Bunny Database as the authoritative catalog. This is an explicit development mode, not the installer's default and not full production data-plane acceptance.

The service setting `JACRED_CATALOG_BACKEND=bunny-sql` requires three private service settings: `JACRED_DATABASE_URL`, `JACRED_DATABASE_READ_TOKEN` and `JACRED_DATABASE_WRITE_TOKEN`. Supply these through the service's private secret configuration. Never put tokens into CLI arguments, static frontend files or public examples. Startup verifies all three migration receipts against the exact SQL embedded in its build. An unknown backend, missing configuration, unavailable database or changed migration checksum fails; there is no local FileDB fallback.

Apply migrations `0001_catalog.sql`, `0002_search_fts5.sql` and `0004_catalog_commits.sql` in order using the existing checksum-receipt migration transport. The current v1 infrastructure payload still packages migrations 1–2; it does **not** enable this runtime mode. A coordinated installer/payload upgrade and live immutable-image rollout remain required. Do not manually change a deployment journal's migration receipts to bypass compatibility checks.

The existing `FileDB.OpenRead/OpenWrite/AddOrUpdate` entry points now select SQL in this mode. All 25 tracker implementations retain their existing parser code and common merge path. Each bucket read obtains its revision and canonical rows in one SQL read transaction. Raw JSON preserves the complete `TorrentDetails` model, while indexed columns and FTS are maintained atomically. The alias index is a short-lived SQL-derived view. Search does not read local FileDB shards in this mode.

A write checks its previously read bucket revision inside the same transaction as its rows, aliases, metadata and commit receipt. A competing writer with a stale revision fails the transaction. The caller must reload and repeat its intended merge; the store does not overwrite newer data or silently retry a mutation. Commit IDs reconcile uncertain responses. Multi-bucket moves use one transaction with a revision guard for every source and destination. Repeating the same committed group returns its matching receipts. Empty-bucket deletion retains its revision so an old writer cannot resurrect deleted state unnoticed.

The HTTP transport follows Bunny's documented Hrana interface, binds typed parameters, preserves exact signed 64-bit values and binary data, refuses credential-bearing/arbitrary URLs and redirects, bounds request/response sizes, and applies an end-to-end deadline. Each batch step depends on the preceding step's success; a failed `BEGIN` cannot fall through to autocommit writes. Failed statements lead to rollback and stream closure. Error messages omit SQL, row values and tokens.

## Verified scope

- Real SQLite-backed tests exercise full model round trips, FTS, deletion, duplicate delivery, uncertain commit responses, concurrent writers, failed transaction start, failed statements, grouped moves, stale destinations, original FileDB merge behavior and scope abortion.
- Live Bunny tests used an isolated owned database for typed parameters, read-only write denial, FTS, optimistic concurrency, rollback and receipt reuse.
- A fresh public SubsPlease response was captured after checking `robots.txt`. The original parser produced 20 releases; three were written through the normal collector entry point to Bunny SQL and found by the original query service. A new process returned the same query-body checksum. The temporary databases were deleted and checked with HTTP404.

These tests do not establish all-tracker readiness, HTTP golden parity, deployed Magic Containers runtime acceptance, durable scheduling, complete Tracks/configuration migration, or full backup/restore. Filesystem-oriented maintenance commands, large-bucket streaming, distributed retry scheduling and sync/checkpoint durability still need integration. Writes are currently bounded to 900 rows per bucket, 32 buckets per group, 1,000 SQL statements and 4 MB per request; oversized operations fail before mutation. Catalog reads are bounded to 8 MB per response. Existing SQL backups do not yet cover the new revision/commit tables and cannot be claimed as production recovery for this mode.

[Redacted live evidence](../docs/evidence/native-sql-runtime-2026-11-03.json). No version-completion gate is marked PASS from this acceptance slice.
