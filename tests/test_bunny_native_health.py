import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
import native
spec=importlib.util.spec_from_file_location('health_cli',ROOT/'bunny/cli.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)

class Response(io.BytesIO):
    status=200

class HealthTests(unittest.TestCase):
    def test_public_liveness_cannot_mask_lost_api_credentials_after_update(self):
        with tempfile.TemporaryDirectory() as root:
            deploy=Mock();deploy.secret_path=Path(root)/'credentials.json'
            native.atomic_private(deploy.secret_path,{'api_key':'test-api','admin_key':'test-admin'})
            deploy.state={'source_sha':'a'*40,'resources':{'worker':{'id':'owned'}}}
            deploy.verify.return_value={'displayEndpoint':{'type':'cdn','address':'mc-fixture.bunny.run'}}
            deploy.sql_client.return_value.query.side_effect=[[{'n':2}],[{'n':0}]]
            def open_request(req,timeout):
                if '/health' in req.full_url:return Response(b'{"status":"OK"}')
                if '/version' in req.full_url:return Response(json.dumps({'gitSha':'a'*8}).encode())
                raise urllib.error.HTTPError(req.full_url,401,'Unauthorized',None,io.BytesIO(b''))
            with patch.object(cli.urllib.request,'build_opener',return_value=Mock(open=open_request)):
                result=cli.native_health(deploy)
            self.assertFalse(result['infrastructureReady'])
