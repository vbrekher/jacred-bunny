"""Strict operator-declared .NET/Edge HTTP golden evidence regressions.

Uses synthetic PRIVATE captures only. Never claims actual live Bunny parity.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"tools"/"bunny_native_parity.py"
spec=importlib.util.spec_from_file_location("bunny_native_parity_evidence",P)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
SOURCE="a"*64
CASES="b"*64

def sample(role,origin,sha=SOURCE,found=True,title="Private Film"):
    return {
        "schema":p.SCHEMA,
        "provenance":{
            "source_role":role,"master_sha256":sha,
            "origin_fingerprint":origin,
            "cases_sha256":CASES,
            "captured_at":"2026-11-03T13:05:00+00:00",
            "provenance_kind":"operator_declared_not_remote_attested"
        },
        "cases":[{
            "id":"search-positive",
            "path":"/api/v2.0/indexers/all/results?query=Example",
            "type":"json","status":200,
            "body":json.dumps({"Results":[{"Title":title}]} if found else
                              {"Results":[]},ensure_ascii=False)
        }]
    }

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name)
        self.legacy=self.dir/"original-private.json"
        self.edge=self.dir/"edge-private.json"
    def put(self,left=None,right=None):
        self.legacy.write_text(json.dumps(left or sample("original","1"*64)),encoding="utf-8")
        self.edge.write_text(json.dumps(right or sample("edge","2"*64)),encoding="utf-8")
    def compare(self):
        return p.compare(p.load(self.legacy),p.load(self.edge))
    def test_declared_distinct_origins_matching_snapshot_and_positive_search(self):
        self.put()
        output=p.evidence_gate(self.legacy,self.edge,self.compare())
        self.assertEqual(output["status"],"OPERATOR_DECLARED_HTTP_GOLDEN_MATCH_NOT_LIVE_CERTIFICATION")
        self.assertTrue(output["positive_search_verified"])
        self.assertFalse(output["production_cutover_authorized"])
    def test_same_origin_is_not_independent_proof(self):
        self.put(right=sample("edge","1"*64))
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_different_snapshots_are_refused(self):
        self.put(right=sample("edge","2"*64,sha="f"*64))
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_empty_search_cannot_pretend_to_be_golden(self):
        self.put(left=sample("original","1"*64,found=False),
                 right=sample("edge","2"*64,found=False))
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_no_provenance_cannot_be_strictly_certified(self):
        a=sample("original","1"*64)
        a.pop("provenance")
        self.put(left=a)
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_wrong_capture_role_refused(self):
        self.put(left=sample("edge","1"*64))
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_different_case_templates_refused(self):
        a=sample("edge","2"*64)
        a["provenance"]["cases_sha256"]="c"*64
        self.put(right=a)
        with self.assertRaises(p.InvalidFixture):
            p.evidence_gate(self.legacy,self.edge,self.compare())
    def test_mismatching_real_result_fails_without_exposing_title(self):
        self.put(right=sample("edge","2"*64,title="ANOTHER_SECRET_TITLE"))
        output=p.evidence_gate(self.legacy,self.edge,self.compare())
        self.assertEqual(output["status"],"FAIL")
        self.assertEqual(output["mismatches"],1)
        self.assertNotIn("ANOTHER_SECRET_TITLE",str(output))
    def test_cli_strict_provenance_missing_refuses_without_data_leak(self):
        self.put()
        a=json.loads(self.edge.read_text())
        a.pop("provenance")
        self.edge.write_text(json.dumps(a))
        run=subprocess.run([sys.executable,str(P),"--legacy",str(self.legacy),
            "--edge",str(self.edge),"--require-evidence"],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,2)
        self.assertNotIn("Private Film",run.stdout+run.stderr)
        self.assertIn("REFUSED",run.stderr)
    def test_cli_strict_positive_pass_is_distinctly_not_live_certification(self):
        self.put()
        run=subprocess.run([sys.executable,str(P),"--legacy",str(self.legacy),
            "--edge",str(self.edge),"--require-evidence"],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("NOT_LIVE_CERTIFICATION",run.stdout)
        self.assertNotIn("Private Film",run.stdout)
if __name__=="__main__":
    unittest.main()
