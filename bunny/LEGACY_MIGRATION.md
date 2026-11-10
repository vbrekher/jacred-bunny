# Original FileDB to runtime SQL — preparation only

This development path prepares a new private SQLite file for the current native runtime schema. It performs no network requests, creates no Bunny resources and changes no catalog authority. It is not a completed public installer migration, remote import, restore or production acceptance.

A real original-volume archive still needs complete private verification, a matching normal-worker shutdown receipt and a safe immutable working copy. The archive verifier does not extract files. Do not treat an arbitrary live FileDB directory or an unverified archive as that working copy.

## Local stages

From a trusted, immutable working copy, first create the existing original-data stage:

    python3 tools/bunny_native_import.py import --data-root /private/working-copy/Data --output /private/stage/original.sqlite
    python3 tools/bunny_native_runtime_stage.py --database /private/stage/original.sqlite --output /private/stage/runtime.sqlite

Both tools use Python's standard library. These commands require neither local .NET nor Docker. They are development tools in the repository; they are not yet integrated into the installed public migration command.

The second command requires an owned private closed SQLite stage, the exact unsharded original stage schema and a new output destination. It rejects WAL/journal sidecars, changed input, extra schema objects, symlinks and existing or concurrently created output files. A fresh mode-0600 temporary file is published atomically without replacing another file. Parent directories must be trusted; this is not a sandbox against another process running as the same local user.

## Preserved data and changed receipt format

The candidate copies every torrent SQL row, its original raw_json string and SHA256, aliases, Track inventory and native_imports provenance. It preserves bucket keys, original update/file times, counts and MD5 keys. It validates matching source URLs and native bucket-key bounds without serializing a torrent DTO. Unknown JSON fields therefore survive this step unchanged. Every individual DTO field and every search behavior still require actual runtime validation.

The original stage's bucket shard_sha256 describes the decompressed legacy shard. The native runtime expects a different receipt: SHA256 of Newtonsoft-compatible JSON URL/raw-hash pairs sorted with .NET UTF-16 ordinal ordering. This command computes that receipt and creates revision-one bucket versions and initial commit receipts. Retain the original stage and verified byte archive for original shard provenance; they are not overwritten.

The native_imports master_sha256 remains the SHA256 of the decompressed original master JSON. It is distinct from the compressed master SHA256 in the shutdown checkpoint. The command does not conflate them.

The candidate records exact checksums for migrations 0001_catalog.sql, 0002_search_fts5.sql and 0004_catalog_commits.sql. It refuses sharded stages and previously upgraded runtime databases. Existing FTS content remains intact. A shared twelve-case fixture runs against both the Python encoder and actual Newtonsoft/.NET tests, including supplementary Unicode, UTF-16 ordering and JavaScript line separators.

## Remaining acceptance

The result explicitly reports PRIVATE_RUNTIME_STAGE_PREPARED_NOT_IMPORTED and production_ready: false. It contains private torrent/config-derived data and must not be uploaded as a public CI artifact.

Still required: safe verified archive extraction, full real-record DTO/API/golden validation, journalled import into a new owned empty Bunny Database, independent complete remote snapshot/FTS proof, resumable failure handling, guarded authority cutover, original config/Tracks/jobs restoration and public installer integration. The existing 22-record SQL pilot must be preserved.

## Actual candidate verification

[The real 20-record candidate](../docs/evidence/live-original-sql-candidate-2026-11-10.json) now passes complete operator import/readback into a new owned Bunny Database and native DTO/snapshot reads. The [public read-only developer harness](../tests/LiveLegacyImport/README.md) reproduces that slice. This does not complete resumable public migration, HTTP cutover or full restore.
