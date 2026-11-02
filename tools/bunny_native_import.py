#!/usr/bin/env python3
"""JacRed Bunny v0.4: read-only FileDB -> atomic local SQLite staging importer.

No network, no Bunny credentials, no production changes. Output is a private
candidate database; transfer and live cutover require independent acceptance.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=ROOT/"bunny-native/migrations/0001_catalog.sql"
FTS=ROOT/"bunny-native/migrations/0002_search_fts5.sql"
LIMIT=128*1024*1024

class Refused(Exception): pass

def sha(data): return hashlib.sha256(data).hexdigest()
def canon(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def signature(path):
    st=path.stat()
    return st.st_size,st.st_mtime_ns

def decompress(path,cap):
    before=signature(path)
    if path.is_symlink() or not path.is_file(): raise Refused("Non-regular input")
    with gzip.open(path,"rb") as stream:
        result=stream.read(cap+1)
    if len(result)>cap: raise Refused("Decompressed shard exceeds byte limit")
    if signature(path)!=before: raise Refused("Source mutated during reading")
    return result

def find_master(root):
    direct=root/"masterDb.bz"
    if direct.is_file() and not direct.is_symlink():return direct
    # Old dated snapshots sorted by actual date rather than lexicographic dd-mm-yyyy.
    import datetime
    candidates=[]
    for p in root.glob("masterDb_*.bz"):
        if p.is_symlink():raise Refused("Source symlink refused")
        try:dt=datetime.datetime.strptime(p.stem.removeprefix("masterDb_"),"%d-%m-%Y")
        except ValueError:continue
        if p.is_file():candidates.append((dt,p))
    if not candidates:raise Refused("Missing masterDb.bz: no dataset to import")
    return max(candidates,key=lambda x:x[0])[1]

def discover(root,cap=LIMIT):
    root=root.resolve()
    master=find_master(root)
    try: master_obj=json.loads(decompress(master,cap))
    except (ValueError,UnicodeError,OSError) as e:raise Refused("Invalid master JSON/GZip") from e
    if not isinstance(master_obj,dict) or not master_obj:raise Refused("Master index empty")
    base=root/"fdb"
    if not base.is_dir():raise Refused("Data/fdb directory missing")
    found={}
    for p in base.rglob("*"):
        if p.is_symlink():raise Refused("Symlink in FileDB")
        if not p.is_file():continue
        rel=p.relative_to(base).parts
        if len(rel)==2 and re.fullmatch("[0-9a-f]{2}",rel[0]) and re.fullmatch("[0-9a-f]{30}",rel[1]):
            key=rel[0]+rel[1]
        elif len(rel)==2 and re.fullmatch("[0-9a-f]",rel[0]) and re.fullmatch("[0-9a-f]{32}",rel[1]):
            key=rel[1]
            if not key.startswith(rel[0]):raise Refused("Invalid legacy shard prefix")
        elif p.name in (".gitignore",".gitkeep","README.md"):continue
        else:raise Refused("Unexpected FileDB path")
        if key in found:raise Refused("Duplicate legacy shard layouts")
        found[key]=p
    specs=[]
    for bucket,meta in sorted(master_obj.items()):
        if not isinstance(bucket,str) or not bucket:raise Refused("Invalid master bucket key")
        key=hashlib.md5(bucket.encode("utf-8")).hexdigest()
        p=found.get(key)
        if p is None:raise Refused("Master references a missing shard")
        if meta is not None and not isinstance(meta,(dict,str)):raise Refused("Unknown master metadata")
        specs.append((bucket,key,p,meta))
    if set(found)!={spec[1] for spec in specs}:raise Refused("Orphan FileDB shard detected")
    trackbase=root/"tracks"
    tracks=[]
    if trackbase.is_dir():
        for p in sorted(trackbase.rglob("*")):
            if p.is_symlink():raise Refused("Track symlink detected")
            if p.is_file() and p.name not in (".gitignore",".gitkeep","README.md"):
                tracks.append((p.relative_to(trackbase).as_posix(),p))
    paths=[master]+[x[2] for x in specs]+[p for _,p in tracks]
    stats={p:signature(p) for p in paths}
    return {"master":master,"specs":specs,"tracks":tracks,"stats":stats,
            "source_bytes":sum(st[0] for st in stats.values())}

def check_source(root,plan,cap):
    if discover(root,cap)["stats"]!=plan["stats"]:
        raise Refused("Source changed during import; use a stable snapshot")

def init_database(path):
    con=sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA.read_text("utf-8"))
    con.executescript(FTS.read_text("utf-8"))
    return con

def validate(con):
    row=con.execute("SELECT bucket_count,torrent_count,track_file_count FROM native_imports").fetchone()
    if row is None:raise Refused("No successful import metadata")
    buckets,torrents,tracks=row
    values=[con.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
            for table in ("buckets","torrents","track_file_inventory")]
    if list(row)!=values:raise Refused("Row-count reconciliation failed")
    for key,count in con.execute("SELECT bucket_key,torrent_count FROM buckets"):
        if con.execute("SELECT count(*) FROM torrents WHERE bucket_key=?",(key,)).fetchone()[0]!=count:
            raise Refused("Bucket-count mismatch")
    for body,d in con.execute("SELECT raw_json,raw_sha256 FROM torrents"):
        if sha(body.encode("utf-8"))!=d:raise Refused("Record checksum mismatch")
    if con.execute("SELECT COUNT(*) FROM torrent_fts").fetchone()[0]!=torrents:
        raise Refused("FTS search index incomplete")
    if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
        raise Refused("SQLite integrity violation")
    return {"buckets":buckets,"torrents":torrents,
            "track_files_inventory_only":tracks,"stage_is_production_ready":False}

def stage(data_root,out,cap=LIMIT,replace=False):
    data_root=data_root.resolve()
    out=out.resolve()
    if out==data_root or data_root in out.parents:raise Refused("Output within source Data")
    if out.is_symlink():raise Refused("Output symlink refused")
    if out.exists() and not replace:raise Refused("Target exists; explicit --replace required")
    plan=discover(data_root,cap)
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,p=tempfile.mkstemp(dir=out.parent,prefix=".jacred-import-",suffix=".db")
    os.close(fd)
    temp=Path(p);con=None
    try:
        con=init_database(temp)
        total=0
        for bucket,md5,path,meta in plan["specs"]:
            try:records=json.loads(decompress(path,cap))
            except (ValueError,UnicodeError,OSError) as e:raise Refused("Invalid shard GZip/JSON") from e
            if not isinstance(records,dict):raise Refused("Non-dictionary FileDB shard")
            timevalue=meta.get("updateTime") if isinstance(meta,dict) else meta
            filetime=meta.get("fileTime") if isinstance(meta,dict) else None
            with con:
                con.execute("INSERT INTO buckets VALUES (?,?,?,?,?,?)",
                            (bucket,md5,str(timevalue) if timevalue is not None else None,
                             filetime,sha(decompress(path,cap)),len(records)))
                for alias in sorted(set(bucket.split(":"))):
                    if alias:con.execute("INSERT INTO bucket_aliases VALUES (?,?)",(alias,bucket))
                for url,obj in sorted(records.items()):
                    if not isinstance(url,str) or not isinstance(obj,dict):
                        raise Refused("Invalid torrent record")
                    payload=canon(obj)
                    con.execute("""INSERT INTO torrents(bucket_key,source_url,tracker_name,title,name,
                      original_name,magnet,update_time,raw_json,raw_sha256)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (bucket,url,obj.get("trackerName"),obj.get("title"),obj.get("name"),
                       obj.get("originalname"),obj.get("magnet"),obj.get("updateTime"),
                       payload,sha(payload.encode("utf-8"))))
                    total+=1
        with con:
            for relative,path in plan["tracks"]:
                h=hashlib.sha256()
                with path.open("rb") as inp:
                    for chunk in iter(lambda:inp.read(1024*1024),b""):h.update(chunk)
                con.execute("INSERT INTO track_file_inventory VALUES (?,?,?)",
                            (relative,path.stat().st_size,h.hexdigest()))
            con.execute("INSERT INTO native_imports VALUES(1,?,?,?,?,?,?)",
                        (sha(decompress(plan["master"],cap)),len(plan["specs"]),total,
                         len(plan["tracks"]),str(int(time.time()))))
        outcome=validate(con)
        check_source(data_root,plan,cap)
        con.close();con=None
        # Only after every check is the stage published. Caller is responsible for
        # not concurrently editing an existing explicit --replace target.
        os.replace(temp,out)
        return {"status":"LOCAL_SQLITE_STAGE_PASS_NOT_BUNNY",**outcome,
                "source_bytes":plan["source_bytes"],"sql_bytes":out.stat().st_size}
    finally:
        if con is not None:con.close()
        if temp.exists():temp.unlink()

