#!/usr/bin/env python3
"""OFFLINE ONLY: synthetic JacRed GZip FileDB -> real SQLite staging SQL bridge.

No network; no Bunny credentials. The source fixture is generated and removed
by Node tests. This is not a captured original .NET HTTP response.
"""
import gzip
import hashlib
import json
import pathlib
import sqlite3
import sys

ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools"))
from bunny_native_import import stage,check_file

def write_gzip(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as stream:
        json.dump(value,stream,ensure_ascii=False,sort_keys=True)

def create(destination):
    root=pathlib.Path(destination)
    data=root/"source"
    master={}
    source={
      "шелкунчик:thenutcracker":{
        "https://tracker.invalid/t/1":{
          "trackerName":"rutor","title":"Щёлкунчик & HD 2026",
          "name":"Щёлкунчик","originalname":"The Nutcracker",
          "types":["movie"],"size":1073741824,"relased":2026,"quality":1080,
          "sid":25,"pir":3,"magnet":"magnet:?xt=urn:btih:"+"a"*40,
          "languages":["rus"],"voices":["Дубляж"],"updateTime":"2026-01-01T00:00:00Z",
          "createTime":"2026-01-01T00:00:00Z"
        }
      },
      "сериал:series":{
        "https://tracker.invalid/t/2":{
          "trackerName":"kinozal","title":"Сериал S02E03 1080p",
          "name":"Сериал","originalname":"Series",
          "types":["serial"],"seasons":[2],"relased":2024,"sid":120,
          "magnet":"magnet:?xt=urn:btih:"+"b"*40,
          "updateTime":"2026-02-01T00:00:00Z","createTime":"2026-02-01T00:00:00Z"
        },
        "https://tracker.invalid/t/3":{
          "trackerName":"kinozal","title":"Сериал S02E04 720p",
          "name":"Сериал","originalname":"Series",
          "types":["serial"],"seasons":[2],"relased":2024,"sid":11,
          "magnet":"magnet:?xt=urn:btih:"+"c"*40,
          "updateTime":"2026-02-02T00:00:00Z","createTime":"2026-02-02T00:00:00Z"
        }
      },
      "шелковыйпуть:silkroad":{
        "https://tracker.invalid/t/4":{
          "trackerName":"rutracker","title":"Шёлковый путь / Silk Road",
          "name":"Шёлковый путь","originalname":"Silk Road",
          "types":["documovie"],"relased":2025,"sid":16,
          "magnet":"magnet:?xt=urn:btih:"+"d"*40,
          "updateTime":"2026-01-05T00:00:00Z","createTime":"2026-01-05T00:00:00Z"
        }
      },
      "тест:duplicatetitle":{
        "https://tracker.invalid/t/1":{
          "trackerName":"rutor","title":"Duplicate title with updated time",
          "name":"Тест","originalname":"Duplicate Title",
          "types":["movie"],"sid":55,"relased":2026,
          "updateTime":"2026-03-01T00:00:00Z","createTime":"2026-03-01T00:00:00Z"
        }
      }
    }
    for key,rows in source.items():
        path_hash=hashlib.md5(key.encode("utf-8")).hexdigest()
        master[key]={"updateTime":"2026-03-01T00:00:00Z","fileTime":133123456789012345}
        write_gzip(data/"fdb"/path_hash[:2]/path_hash[2:],rows)
    write_gzip(data/"masterDb.bz",master)
    output=root/"staged.sqlite"
    result=stage(data,output)
    if result["torrents"]!=5 or result["buckets"]!=4:
        raise AssertionError("Unexpected fixture size")
    print(json.dumps({"database":str(output),"verification":check_file(output)},sort_keys=True))

def execute(path):
    request=json.loads(sys.stdin.readline())
    sql=request.get("sql")
    if not isinstance(sql,str) or not sql.lstrip().upper().startswith("SELECT") or ";" in sql:
        raise ValueError("Read-only SELECT bridge expected")
    conn=sqlite3.connect("file:"+pathlib.Path(path).resolve().as_posix()+"?mode=ro",uri=True)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        result=conn.execute(sql,request.get("args",[]))
        rows=[dict(x) for x in result.fetchall()]
        print(json.dumps(rows[0] if request.get("mode")=="first" and rows else
                         None if request.get("mode")=="first" else rows,
                         ensure_ascii=False,separators=(",",":")))
    finally:
        conn.close()

if __name__=="__main__":
    if len(sys.argv)!=3:raise SystemExit("usage: bridge.py create|query DB_OR_WORKDIR")
    if sys.argv[1]=="create":create(sys.argv[2])
    elif sys.argv[1]=="query":execute(sys.argv[2])
    else:raise SystemExit("Unknown test operation")
