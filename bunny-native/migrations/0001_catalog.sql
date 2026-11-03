-- JacRed Bunny Native v0.4.0: isolated SQLite/libSQL staging (never live FileDB).
CREATE TABLE IF NOT EXISTS native_imports (
 id INTEGER PRIMARY KEY CHECK(id=1), master_sha256 TEXT NOT NULL,
 bucket_count INTEGER NOT NULL, torrent_count INTEGER NOT NULL,
 track_file_count INTEGER NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buckets (
 bucket_key TEXT PRIMARY KEY, key_md5 TEXT NOT NULL UNIQUE,
 update_time TEXT, file_time INTEGER, shard_sha256 TEXT NOT NULL,
 torrent_count INTEGER NOT NULL CHECK(torrent_count>=0)
);
CREATE TABLE IF NOT EXISTS bucket_aliases (
 alias TEXT NOT NULL, bucket_key TEXT NOT NULL REFERENCES buckets(bucket_key),
 PRIMARY KEY(alias,bucket_key)
);
CREATE INDEX IF NOT EXISTS aliases_by_bucket ON bucket_aliases(bucket_key);
CREATE TABLE IF NOT EXISTS torrents (
 id INTEGER PRIMARY KEY, bucket_key TEXT NOT NULL REFERENCES buckets(bucket_key),
 source_url TEXT NOT NULL, tracker_name TEXT, title TEXT, name TEXT,
 original_name TEXT, magnet TEXT, update_time TEXT, raw_json TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, UNIQUE(bucket_key,source_url)
);
CREATE INDEX IF NOT EXISTS torrent_tracker ON torrents(tracker_name);
CREATE INDEX IF NOT EXISTS torrent_bucket ON torrents(bucket_key);
CREATE TABLE IF NOT EXISTS track_file_inventory (
 relative_path TEXT PRIMARY KEY, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL
);
