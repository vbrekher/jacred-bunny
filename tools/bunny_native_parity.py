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

def validate_evidence_capture(path,expected_role):
    """Operator-declared provenance; NOT a cryptographic live-server attestation."""
    try:
        payload=json.loads(Path(path).read_text("utf-8"))
    except (OSError,ValueError) as exc:
        raise InvalidFixture("Missing private capture") from exc
    meta=payload.get("provenance") if isinstance(payload,dict) else None
    if not isinstance(meta,dict) or meta.get("source_role")!=expected_role or \
       meta.get("provenance_kind")!="operator_declared_not_remote_attested":
        raise InvalidFixture("No independently declared provenance")
    for field in ("master_sha256","origin_fingerprint","cases_sha256"):
        value=meta.get(field)
        if not isinstance(value,str) or len(value)!=64 or \
           any(c not in "0123456789abcdef" for c in value):
            raise InvalidFixture("Malformed provenance")
    if not isinstance(meta.get("captured_at"),str):
        raise InvalidFixture("Missing capture timestamp")
    return payload,meta

def positive_search_count(payload):
    """Search result must include a real (nonempty) hit, not only metadata."""
    positive=0
    for case in payload.get("cases",[]):
        if not isinstance(case,dict) or case.get("status")!=200:
            continue
        path=case.get("path","")
        if not isinstance(path,str) or not (
            "/results?" in path or "/api/v1/search?" in path or
            "/api/v1.0/torrents?" in path or
            (path.startswith("/torznab/api?") and "t=search" in path)
        ):
            continue
        if case.get("type")=="json":
            try:
                obj=json.loads(case.get("body",""))
            except (TypeError,ValueError):
                continue
            hits=obj.get("Results",[]) if isinstance(obj,dict) else obj
            if isinstance(hits,list) and len(hits)>0:
                positive+=1
        elif case.get("type")=="xml":
            try:
                root=ET.fromstring(case.get("body",""))
            except (TypeError,ET.ParseError):
                continue
            if root.findall(".//item"):
                positive+=1
    return positive

def evidence_gate(original_path,edge_path,matched):
    """Reject self-comparisons, stale/mixed declared snapshots and empty demos."""
    original,oa=validate_evidence_capture(original_path,"original")
    edge,eb=validate_evidence_capture(edge_path,"edge")
    if oa["origin_fingerprint"]==eb["origin_fingerprint"]:
        raise InvalidFixture("Source endpoints cannot be identical")
    if oa["master_sha256"]!=eb["master_sha256"]:
        raise InvalidFixture("Operator-declared snapshots differ")
    if oa["cases_sha256"]!=eb["cases_sha256"]:
        raise InvalidFixture("Request set differs")
    if positive_search_count(original)<1 or positive_search_count(edge)<1:
        raise InvalidFixture("No positive search results in either capture")
    if matched.get("status")!="PASS":
        return {"status":"FAIL","cases":matched.get("cases",0),
                "mismatches":matched.get("mismatches",0),
                "production_cutover_authorized":False}
    return {
        "status":"OPERATOR_DECLARED_HTTP_GOLDEN_MATCH_NOT_LIVE_CERTIFICATION",
        "cases":matched["cases"],"mismatches":0,
        "distinct_source_origins":True,
        "declared_source_snapshot_match":True,
        "positive_search_verified":True,
        "independent_live_host_attestation":"NOT_AVAILABLE",
        "production_cutover_authorized":False
    }

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy",type=Path,required=True)
    parser.add_argument("--edge",type=Path,required=True)
    parser.add_argument("--require-evidence",action="store_true",help="Require distinct source provenance and positive search")
    a=parser.parse_args(argv)
    try:
        result=compare(load(a.legacy),load(a.edge))
        if a.require_evidence:
            result=evidence_gate(a.legacy,a.edge,result)
        print(json.dumps(result,sort_keys=True))
        return 0 if result["status"] in ("PASS",
            "OPERATOR_DECLARED_HTTP_GOLDEN_MATCH_NOT_LIVE_CERTIFICATION") else 3
    except InvalidFixture:
        print('{"status":"REFUSED","reason":"INVALID_PRIVATE_GOLDEN_FIXTURE"}',
              file=sys.stderr)
        return 2
if __name__=="__main__":
    sys.exit(main())
