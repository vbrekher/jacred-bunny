"""No-network, secret-safe capture regression."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"tools"/"bunny_native_capture.py"
spec=importlib.util.spec_from_file_location("bunny_native_capture",SCRIPT)
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"cases.json"
        self.path.write_text(json.dumps({"schema":"jacred.bunny.http-compare.v1",
         "cases":[{"id":"trackers","path":"/api/v1.0/trackers","type":"json"}]}),encoding="utf-8")
    def test_https_and_loopback_allowed(self):
        self.assertEqual(len(a.validate("https://edge.example",self.path)["cases"]),1)
        self.assertEqual(len(a.validate("http://127.0.0.1:9117",self.path)["cases"]),1)
    def test_plain_external_http_refused(self):
        with self.assertRaises(a.Refused):a.validate("http://remote.example",self.path)
    def test_path_embedded_api_key_refused(self):
        data=json.loads(self.path.read_text())
        data["cases"][0]["path"]="/api/v1.0/torrents?apikey=PRIVATE"
        self.path.write_text(json.dumps(data))
        with self.assertRaises(a.Refused):a.validate("https://edge.example",self.path)
    def test_duplicate_ids_refused(self):
        data=json.loads(self.path.read_text())
        data["cases"].append(data["cases"][0])
        self.path.write_text(json.dumps(data))
        with self.assertRaises(a.Refused):a.validate("https://edge.example",self.path)
    def test_private_output_and_refuse_overwrite(self):
        target=Path(self.temp.name)/"private.json"
        data={"schema":"jacred.bunny.http-compare.v1","cases":[]}
        a.private_output(target,data)
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        with self.assertRaises(a.Refused):a.private_output(target,data)
        self.assertTrue(target.exists())
    def test_non_redirecting_http_handler(self):
        self.assertIsNone(a.NoRedirect().redirect_request(None,None,None,None,None,"https://evil.example"))
if __name__=="__main__":unittest.main()
