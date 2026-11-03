#!/usr/bin/env python3
"""JacRed Bunny v0.3 read-only source and local FileDB capacity inventory.

No network calls or credentials. No database writes. Local SQLite FTS is
a feasibility smoke only, never evidence of live Bunny Database behavior.
"""
import argparse
import gzip
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import tempfile
import time

EXPECTED_TRACKERS = ("anibelka anidub anifilm aniliberty animelayer anistar baibako "
 "bitru kinozal knaben korsars leproduction lostfilm mazepa megapeer nnmclub "
 "rudub rutor rutracker selezen subsplease toloka torrentby ultradox viruseproject").split()
ATTR = re.compile(r'\[(Route|HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)\s*(?:\(\s*"([^"]*)")?[^]\n]*\]')
DECL = re.compile(r'\b(?:public|internal)\s+(?:(?:async|static|override|virtual)\s+)*(?:[\w<>?,\[\].]+\s+)+(\w+)\s*\(')
CLASS = re.compile(r'\bclass\s+(\w+Controller)\b')

def extract_routes(root):
    records = []
    for path in sorted((root / "Controllers").rglob("*Controller.cs")):
        if path.is_symlink() or not path.is_file():
            continue
        cls, prefix, attrs = path.stem, None, []
        for line_no, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
            attrs.extend((m.group(1), m.group(2)) for m in ATTR.finditer(line))
            if match := CLASS.search(line):
                cls = match.group(1)
                prefix = next((value for kind, value in reversed(attrs) if kind == "Route" and value), None)
                attrs = []
                continue
            declaration = DECL.search(line)
            if not declaration:
                if line.strip() and not line.strip().startswith(("//", "/*", "*", "[")):
                    attrs = []
                continue
            action = declaration.group(1)
            if action == cls:
                attrs = []
                continue
            methods = [kind[4:].upper() for kind, _ in attrs if kind.startswith("Http")]
            paths = [value for kind, value in attrs if kind == "Route" and value]
            paths.extend(value for kind, value in attrs if kind.startswith("Http") and value)
            if not paths and prefix and methods:
                paths = [""]
            for template in paths:
                if template.startswith(("/", "~/")):
                    resolved = template.removeprefix("~")
                elif prefix:
                    resolved = prefix.rstrip("/") + ("/" + template.lstrip("/") if template else "")
                else:
                    resolved = "/" + template
                resolved = re.sub(r"/+", "/", resolved.replace("[controller]", cls.removesuffix("Controller")).replace("[action]", action))
                records.append({"route": resolved, "declared_verbs": methods or ["UNSPECIFIED"],
                                "handler": cls + "." + action, "source": path.relative_to(root).as_posix(),
                                "line": line_no, "auth": "CENTRAL_POLICY_NEEDS_RUNTIME_VERIFICATION"})
            attrs = []
    return sorted(records, key=lambda r: (r["route"], r["handler"], r["line"]))

def trackers(root):
    config = (root / "Data/init.yaml").read_text("utf-8-sig")
    block = config.split("synctrackers:", 1)[1].split("\ndisable_trackers:", 1)[0]
    slugs = re.findall(r'(?m)^\s+- ([a-z0-9_-]+)\s*$', block)
    if len(slugs) != len(set(slugs)):
        raise ValueError("Duplicate synctrackers")
    lines = (root / "Data/crontab").read_text("utf-8").splitlines()
    active = set()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        active.update(re.findall(r"/cron/([a-z0-9_-]+)/", line.lower()))
    out = []
    directories = {}
    for item in (root / "Infrastructure/Trackers").iterdir():
        if item.is_dir() and not item.is_symlink():
            folded = item.name.casefold()
            if folded in directories:
                raise ValueError("Ambiguous tracker directory case")
            directories[folded] = item
    for slug in slugs:
        directory = directories.get(slug.casefold())
        if directory is None:
            raise ValueError("Missing tracker directory: " + slug)
        out.append({"id": slug, "parsers": sorted(x.relative_to(root).as_posix() for x in directory.glob("*Parser*.cs")),
                    "sync_services": sorted(x.relative_to(root).as_posix() for x in directory.glob("*SyncService.cs")),
                    "active_in_host_crontab": slug in active, "live": "NOT_TESTED"})
    return out

