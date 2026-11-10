"""Offline migration preparation; no Bunny API or SQL cutover acceptance."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from bunny_native_import import stage, Refused
from bunny_native_runtime_stage import prepare, bucket_receipt
from test_bunny_native_import import make_source, gz


class RuntimeStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data, _, records, shard = make_source(self.root)
        for url, value in records.items(): value['url'] = url
        gz(shard, records)
        self.source = self.root/'stage.db'
        stage(data, self.source)
        self.output = self.root/'runtime.db'

    def test_portable_explicit_migrations_need_no_repository_layout(self):
        import shutil
        import bunny_native_import as importer
        import bunny_native_runtime_stage as runtime
        payload=self.root/'verified-payload';payload.mkdir()
        for name in runtime.MIGRATIONS:shutil.copyfile(ROOT/'bunny-native/migrations'/name,payload/name)
        data,_,records,shard=make_source(self.root/'portable-source')
        for url,value in records.items():value['url']=url
        gz(shard,records)
        with patch.object(importer,'SCHEMA',self.root/'missing/schema'),patch.object(importer,'FTS',self.root/'missing/fts'),patch.object(runtime,'ROOT',self.root/'missing-repository'):
            original=self.root/'portable-original.sqlite'
            stage(data,original,migrations_dir=payload)
            result=prepare(original,self.root/'portable-runtime.sqlite',migrations_dir=payload)
        self.assertEqual(result['counts']['torrents'],len(records))
        with closing(sqlite3.connect(self.root/'portable-runtime.sqlite')) as con:
            receipts=dict(con.execute('SELECT name,sha256 FROM native_schema_migrations'))
        self.assertEqual(receipts,{name:hashlib.sha256((payload/name).read_bytes()).hexdigest() for name in runtime.MIGRATIONS})

    def test_preserves_unknown_raw_fields_metadata_provenance_and_fts(self):
        con = sqlite3.connect(self.source)
        url, raw = con.execute('SELECT source_url,raw_json FROM torrents LIMIT 1').fetchone()
        value = json.loads(raw)
        value['future_unknown'] = {'precise_integer': 9007199254740997, 'caption':'日本語'}
        raw = json.dumps(value, ensure_ascii=False)
        con.execute('UPDATE torrents SET raw_json=?,raw_sha256=? WHERE source_url=?',
                    (raw, hashlib.sha256(raw.encode()).hexdigest(), url))
        con.commit()
        before_rows = con.execute('SELECT * FROM torrents ORDER BY id').fetchall()
        before_meta = con.execute('SELECT bucket_key,key_md5,update_time,file_time,torrent_count FROM buckets ORDER BY bucket_key').fetchall()
        con.close()
        before = self.source.read_bytes()
        result = prepare(self.source, self.output)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(result['cloud_modified'])
        self.assertFalse(result['production_ready'])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(self.output)) as con:
            self.assertEqual(con.execute('SELECT * FROM torrents ORDER BY id').fetchall(), before_rows)
            self.assertEqual(con.execute('SELECT bucket_key,key_md5,update_time,file_time,torrent_count FROM buckets ORDER BY bucket_key').fetchall(), before_meta)
            self.assertEqual(con.execute('SELECT DISTINCT revision FROM catalog_bucket_versions').fetchall(), [(1,)])
            self.assertEqual(con.execute('SELECT count(*) FROM catalog_commits').fetchone()[0], len(before_meta))
            self.assertEqual(con.execute('SELECT count(*) FROM native_schema_migrations').fetchone()[0], 3)
            for key, digest, count in con.execute('SELECT bucket_key,shard_sha256,torrent_count FROM buckets'):
                pairs = [list(x) for x in con.execute('SELECT source_url,raw_sha256 FROM torrents WHERE bucket_key=?', (key,))]
                self.assertEqual(digest, bucket_receipt(pairs))
                self.assertEqual(len(pairs), count)
            self.assertTrue(con.execute("SELECT count(*) FROM torrent_fts WHERE torrent_fts MATCH 'Film'").fetchone()[0])

    def test_refuses_existing_or_dangling_output_without_overwrite(self):
        self.output.write_bytes(b'foreign')
        with self.assertRaises(Refused): prepare(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'foreign')
        self.output.unlink()
        self.output.symlink_to(self.root/'absent')
        with self.assertRaises(Refused): prepare(self.source, self.output)
        self.assertFalse((self.root/'absent').exists())

    def test_atomic_publication_refuses_concurrent_output_and_cleans_temporary(self):
        real_link = os.link
        def race(source, target):
            Path(target).write_bytes(b'concurrent-owner')
            return real_link(source, target)
        with patch('bunny_native_runtime_stage.os.link', side_effect=race):
            with self.assertRaises(Refused): prepare(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'concurrent-owner')
        self.assertFalse(list(self.root.glob('.jacred-runtime-stage-*')))

    def test_rejects_nonprivate_sidecar_extra_schema_and_double_upgrade(self):
        self.source.chmod(0o644)
        with self.assertRaises(Refused): prepare(self.source, self.output)
        self.source.chmod(0o600)
        sidecar = Path(str(self.source)+'-wal')
        sidecar.touch()
        with self.assertRaises(Refused): prepare(self.source, self.output)
        sidecar.unlink()
        prepare(self.source, self.output)
        with self.assertRaises(Refused): prepare(self.output, self.root/'second.db')
        with closing(sqlite3.connect(self.source)) as con:
            con.execute('CREATE TABLE unexpected(secret TEXT)')
            con.commit()
        with self.assertRaises(Refused): prepare(self.source, self.root/'extra.db')

    def test_missing_or_mismatched_runtime_url_refuses_without_output(self):
        for url in (None, 'https://foreign.example/'):
            with self.subTest(url=url):
                with closing(sqlite3.connect(self.source)) as con:
                    rowid, raw = con.execute('SELECT id,raw_json FROM torrents LIMIT 1').fetchone()
                    value = json.loads(raw)
                    value['url'] = url
                    raw = json.dumps(value)
                    con.execute('UPDATE torrents SET raw_json=?,raw_sha256=? WHERE id=?',
                        (raw, hashlib.sha256(raw.encode()).hexdigest(), rowid))
                    con.commit()
                with self.assertRaises(Refused): prepare(self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_receipts_match_independently_run_newtonsoft_ordinal_vectors(self):
        # Exact hashes captured from a real Newtonsoft.Json/StringComparer.Ordinal
        # process, including UTF-16 ordering that differs from SQLite UTF-8 order.
        vectors = json.loads((ROOT/'tests/JacRed.Tests/Fixtures/NativeBucketReceipts.json').read_text())
        chars, expected = vectors['values'], vectors['sha256']
        for n, digest in enumerate(expected, 1):
            with self.subTest(rows=n):
                self.assertEqual(bucket_receipt([[x,'a'*64] for x in reversed(chars[:n])]), digest)


if __name__ == '__main__':
    unittest.main()
