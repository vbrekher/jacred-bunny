"""JacRed Bunny 0.4.0 isolated GZip FileDB -> SQL staging regressions."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE=Path(__file__).resolve().parents[1]/"tools/bunny_native_import.py"
spec=importlib.util.spec_from_file_location("jacred_native_import",MODULE)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def gz(path, obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as out:
        json.dump(obj,out,ensure_ascii=False)

def make_source(base,with_tracks=True):
    src=base/"Data"
    bucket="фильм название:original film"
    hashed=hashlib.md5(bucket.encode("utf-8")).hexdigest()
    source=src/"fdb"/hashed[:2]/hashed[2:]
    original={
      "https://tracker.example/t/1":{"trackerName":"rutor","url":"https://tracker.example/t/1",
      "title":"Фильм 1080p","name":"Фильм","originalname":"Original Film","magnet":"magnet:?xt=urn:btih:FAKE",
      "voices":["Дубляж"],"languages":["rus","ukr"],"quality":1080,"ffprobe":[{"codec_name":"aac"}],
      "seasons":[1],"sid":100,"pir":35,"someUnknownField":{"nested": [1,2,3]}},
      "https://tracker.example/t/2":{"trackerName":"kinozal","title":"Second Film","name":"Second Film",
       "updateTime":"2026-01-02T08:00:00Z"}
    }
    gz(src/"masterDb.bz",{bucket:{"updateTime":"2026-01-02T08:00:00Z","fileTime":123}})
    gz(source,original)
    (src/"fdb"/".gitignore").write_text("!/.gitignore\n")
    if with_tracks:
        (src/"tracks").mkdir()
        (src/"tracks"/"test-track.bin").write_bytes(b"\x00\x01ffprobe")
    return src,bucket,original,source

class Importer(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            src,bucket,original,source=make_source(base)
            output=base/"out"/"stage.sqlite"
            result=a.stage(src,output)
            self.assertEqual(result["torrents"],2)
            self.assertEqual(result["buckets"],1)
            self.assertEqual(result["track_files_inventory_only"],1)
            self.assertFalse(result["stage_is_production_ready"])
            self.assertEqual(a.check_file(output)["torrents"],2)
            db=sqlite3.connect(output)
            row=db.execute("SELECT raw_json,raw_sha256,magnet FROM torrents WHERE source_url LIKE '%/1'").fetchone()
            self.assertEqual(json.loads(row[0]),original["https://tracker.example/t/1"])
            self.assertEqual(a.sha(row[0].encode()),row[1])
            self.assertTrue(row[2].startswith("magnet:"))
            aliases={x[0] for x in db.execute("SELECT alias FROM bucket_aliases")}
            self.assertEqual(aliases,set(bucket.split(":")))
            self.assertEqual(db.execute("SELECT count(*) FROM track_file_inventory").fetchone()[0],1)
            db.close()
            self.assertTrue(source.exists())
            self.assertTrue((src/"masterDb.bz").exists())

    def test_plan_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            src,*_=make_source(Path(td))
            plan=a.discover(src)
            self.assertEqual(len(plan["specs"]),1)
            self.assertGreater(plan["source_bytes"],0)
            self.assertEqual(list(Path(td).rglob("*.sqlite")),[])

    def test_missing_master_refused(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/"Data";(data/"fdb").mkdir(parents=True)
            (data/"fdb"/".gitignore").write_text("*")
            with self.assertRaises(a.Refused):a.discover(data)

    def test_orphan_refused(self):
        with tempfile.TemporaryDirectory() as td:
            data,*_=make_source(Path(td))
            orphan=data/"fdb"/"aa"/("c"*30)
            orphan.parent.mkdir(parents=True,exist_ok=True)
            orphan.write_bytes(b"x")
            with self.assertRaisesRegex(a.Refused,"Orphan"):a.discover(data)

    def test_invalid_gzip_does_not_publish(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            src,_,_,shard=make_source(base)
            shard.write_bytes(b"not a valid GZip")
            target=base/"stage.sqlite"
            with self.assertRaises((OSError,a.Refused)):a.stage(src,target)
            self.assertFalse(target.exists())
            self.assertFalse(any(base.glob(".jacred-import-*")))

    def test_bounded_decompression_refused(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            src,*_=make_source(base)
            with self.assertRaises(a.Refused):a.stage(src,base/"out.db",cap=25)
            self.assertFalse((base/"out.db").exists())

    def test_existing_database_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            target=base/"out.sqlite";target.write_bytes(b"DO NOT ERASE")
            with self.assertRaises(a.Refused):a.stage(src,target)
            self.assertEqual(target.read_bytes(),b"DO NOT ERASE")

    def test_replace_is_explicit_and_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            target=base/"out.sqlite";target.write_bytes(b"old")
            result=a.stage(src,target,replace=True)
            self.assertEqual(result["torrents"],2)
            self.assertEqual(a.check_file(target)["buckets"],1)

    def test_both_hash_layouts(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,bucket,_,shard=make_source(base)
            digest=hashlib.md5(bucket.encode()).hexdigest()
            old=src/"fdb"/digest[0]/digest
            old.parent.mkdir(parents=True,exist_ok=True)
            shard.rename(old)
            result=a.stage(src,base/"out.sqlite")
            self.assertEqual(result["torrents"],2)

    def test_duplicate_hash_layout_refused(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,bucket,_,shard=make_source(base)
            digest=hashlib.md5(bucket.encode()).hexdigest()
            old=src/"fdb"/digest[0]/digest
            old.parent.mkdir(parents=True,exist_ok=True)
            old.write_bytes(shard.read_bytes())
            with self.assertRaisesRegex(a.Refused,"Duplicate"):a.discover(src)

    def test_corrupt_source_does_not_disclose_fields(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            (src/"fdb"/".gitignore").write_text("private")
            self.assertEqual(len(a.discover(src)["specs"]),1)

    def test_search_from_staged_index(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            path=base/"out.sqlite";a.stage(src,path)
            rows=a.query_file(path,"Second",limit=10)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["tracker"],"kinozal")

    def test_tampered_payload_fails_verification(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            path=base/"out.sqlite";a.stage(src,path)
            db=sqlite3.connect(path)
            with db:db.execute("UPDATE torrents SET raw_json='{}' WHERE id=1")
            db.close()
            with self.assertRaisesRegex(a.Refused,"checksum"):a.check_file(path)

    def test_tracks_are_manifest_only_not_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            path=base/"out.sqlite";a.stage(src,path)
            db=sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT sha256 FROM track_file_inventory").fetchone()[0],
                             hashlib.sha256(b"\x00\x01ffprobe").hexdigest())
            db.close()

    def test_invalid_track_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);src,*_=make_source(base)
            (src/"tracks"/"evil").symlink_to(base/"secret")
            with self.assertRaisesRegex(a.Refused,"symlink"):a.discover(src)

if __name__=="__main__":unittest.main()