def inspect_disk(data_root, sample_limit=0):
    fdb_root = data_root / "fdb"
    all_fdb = sorted(p for p in fdb_root.rglob("*") if p.is_file() and not p.is_symlink()) if fdb_root.is_dir() else []

    def is_file_db_shard(p):
        # Match the two real pathDb() formats from FileDB.MasterDb.cs:
        # fdbPathLevels=2: fdb/aa/<30 hex>, =1: fdb/a/<32 hex>.
        # Dotfiles, READMEs and arbitrary nested files are not database shards.
        parts = p.relative_to(fdb_root).parts
        if len(parts) != 2:
            return False
        folder, filename = parts
        return bool((re.fullmatch(r"[0-9a-f]{2}", folder) and
                     re.fullmatch(r"[0-9a-f]{30}", filename)) or
                    (re.fullmatch(r"[0-9a-f]", folder) and
                     re.fullmatch(r"[0-9a-f]{32}", filename)))

    files = [p for p in all_fdb if is_file_db_shard(p)]
    track_root = data_root / "tracks"
    tracks = sorted(p for p in track_root.rglob("*")
                    if p.is_file() and not p.is_symlink()
                    and all(not part.startswith(".") for part in p.relative_to(track_root).parts)
                    and not p.name.lower().startswith("readme")) if track_root.is_dir() else []
    masters = sorted(p for p in data_root.glob("masterDb*.bz") if p.is_file() and not p.is_symlink())
    master_magic = []
    valid_masters = []
    for p in masters:
        with p.open("rb") as inp:
            is_gzip = inp.read(2) == b"\x1f\x8b"
        master_magic.append({"name": p.name, "gzip_magic": is_gzip, "bytes": p.stat().st_size})
        if is_gzip:
            valid_masters.append(p)
    sample = {"checked": 0, "records_seen": 0, "invalid": 0, "over_limit": 0}
    for p in files[:max(0,sample_limit)]:
        sample["checked"] += 1
        try:
            with gzip.open(p, "rb") as fh:
                raw = fh.read(8_000_001)
            if len(raw) > 8_000_000:
                sample["over_limit"] += 1
                continue
            obj = json.loads(raw)
            if not isinstance(obj,dict):
                sample["invalid"] += 1
            else:
                sample["records_seen"] += len(obj)
        except (OSError, ValueError, UnicodeError):
            sample["invalid"] += 1
    size = sum(x.stat().st_size for x in files + tracks + valid_masters)
    # Presence of canonical files establishes only disk bytes, not record parity.
    return {"state": "MEASURED_LOCAL_FILE_BYTES" if (files or tracks or valid_masters) else "NO_REAL_DATASET",
            "fdb_files": len(files), "fdb_bytes": sum(x.stat().st_size for x in files),
            "ignored_non_shard_fdb_files": len(all_fdb) - len(files),
            "track_files": len(tracks), "master_files": master_magic,
            "valid_gzip_master_files": len(valid_masters),
            "total_source_bytes": size, "total_records": "UNKNOWN_NOT_COUNTED", "sample": sample}

def capacity(d):
    if d["state"] == "NO_REAL_DATASET":
        return {"state": "BLOCKED_MISSING_REAL_DATA", "scenarios": []}
    usable = 750_000_000  # 75% of 1 GB limit, includes operational headroom.
    return {"state": "HYPOTHETICAL_NOT_ACTUAL_SQL_SIZE", "scenarios": [
        {"hypothetical_expansion": ratio, "shards_estimated": max(1,math.ceil(d["total_source_bytes"]*ratio/usable)),
         "fits_default_49_shards": max(1,math.ceil(d["total_source_bytes"]*ratio/usable)) <= 49}
        for ratio in (2,4,8)]}

