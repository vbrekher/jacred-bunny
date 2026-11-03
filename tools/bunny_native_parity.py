#!/usr/bin/env python3
"""Strict JacRed original .NET vs Bunny Edge HTTP response comparison.

Offline ONLY. Consumes separately captured JSON fixtures (never sends requests).
Does not log API keys, magnet values, titles, source URLs or full response bodies.
No result is a true .NET golden gate unless fixtures were captured from real
running original .NET JacRed on the same immutable dataset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

SCHEMA="jacred.bunny.http-compare.v1"
ALLOWED=("/api/v2.0/indexers/","/api/v1.0/torrents","/api/v1/search",
         "/torznab/api","/api/v1/indexer/")
class InvalidFixture(Exception):
    pass

def normalized(body,kind):
    if kind=="json":
        # Exact JSON semantics incl. list ordering, ignoring only object key ordering.
        if isinstance(body,str):
            try:body=json.loads(body)
            except json.JSONDecodeError as exc:raise InvalidFixture("Invalid JSON response") from exc
        return json.dumps(body,ensure_ascii=False,separators=(",",":"),
                          sort_keys=True,allow_nan=False).encode("utf-8")
    if kind=="xml":
        if not isinstance(body,str):raise InvalidFixture("XML body must be string")
        try:
            root=ET.fromstring(body)
        except ET.ParseError as exc:raise InvalidFixture("Invalid XML response") from exc
        def visit(node):
            # Namespace, element order and text preserved; attribute order ignored.
            return (node.tag,tuple(sorted(node.attrib.items())),
                    node.text.strip() if node.text else "",
                    tuple(visit(child) for child in node),
                    node.tail.strip() if node.tail else "")
        return repr(visit(root)).encode("utf-8")
    raise InvalidFixture("Unsupported body type")

def load(path):
    try:payload=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError) as exc:raise InvalidFixture("Unreadable fixture") from exc
    if not isinstance(payload,dict) or payload.get("schema")!=SCHEMA:
        raise InvalidFixture("Wrong fixture schema")
    cases=payload.get("cases")
    if not isinstance(cases,list) or not cases:
        raise InvalidFixture("Empty fixture set")
    if len(cases)>100:raise InvalidFixture("Maximum 100 cases")
    seen={}
    for case in cases:
        if not isinstance(case,dict):raise InvalidFixture("Invalid case object")
        identifier=case.get("id")
        path=case.get("path")
        kind=case.get("type")
        status=case.get("status")
        if not isinstance(identifier,str) or not identifier.isascii() or len(identifier)>60 or not identifier or identifier in seen:
            raise InvalidFixture("Invalid/duplicate case ID")
        if not isinstance(path,str) or not path.startswith(ALLOWED):
            raise InvalidFixture("Path outside read-only parity endpoints")
        if any(x in path.lower() for x in ["apikey=","passkey=","token=","magnet:"]):
            raise InvalidFixture("Credential-bearing path not permitted in fixture")
        if kind not in ("json","xml") or not isinstance(status,int) or not 100<=status<=599:
            raise InvalidFixture("Invalid HTTP capture metadata")
        seen[identifier]={
            "path":path,"type":kind,"status":status,
            "hash":hashlib.sha256(normalized(case.get("body"),kind)).hexdigest()
        }
    return seen

def compare(legacy,candidate):
    if set(legacy)!=set(candidate):
        return {"status":"FAIL","reason":"CASE_IDS_MISMATCH","count":len(legacy),
                "mismatches":abs(len(legacy)-len(candidate))}
    problems=0
    for case_id in sorted(legacy):
        a,b=legacy[case_id],candidate[case_id]
        if a!=b:problems+=1
    return {"status":"PASS" if problems==0 else "FAIL",
            "cases":len(legacy),"mismatches":problems,
            "response_order_checked":True,
            "body_values_logged":False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy",type=Path,required=True)
    parser.add_argument("--edge",type=Path,required=True)
    a=parser.parse_args(argv)
    try:
        result=compare(load(a.legacy),load(a.edge))
        print(json.dumps(result,sort_keys=True))
        return 0 if result["status"]=="PASS" else 3
    except InvalidFixture:
        print('{"status":"REFUSED","reason":"INVALID_PRIVATE_GOLDEN_FIXTURE"}',
              file=sys.stderr)
        return 2
if __name__=="__main__":
    sys.exit(main())
