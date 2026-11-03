#!/usr/bin/env python3
"""Private JSONL transport for an already verified JacRed Bunny v0.4 SQL stage.

Reads the staged local SQLite only. Never uploads credentials or uses Bunny API.
Payload includes raw torrent data and may contain tracker passkeys: chmod 0600.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from bunny_native_import import check_file, Refused

def encode(obj):
    return (json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"))+"\n").encode("utf-8")

def export_stage(database, output):
    database=Path(database).resolve();output=Path(output)
    if output.is_symlink():
        raise Refused("Output conflicts with database or is a symlink")
    output=output.parent.resolve()/output.name
    if output == database or output.is_symlink():
        raise Refused("Output conflicts with database or is a symlink")
    if output.exists():
        raise Refused("Export target exists; choose another path")
    stats=check_file(database)
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".jacred-private-export-",suffix=".jsonl",dir=output.parent)
    os.fchmod(fd,0o600)
    counts={"bucket":0,"alias":0,"torrent":0,"track":0}
    try:
        conn=sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)
        try:
            with os.fdopen(fd,"wb") as stream:
                manifest=conn.execute("SELECT master_sha256 FROM native_imports WHERE id=1").fetchone()
                if not manifest or not isinstance(manifest[0],str):
                    raise Refused("Stage import provenance unavailable")
                header={"kind":"header","schema":"jacred-bunny-v0.4",
                        "master_sha256":manifest[0],
                        "bucket_count":stats["buckets"],"torrent_count":stats["torrents"],
                        "track_file_count":stats["track_files_inventory_only"]}
                # Optional v0.5.2 shard identity, included only if verified in SQL.
                shard_exists=conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='native_shard_manifest'").fetchone()[0]
                if shard_exists:
                    shard=conn.execute("SELECT shard_index,shard_count,master_sha256 FROM native_shard_manifest WHERE id=1").fetchone()
                    if shard is None or shard[2]!=manifest[0] or not 2<=shard[1]<=4 or not 0<=shard[0]<shard[1]:
                        raise Refused("Invalid shard source identity")
                    header.update({"shard_index":shard[0],"shard_count":shard[1]})
                stream.write(encode(header))
                specs=[
                    ("bucket","SELECT bucket_key,key_md5,update_time,file_time,shard_sha256,torrent_count FROM buckets ORDER BY bucket_key",
                     ("bucket_key","key_md5","update_time","file_time","shard_sha256","torrent_count")),
                    ("alias","SELECT alias,bucket_key FROM bucket_aliases ORDER BY bucket_key,alias",
                     ("alias","bucket_key")),
                    ("torrent","SELECT bucket_key,source_url,tracker_name,title,name,original_name,magnet,update_time,raw_json,raw_sha256 FROM torrents ORDER BY bucket_key,source_url",
                     ("bucket_key","source_url","tracker_name","title","name","original_name","magnet","update_time","raw_json","raw_sha256")),
                    ("track","SELECT relative_path,bytes,sha256 FROM track_file_inventory ORDER BY relative_path",
                     ("relative_path","bytes","sha256"))
                ]
                for kind,sql,keys in specs:
                    for record in conn.execute(sql):
                        fields=dict(zip(keys,record))
                        # .NET FileTime is > 2^53; JSON decimal string avoids JS rounding.
                        if kind=="bucket" and fields["file_time"] is not None:
                            fields["file_time"]=str(fields["file_time"])
                        stream.write(encode({"kind":kind,**fields}))
                        counts[kind]+=1
                stream.write(encode({"kind":"end","counts":counts}))
                stream.flush();os.fsync(stream.fileno())
        finally:conn.close()
        if counts["bucket"]!=stats["buckets"] or counts["torrent"]!=stats["torrents"] or counts["track"]!=stats["track_files_inventory_only"]:
            raise Refused("Export count mismatch")
        digest=hashlib.sha256()
        with open(tmp,"rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        # Unlike replace(), link() cannot clobber a concurrently created file
        # or symlink. mkstemp used the destination filesystem and mode 0600.
        try:os.link(tmp,output)
        except FileExistsError:raise Refused("Export target exists; choose another path") from None
        return {"status":"PRIVATE_STAGED_EXPORT_NOT_UPLOADED","sha256":digest.hexdigest(),
                "counts":counts,"bytes":output.stat().st_size,"permissions":"0600"}
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    try:
        print(json.dumps(export_stage(args.database,args.output),sort_keys=True))
        return 0
    except (Refused,OSError,sqlite3.Error,ValueError) as err:
        print("PRIVATE_EXPORT_REFUSED: "+type(err).__name__,file=sys.stderr)
        return 2
if __name__=="__main__":sys.exit(main())