def local_sqlite_probe(rows=1000):
    if not 100 <= rows <= 30000:
        raise ValueError("rows must be 100..30000")
    with tempfile.TemporaryDirectory(prefix="jacred-audit-") as tmp:
        dbfile = Path(tmp)/"fts.db"
        db = sqlite3.connect(str(dbfile))
        db.executescript("CREATE TABLE torrents (id INTEGER PRIMARY KEY, title TEXT NOT NULL);"
                         "CREATE VIRTUAL TABLE title_fts USING fts5(title,content='torrents',content_rowid='id');")
        started = time.perf_counter()
        with db:
            db.executemany("INSERT INTO torrents VALUES (?,?)", ((i,f"Test Film {i}") for i in range(rows)))
            db.execute("INSERT INTO title_fts(title_fts) VALUES('rebuild')")
        latencies = []
        for _ in range(20):
            t = time.perf_counter()
            count = db.execute("SELECT count(*) FROM title_fts WHERE title_fts MATCH 'Film'").fetchone()[0]
            latencies.append((time.perf_counter()-t)*1000)
        assert count == rows
        insert_ms = round((time.perf_counter()-started)*1000,2)
        db.close()
        return {"type": "LOCAL_SQLITE_ONLY_NOT_BUNNY", "rows": rows, "fts_hits": count,
                "insert_and_query_ms": insert_ms, "query_p50_ms": round(statistics.median(latencies),3),
                "query_p95_ms": round(sorted(latencies)[18],3), "db_bytes": dbfile.stat().st_size}

def audit(root, data_root=None, sample_limit=0, sqlite_rows=None):
    paths = extract_routes(root)
    sources = trackers(root)
    disk = inspect_disk(data_root or root/"Data", sample_limit)
    return {"schema": "jacred.bunny.native.source-audit.v0.3",
            "mode": "READ_ONLY_STATIC_NOT_LIVE",
            "limitations": ["Static attribute extraction may omit convention and dynamic routes",
                             "Access control requires runtime middleware tests",
                             "Source inventory does not prove live tracker availability",
                             "SQLite FTS synthetic probe does not validate Bunny libSQL"],
            "documented_limits": {"database_count":50,"bytes_per_database":1_000_000_000,
                                  "edge_cpu_seconds":30,"edge_memory_mb":128,"edge_subrequests":50},
            "routes":paths,"route_count":len(paths),"trackers":sources,"tracker_count":len(sources),
            "disk":disk,"capacity":capacity(disk),
            "sqlite_probe":local_sqlite_probe(sqlite_rows) if sqlite_rows else "NOT_RUN"}

def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    cli.add_argument("--data-root",type=Path)
    cli.add_argument("--sample-shards",type=int,default=0)
    cli.add_argument("--synthetic-sqlite",type=int,metavar="ROWS")
    cli.add_argument("--output",type=Path)
    cli.add_argument("--strict",action="store_true")
    x = cli.parse_args()
    root = x.root.resolve()
    data_root = x.data_root.resolve() if x.data_root else None
    report = audit(root,data_root,x.sample_shards,x.synthetic_sqlite)
    if x.strict:
        if [s["id"] for s in report["trackers"]] != EXPECTED_TRACKERS:
            raise SystemExit("FAIL: tracker contract changed; review 25 sources")
        if report["route_count"] < 15:
            raise SystemExit("FAIL: attributed route inventory suspiciously small")
    if x.output:
        out = x.output.resolve()
        actual_data = data_root or root/"Data"
        if out == actual_data or actual_data in out.parents:
            raise SystemExit("Refusing to write inside the source data directory")
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True)+"\n",encoding="utf-8")
    print("BUNNY_NATIVE_V0_3_AUDIT_PASS routes="+str(report["route_count"])
          +" trackers="+str(report["tracker_count"])+" dataset="+report["disk"]["state"])

if __name__=="__main__":
    main()
