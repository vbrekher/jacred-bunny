"""Offline original .NET vs Bunny Edge strict JSON/XML parity comparator."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"tools"/"bunny_native_parity.py"
spec=importlib.util.spec_from_file_location("bunny_native_parity",SCRIPT)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def fixture(body, kind="json", status=200):
    return {"schema":mod.SCHEMA,"cases":[{"id":"movie-001",
        "path":"/api/v2.0/indexers/all/results?query=Example",
        "type":kind,"status":status,"body":body}]}
def save(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False),encoding="utf-8")

class GoldenTest(unittest.TestCase):
    def test_json_key_order_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            save(p/"a.json",fixture({"Results":[{"Title":"film","Size":1}],"jacred":True}))
            save(p/"b.json",fixture({"jacred":True,"Results":[{"Size":1,"Title":"film"}]}))
            self.assertEqual(mod.compare(mod.load(p/"a.json"),mod.load(p/"b.json"))["status"],"PASS")
    def test_json_array_order_changes_are_not_silenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            save(p/"a.json",fixture({"Results":[1,2,3]}))
            save(p/"b.json",fixture({"Results":[3,2,1]}))
            self.assertEqual(mod.compare(mod.load(p/"a.json"),mod.load(p/"b.json"))["mismatches"],1)
    def test_xml_attribute_order_ignored_but_text_preserved(self):
        original='<rss version="2.0"><item a="1" b="2">Film</item></rss>'
        same='<rss version="2.0"><item b="2" a="1">Film</item></rss>'
        diff='<rss version="2.0"><item b="2" a="1">Different</item></rss>'
        a=mod.normalized(original,"xml")
        self.assertEqual(a,mod.normalized(same,"xml"))
        self.assertNotEqual(a,mod.normalized(diff,"xml"))
    def test_status_and_path_are_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            save(p/"a.json",fixture([]))
            save(p/"b.json",fixture([],status=503))
            self.assertEqual(mod.compare(mod.load(p/"a.json"),mod.load(p/"b.json"))["status"],"FAIL")
    def test_disallow_token_in_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"leak.json"
            x=fixture([])
            x["cases"][0]["path"]="/api/v2.0/indexers/all/results?apikey=SECRET"
            save(path,x)
            with self.assertRaises(mod.InvalidFixture):mod.load(path)
    def test_refuse_empty_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"empty.json"
            save(path,{"schema":mod.SCHEMA,"cases":[]})
            with self.assertRaises(mod.InvalidFixture):mod.load(path)
    def test_cli_pass_is_redaction_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            secret={"Results":[{"MagnetUri":"magnet:?xt=urn:btih:SECRET"}]}
            save(p/"a.json",fixture(secret));save(p/"b.json",fixture(secret))
            proc=subprocess.run([sys.executable,str(SCRIPT),"--legacy",str(p/"a.json"),
                "--edge",str(p/"b.json")],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertIn('"status": "PASS"',proc.stdout)
            self.assertNotIn("magnet",proc.stdout)
            self.assertNotIn("SECRET",proc.stdout)
    def test_cli_rejects_mismatch_without_leaking_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            save(p/"a.json",fixture({"Results":["PRIVATE_A"]}))
            save(p/"b.json",fixture({"Results":["PRIVATE_B"]}))
            proc=subprocess.run([sys.executable,str(SCRIPT),"--legacy",str(p/"a.json"),
                "--edge",str(p/"b.json")],capture_output=True,text=True)
            self.assertEqual(proc.returncode,3)
            self.assertIn('"status": "FAIL"',proc.stdout)
            self.assertNotIn("PRIVATE_",proc.stdout)

if __name__=="__main__":
    unittest.main()