def check_file(path):
    con=sqlite3.connect("file:"+path.resolve().as_posix()+"?mode=ro",uri=True)
    try:return validate(con)
    finally:con.close()

def query_file(path,term,limit=20):
    if not 1<=limit<=100:raise Refused("Invalid limit")
    con=sqlite3.connect("file:"+path.resolve().as_posix()+"?mode=ro",uri=True)
    try:
        return [{"bucket":x[0],"tracker":x[1],"title":x[2],"sha256":x[3]} for x in con.execute(
            """SELECT t.bucket_key,t.tracker_name,t.title,t.raw_sha256 FROM torrent_fts
              JOIN torrents t ON t.id=torrent_fts.rowid
              WHERE torrent_fts MATCH ? ORDER BY bm25(torrent_fts),t.bucket_key,t.source_url LIMIT ?""",
            (term,limit))]
    finally:con.close()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    subs=ap.add_subparsers(dest="cmd",required=True)
    p=subs.add_parser("plan");p.add_argument("--data-root",required=True,type=Path)
    p=subs.add_parser("import");p.add_argument("--data-root",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--replace",action="store_true");p.add_argument("--max-json-mb",type=int,default=128)
    p=subs.add_parser("verify");p.add_argument("--database",type=Path,required=True)
    p=subs.add_parser("search");p.add_argument("--database",type=Path,required=True)
    p.add_argument("--query",required=True);p.add_argument("--limit",type=int,default=20)
    a=ap.parse_args()
    try:
        if a.cmd=="plan":
            plan=discover(a.data_root)
            result={"status":"READ_ONLY_SOURCE_PLAN","buckets":len(plan["specs"]),
                    "track_files":len(plan["tracks"]), "source_bytes":plan["source_bytes"],
                    "records":"UNKNOWN_UNTIL_IMPORT"}
        elif a.cmd=="import":
            if not 1<=a.max_json_mb<=1024:raise Refused("max-json-mb out of range")
            result=stage(a.data_root,a.output,a.max_json_mb*1024*1024,a.replace)
        elif a.cmd=="verify":result={"status":"LOCAL_SQLITE_VERIFIED_NOT_BUNNY",**check_file(a.database)}
        else:result={"status":"LOCAL_SEARCH_NOT_PARITY_CERTIFIED",
                     "results":query_file(a.database,a.query,a.limit)}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0
    except (Refused,OSError,sqlite3.Error,ValueError) as e:
        # Never expose secret-containing raw torrent records or account keys.
        print("BUNNY_NATIVE_IMPORT_REFUSED: "+type(e).__name__+
              (": "+str(e) if isinstance(e,Refused) else ""),file=sys.stderr)
        return 2

if __name__=="__main__":sys.exit(main())
