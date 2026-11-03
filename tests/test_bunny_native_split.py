"""Offline deterministic SQLite shard split, privacy and import contracts."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/"tools"))
from bunny_native_import import stage,verify_stage
from bunny_native_split import split,SplitRefused,index_for
from bunny_native_export import export_stage

def put(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as stream:
        json.dump(obj,stream,ensure_ascii=False)
def sample(base):
    root=base/"data"
    master={}
    for idx,key in enumerate(["кино:film","сериал:series","аниме:anime","док:documentary","дубль:duplicate"]):
        hashkey=hashlib.md5(key.encode()).hexdigest()
        master[key]={"updateTime":"2026-01-01T00:00:00Z","fileTime":133123456789012345}
        put(root/"fdb"/hashkey[:2]/hashkey[2:],{
            "https://test.invalid/"+str(idx):{
               "trackerName":"rutor","title":"Title "+str(idx),
               "types":["movie"],"sid":idx,"url":"https://test.invalid/"+str(idx),
               "magnet":"magnet:?xt=urn:btih:"+"a"*40,
               "updateTime":"2026-01-01T00:00:00Z"
            }})
    put(root/"masterDb.bz",master)
    track=root/"tracks";track.mkdir();(track/"one").write_bytes(b"private-track-1")
    (track/"two").write_bytes(b"private-track-2")
    stagefile=base/"source.sqlite"
    outcome=stage(root,stagefile)
    assert outcome["torrents"]==5
    return stagefile

class SplitTests(unittest.TestCase):
    def test_shard_set_reconciles_real_sqlite_and_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            source=sample(base)
            outcome=split(source,base/"bundle",3)
            self.assertEqual(outcome["status"],"PRIVATE_LOCAL_SHARD_SET_PASS_NOT_BUNNY")
            self.assertEqual(sum(s["torrents"] for s in outcome["shards"]),5)
            self.assertEqual(sum(s["buckets"] for s in outcome["shards"]),5)
            self.assertEqual(sum(s["tracks"] for s in outcome["shards"]),2)
            self.assertFalse(outcome["ready_for_production"])
            self.assertEqual((base/"bundle").stat().st_mode & 0o777,0o700)
            for i in range(3):
                p=base/"bundle"/f"shard-{i}.sqlite"
                self.assertEqual(p.stat().st_mode & 0o777,0o600)
                conn=sqlite3.connect(p)
                self.assertEqual(verify_stage(conn)["torrents"],outcome["shards"][i]["torrents"])
                self.assertEqual(conn.execute("SELECT shard_index FROM native_shard_manifest").fetchone()[0],i)
                conn.close()

    def test_bucket_partition_is_exact_once_without_duplicate_records(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            source=sample(base)
            split(source,base/"set",2)
            entries=[]
            for i in range(2):
                conn=sqlite3.connect(base/"set"/f"shard-{i}.sqlite")
                vals=conn.execute("SELECT key_md5 FROM buckets").fetchall()
                for (value,) in vals:
                    self.assertEqual(index_for(value,2),i)
                    entries.append(value)
                conn.close()
            self.assertEqual(len(entries),len(set(entries)))
            self.assertEqual(len(entries),5)

    def test_source_stage_never_modified(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            split(source,base/"bundle",2)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

    def test_existing_target_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            out=base/"existing";out.mkdir();(out/"keep").write_text("private")
            with self.assertRaises(SplitRefused):split(source,out,2)
            self.assertEqual((out/"keep").read_text(),"private")

    def test_invalid_shard_count_refused(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            for count in (0,1,5,10):
                with self.assertRaises(SplitRefused):
                    split(source,base/"bad",count)
            self.assertFalse((base/"bad").exists())

    def test_shard_jsonl_header_contains_stable_source_identity(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            split(source,base/"parts",2)
            master=[]
            for idx in range(2):
                bundle=base/f"private-{idx}.jsonl"
                export_stage(base/"parts"/f"shard-{idx}.sqlite",bundle)
                self.assertEqual(bundle.stat().st_mode & 0o777,0o600)
                header=json.loads(bundle.open().readline())
                self.assertEqual(header["shard_index"],idx)
                self.assertEqual(header["shard_count"],2)
                master.append(header["master_sha256"])
            self.assertEqual(len(set(master)),1)

    def test_null_shards_keep_original_stage_jsonl_backward_compat(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            output=base/"original-private.jsonl"
            export_stage(source,output)
            header=json.loads(output.open().readline())
            self.assertNotIn("shard_index",header)
            self.assertNotIn("shard_count",header)

    def test_track_payload_bytes_are_not_embedded_in_sql_stages(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=sample(base)
            split(source,base/"parts",2)
            payload=b"private-track-1"
            for idx in range(2):
                sql=base/"parts"/f"shard-{idx}.sqlite"
                self.assertNotIn(payload,sql.read_bytes())
            self.assertTrue((base/"data"/"tracks"/"one").exists())

if __name__=="__main__":unittest.main()
