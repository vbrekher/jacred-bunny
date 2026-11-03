-- Optimistic bucket writes: durable versions survive empty-bucket deletion.
-- A stale read must fail the whole transaction, never replace a newer bucket.
CREATE TABLE IF NOT EXISTS catalog_bucket_versions (
 bucket_key TEXT PRIMARY KEY,
 revision INTEGER NOT NULL CHECK(revision>=1)
);
CREATE TABLE IF NOT EXISTS catalog_write_guards (
 commit_id TEXT PRIMARY KEY,
 valid INTEGER NOT NULL CHECK(valid=1)
);
CREATE TABLE IF NOT EXISTS catalog_commits (
 commit_id TEXT PRIMARY KEY,
 bucket_key TEXT NOT NULL,
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 revision INTEGER NOT NULL CHECK(revision>=1),
 committed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS catalog_commits_by_bucket ON catalog_commits(bucket_key,revision);
