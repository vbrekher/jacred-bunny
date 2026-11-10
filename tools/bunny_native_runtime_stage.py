#!/usr/bin/env python3
"""Prepare a new private runtime-compatible SQL stage. No network or cutover."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata
import sys
import tempfile

from bunny_native_import import ROOT, SCHEMA, FTS, Refused, validate

MIGRATIONS = ('0001_catalog.sql', '0002_search_fts5.sql', '0004_catalog_commits.sql')


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def bucket_receipt(pairs):
    """Newtonsoft default JSON of UTF-16 ordinal URL/hash pairs, not SQLite order."""
    pairs = sorted(pairs, key=lambda pair: pair[0].encode('utf-16-be'))
    encoded = json.dumps(pairs, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for char in ('\u0085', '\u2028', '\u2029'):
        encoded = encoded.replace(char, '\\u%04x' % ord(char))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def schema(connection):
    return connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def prepare(database, output, *, migrations_dir=None):
    migrations = Path(migrations_dir) if migrations_dir is not None else ROOT/'bunny-native/migrations'
    # A portable installed CLI supplies its already checksum-verified payload.
    # Snapshot all three inputs once so DDL and receipts use identical bytes.
    migration_bytes = {name:(migrations/name).read_bytes() for name in MIGRATIONS}
    migration_text = {name:value.decode('utf-8') for name,value in migration_bytes.items()}
    database, output = Path(database), Path(output)
    if database.is_symlink() or output.is_symlink():
        raise Refused('Stage symlink refused')
    database = database.resolve()
    output = output.parent.resolve() / output.name
    info = database.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused('Input stage must be a private owned regular file')
    if info.st_size > 900_000_000:
        raise Refused('Stage exceeds single-database bound')
    if output == database or output.exists():
        raise Refused('Output must be a new destination')
    if any(Path(str(database) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise Refused('Use a closed immutable stage without sidecars')
    before = digest_file(database)
    source = None
    expected = None
    candidate = None
    temporary = None
    try:
        source = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        expected = sqlite3.connect(':memory:')
        expected.executescript(migration_text['0001_catalog.sql'] + '\n' + migration_text['0002_search_fts5.sql'])
        source.execute('BEGIN')
        if schema(source) != schema(expected):
            raise Refused('Input must have exactly the original unsharded stage schema')
        original = validate(source)
        for (key,) in source.execute('SELECT bucket_key FROM buckets'):
            if len(key.encode('utf-16-le')) > 4096 or any(unicodedata.category(c) == 'Cc' for c in key):
                raise Refused('Bucket key is outside native runtime bounds')
        for url, raw in source.execute('SELECT source_url,raw_json FROM torrents'):
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('url') != url:
                raise Refused('Native runtime requires matching source URLs in every raw row')
        provenance = source.execute('SELECT master_sha256,imported_at FROM native_imports WHERE id=1').fetchone()
        if not re.fullmatch('[0-9a-f]{64}', provenance[0]):
            raise Refused('Invalid source master provenance')
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.jacred-runtime-stage-', suffix='.db', dir=output.parent)
        os.fchmod(fd, 0o600)
        os.close(fd)
        temporary = Path(name)
        candidate = sqlite3.connect(temporary)
        source.backup(candidate)
        candidate.executescript(migration_text['0004_catalog_commits.sql'])
        candidate.execute('CREATE TABLE native_schema_migrations(name TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL)')
        stamp = datetime.now(timezone.utc).isoformat()
        for name in MIGRATIONS:
            sha = hashlib.sha256(migration_bytes[name]).hexdigest()
            candidate.execute('INSERT INTO native_schema_migrations VALUES(?,?,?)', (name, sha, stamp))
        for (key,) in candidate.execute('SELECT bucket_key FROM buckets ORDER BY bucket_key').fetchall():
            pairs = [list(row) for row in candidate.execute('SELECT source_url,raw_sha256 FROM torrents WHERE bucket_key=?', (key,))]
            receipt = bucket_receipt(pairs)
            commit = hashlib.sha256(json.dumps([provenance[0], key, receipt], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]
            candidate.execute('UPDATE buckets SET shard_sha256=? WHERE bucket_key=?', (receipt, key))
            candidate.execute('INSERT INTO catalog_bucket_versions VALUES(?,1)', (key,))
            candidate.execute('INSERT INTO catalog_commits(commit_id,bucket_key,content_sha256,revision,committed_at) VALUES(?,?,?,1,?)', (commit, key, receipt, stamp))
        candidate.commit()
        if validate(candidate) != original or candidate.execute('PRAGMA foreign_key_check').fetchall():
            raise Refused('Runtime stage reconciliation failed')
        # Raw JSON, hashes, source metadata and provenance are copied, never DTO-serialized.
        for table, order in [('torrents','id'), ('bucket_aliases','alias,bucket_key'),
                             ('track_file_inventory','relative_path'), ('native_imports','id')]:
            if source.execute('SELECT * FROM '+table+' ORDER BY '+order).fetchall() != candidate.execute('SELECT * FROM '+table+' ORDER BY '+order).fetchall():
                raise Refused('Original relational rows changed')
        if source.execute('SELECT bucket_key,key_md5,update_time,file_time,torrent_count FROM buckets ORDER BY bucket_key').fetchall() != candidate.execute('SELECT bucket_key,key_md5,update_time,file_time,torrent_count FROM buckets ORDER BY bucket_key').fetchall():
            raise Refused('Original bucket metadata changed')
        candidate.close()
        candidate = None
        source.rollback()
        after = database.stat()
        if (digest_file(database) != before or
            (after.st_dev,after.st_ino,after.st_mode,after.st_uid,after.st_size,after.st_mtime_ns) !=
            (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_size,info.st_mtime_ns) or
            any(Path(str(database) + suffix).exists() for suffix in ('-wal','-shm','-journal'))):
            raise Refused('Input stage changed during preparation')
        if temporary.stat().st_size > 900_000_000:
            raise Refused('Runtime stage exceeds single-database bound')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        result_sha = digest_file(temporary)
        # Atomic no-clobber publication, including concurrent files/dangling symlinks.
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise Refused('Output appeared during preparation') from None
        return {'status':'PRIVATE_RUNTIME_STAGE_PREPARED_NOT_IMPORTED', 'source_stage_sha256':before,
                'runtime_stage_sha256':result_sha, 'source_master_sha256':provenance[0],
                'counts':original, 'migration_names':list(MIGRATIONS),
                'raw_rows_preserved':True, 'cloud_modified':False, 'production_ready':False}
    finally:
        if source is not None:
            source.close()
        if expected is not None:
            expected.close()
        if candidate is not None:
            candidate.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.database, args.output), sort_keys=True))
        return 0
    except (Refused, OSError, sqlite3.Error, ValueError):
        print('RUNTIME_STAGE_REFUSED: private input details omitted', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
