#!/usr/bin/env python3
"""JacRed v0.5.2: deterministic, private SQLite shard-set from VERIFIED v0.4 stage.

Reads one immutable local SQLite stage; does not connect to Bunny or touch FileDB.
Atomically publishes a new directory containing 2..4 independent SQL databases.
Data/tracks bytes remain OUTSIDE all SQL databases (inventory only).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SCHEMA=ROOT/"bunny-native/migrations/0001_catalog.sql"
FTS=ROOT/"bunny-native/migrations/0002_search_fts5.sql"
SHARD_SCHEMA=ROOT/"bunny-native/migrations/0003_shard_identity.sql"

from bunny_native_import import verify_stage

class SplitRefused(Exception):pass

def index_for(md5,shard_count):
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in "0123456789abcdef" for c in md5):
        raise SplitRefused("Invalid canonical bucket MD5")
    return int(md5,16)%shard_count

def index_for_track(path,shard_count):
    return int(hashlib.sha256(path.encode("utf-8")).hexdigest(),16)%shard_count

def db_open(path):
    con=sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA.read_text("utf-8"))
    con.executescript(FTS.read_text("utf-8"))
    con.executescript(SHARD_SCHEMA.read_text("utf-8"))
    return con

def split(source,out,shard_count):
    if not isinstance(shard_count,int) or not 2<=shard_count<=4:
        raise SplitRefused("Only 2..4 shards supported by Edge preview")
    source=Path(source).resolve()
    out=Path(out).absolute()
    if out.exists() or out.is_symlink():
        raise SplitRefused("Output must be a new directory")
    if out==source or source.parent==out or source in out.parents:
        raise SplitRefused("Output must not contain source SQLite stage")
    if not source.is_file() or source.is_symlink():
        raise SplitRefused("Source is not a regular SQLite stage file")
    if source.stat().st_size>950_000_000:
        raise SplitRefused("Staging source exceeds conservative size guard")
    out.parent.mkdir(parents=True,exist_ok=True)
    source_stat=(source.stat().st_size,source.stat().st_mtime_ns)
    uri="file:"+source.as_posix()+"?mode=ro"
    con=sqlite3.connect(uri,uri=True)
    con.execute("PRAGMA query_only=ON")
    stage_dir=Path(tempfile.mkdtemp(dir=out.parent,prefix=".jacred-split-"))
    os.chmod(stage_dir,0o700)
    sinks=[]
    try:
        con.execute("BEGIN")
        origin=verify_stage(con)
        row=con.execute("SELECT master_sha256 FROM native_imports WHERE id=1").fetchone()
        if row is None or not isinstance(row[0],str) or len(row[0])!=64:
            raise SplitRefused("Source missing original master checksum")
        original_sha=row[0]
        for idx in range(shard_count):
            path=stage_dir/("shard-"+str(idx)+".sqlite")
            db=db_open(path)
            os.chmod(path,0o600)
            sinks.append(db)
        bucket_cols="bucket_key,key_md5,update_time,file_time,shard_sha256,torrent_count"
        torrent_cols=("bucket_key,source_url,tracker_name,title,name,original_name,"
                      "magnet,update_time,raw_json,raw_sha256")
        for bucket in con.execute("SELECT "+bucket_cols+" FROM buckets ORDER BY bucket_key"):
            bucket_key,md5=bucket[:2]
            target=sinks[index_for(md5,shard_count)]
            with target:
                target.execute("INSERT INTO buckets("+bucket_cols+") VALUES(?,?,?,?,?,?)",bucket)
                for alias in con.execute("SELECT alias,bucket_key FROM bucket_aliases WHERE bucket_key=? ORDER BY alias",(bucket_key,)):
                    target.execute("INSERT INTO bucket_aliases(alias,bucket_key) VALUES(?,?)",alias)
                for row in con.execute("SELECT "+torrent_cols+" FROM torrents WHERE bucket_key=? ORDER BY source_url",(bucket_key,)):
                    target.execute("INSERT INTO torrents("+torrent_cols+") VALUES(?,?,?,?,?,?,?,?,?,?)",row)
        for record in con.execute("SELECT relative_path,bytes,sha256 FROM track_file_inventory ORDER BY relative_path"):
            target=sinks[index_for_track(record[0],shard_count)]
            with target:
                target.execute("INSERT INTO track_file_inventory VALUES(?,?,?)",record)
        result=[]
        stamp=str(int(time.time()))
        for idx,target in enumerate(sinks):
            with target:
                buckets=target.execute("SELECT COUNT(*) FROM buckets").fetchone()[0]
                torrents=target.execute("SELECT COUNT(*) FROM torrents").fetchone()[0]
                tracks=target.execute("SELECT COUNT(*) FROM track_file_inventory").fetchone()[0]
                target.execute("INSERT INTO native_shard_manifest VALUES(1,?,?,?,?)",
                               (idx,shard_count,original_sha,stamp))
                target.execute("INSERT INTO native_imports VALUES(1,?,?,?,?,?)",
                               (original_sha,buckets,torrents,tracks,stamp))
            verify_stage(target)
            result.append({"index":idx,"buckets":buckets,"torrents":torrents,"tracks":tracks})
        if sum(x["buckets"] for x in result)!=origin["buckets"] or \
           sum(x["torrents"] for x in result)!=origin["torrents"] or \
           sum(x["tracks"] for x in result)!=origin["track_file_manifest_only"]:
            raise SplitRefused("Global source/shard reconciliation failed")
        if (source.stat().st_size,source.stat().st_mtime_ns)!=source_stat:
            raise SplitRefused("Source stage modified during split")
        for sink in sinks:sink.close()
        sinks=[]
        if out.exists():raise SplitRefused("Output appeared during validation")
        os.replace(stage_dir,out)
        return {"status":"PRIVATE_LOCAL_SHARD_SET_PASS_NOT_BUNNY",
                "source_sha256":original_sha,"shards":result,
                "ready_for_production":False,
                "shard_storage":"TRACK_INVENTORY_ONLY"}
    finally:
        for sink in sinks:sink.close()
        con.close()
        if stage_dir.exists():shutil.rmtree(stage_dir)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--shards",type=int,required=True)
    opts=p.parse_args()
    try:
        print(json.dumps(split(opts.database,opts.output_dir,opts.shards),sort_keys=True))
    except (SplitRefused,sqlite3.Error,OSError,ValueError) as error:
        # Never print database rows, magnet values, source filenames or tokens.
        print("JACRED_SQL_SHARD_SPLIT_REFUSED: "+type(error).__name__,file=sys.stderr)
        return 2
    return 0
if __name__=="__main__":sys.exit(main())
