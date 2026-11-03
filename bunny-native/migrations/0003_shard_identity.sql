-- JacRed v0.5.2 optional sharded staging identity.
-- Every shard must share the same original masterDb SHA256 and total shard_count.
-- Create only on actual sharded catalog databases. Never alter live .NET FileDB.
CREATE TABLE IF NOT EXISTS native_shard_manifest(
 id INTEGER PRIMARY KEY CHECK(id=1),
 shard_index INTEGER NOT NULL CHECK(shard_index>=0 AND shard_index<4),
 shard_count INTEGER NOT NULL CHECK(shard_count>=2 AND shard_count<=4),
 master_sha256 TEXT NOT NULL CHECK(length(master_sha256)=64),
 created_at TEXT NOT NULL
);
