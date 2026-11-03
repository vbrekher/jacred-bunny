"""Live loopback HTTP capture proof without external network or Bunny secrets."""
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"tools/bunny_native_capture.py"
SHA="a"*64
KEY="local-test-secret-1234567890"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path!="/api/v2.0/indexers/all/results?query=Film" or \
           self.headers.get("X-Api-Key")!=KEY:
            self.send_response(401);self.end_headers();return
        raw=json.dumps({"Results":[{"Title":"Private Film",
            "MagnetUri":"magnet:?xt=urn:btih:"+"a"*40}]},ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def log_message(self,*args):
        return

class LoopbackCapture(unittest.TestCase):
    def test_two_distinct_capture_roles_and_private_snapshot_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            source=base/"cases.json"
            source.write_text(json.dumps({
                "schema":"jacred.bunny.http-compare.v1",
                "cases":[{"id":"film","path":"/api/v2.0/indexers/all/results?query=Film",
                          "type":"json"}]}),encoding="utf-8")
            server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                address=f"http://127.0.0.1:{server.server_port}"
                import os
                env={**os.environ,"JACRED_LEGACY_API_KEY":KEY}
                saved=base/"original-private.json"
                args=[sys.executable,str(SCRIPT),"--run","--base-url",address,
                    "--cases",str(source),"--output",str(saved),
                    "--token-env","JACRED_LEGACY_API_KEY","--source-role","original",
                    "--master-sha256",SHA]
                run=subprocess.run(args,capture_output=True,text=True,timeout=10,env=env)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertEqual(saved.stat().st_mode & 0o777,0o600)
                payload=json.loads(saved.read_text("utf-8"))
                self.assertEqual(payload["provenance"]["source_role"],"original")
                self.assertEqual(payload["provenance"]["master_sha256"],SHA)
                self.assertEqual(payload["provenance"]["origin_fingerprint"],
                                 hashlib.sha256(address.encode()).hexdigest())
                self.assertEqual(payload["provenance"]["cases_sha256"],
                                 hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(len(payload["cases"][0]["body"])>0,True)
                self.assertNotIn(KEY,run.stdout+run.stderr)
                self.assertNotIn("magnet:?",run.stdout+run.stderr)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
    def test_no_run_remains_offline_even_with_example_http_address(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            cases=base/"cases.json"
            cases.write_text(json.dumps({"schema":"jacred.bunny.http-compare.v1",
                "cases":[{"id":"film","path":"/api/v1.0/torrents?search=Film",
                           "type":"json"}]}))
            out=base/"never-created.json"
            r=subprocess.run([sys.executable,str(SCRIPT),
               "--base-url","https://example.invalid",
               "--cases",str(cases),"--output",str(out),
               "--token-env","JACRED_EDGE_API_KEY"],
               capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn("DRY_RUN_NO_NETWORK",r.stdout)
            self.assertFalse(out.exists())

if __name__=="__main__":
    unittest.main()
