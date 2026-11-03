"""Archive integrity checks; these fixtures are not production acceptance."""
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
from native import NativeError
from backup import export_snapshot,verify_archive,sha_file,Storage


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'archive.gz'
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE native_schema_migrations(name TEXT,sha256 TEXT);')
        for name in ('0001_catalog.sql','0002_search_fts5.sql'):
            self.db.executescript((ROOT/'bunny-native/migrations'/name).read_text())
        self.query=lambda statement,args=():[dict(row) for row in self.db.execute(statement,args).fetchall()]
        self.sql=type('Reader',(),{'query':staticmethod(self.query)})()

    def test_empty_catalog_archive_roundtrip_is_explicitly_partial(self):
        summary=export_snapshot(self.sql,self.path,{'fixture':True})
        checked=verify_archive(self.path,summary['gzip_sha256'])
        self.assertEqual(checked['counts'],summary['counts'])
        self.assertEqual(checked['content_sha256'],summary['content_sha256'])
        self.assertFalse(checked['header']['coverage']['legacy_filedb'])
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_nonempty_rows_keep_unicode_blob_and_exact_int64(self):
        self.db.execute("INSERT INTO buckets VALUES(?,?,?,?,?,?)",('fixture','f'*32,None,2**63-1,'a'*64,1))
        self.db.execute("INSERT INTO bucket_aliases VALUES(?,?)",('Ёж 雪','fixture'))
        self.db.execute("INSERT INTO torrents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (2**63-1,'fixture','https://example.invalid/fixture','synthetic','JacRed Ёж',None,None,None,None,'{}','b'*64))
        result=export_snapshot(self.sql,self.path,{})
        checked=verify_archive(self.path,result['gzip_sha256'])
        self.assertEqual(checked['counts']['torrents'],1)
        rows=[json.loads(x) for x in gzip.decompress(self.path.read_bytes()).splitlines()]
        torrent=next(x for x in rows if x.get('table')=='torrents')
        self.assertEqual(torrent['values'][0],{'type':'integer','value':str(2**63-1)})
        self.assertIn('Ёж',gzip.decompress(self.path.read_bytes()).decode())

    def test_corrupt_download_and_incomplete_archive_rejected(self):
        result=export_snapshot(self.sql,self.path,{})
        with self.assertRaises(NativeError):verify_archive(self.path,'0'*64)
        records=gzip.decompress(self.path.read_bytes()).splitlines(keepends=True)
        self.path.write_bytes(gzip.compress(b''.join(records[:-1])))
        with self.assertRaisesRegex(NativeError,'Incomplete'):verify_archive(self.path,sha_file(self.path))

    def test_valid_compression_cannot_hide_changed_content(self):
        export_snapshot(self.sql,self.path,{})
        records=gzip.decompress(self.path.read_bytes()).splitlines(keepends=True)
        header=json.loads(records[0]);header['metadata']={'tampered':True}
        records[0]=(json.dumps(header)+'\n').encode()
        self.path.write_bytes(gzip.compress(b''.join(records)))
        with self.assertRaisesRegex(NativeError,'footer'):verify_archive(self.path,sha_file(self.path))

    def test_public_storage_and_foreign_hosts_rejected(self):
        with self.assertRaises(NativeError):Storage({'PullZones':[{'Id':1}]})
        with self.assertRaises(NativeError):Storage({'StorageHostname':'storage.bunnycdn.com.attacker.invalid'})
