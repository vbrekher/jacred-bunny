"""Installer/lifecycle ordering regressions; mocks do not prove fresh cloud acceptance."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
import native,cli,operations

class InstallOrderTests(unittest.TestCase):
    def test_gateway_publication_follows_worker_creation_and_cdn_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);payload=root/'payload';payload.mkdir();files={}
            (payload/'edge.js').write_text('fixture-only')
            for name in native.SQL_RUNTIME_MIGRATIONS:shutil.copyfile(ROOT/'bunny-native/migrations'/name,payload/name)
            for path in payload.iterdir():files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
            manifest={'schema':'jacred-bunny-native-payload/v2','catalog_backend':'bunny-sql','edge_mode':'original-api-gateway','source_sha':'a'*40,'image':{'reference':'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,'digest':'sha256:'+'b'*64},'files':files}
            (payload/'manifest.json').write_text(json.dumps(manifest))
            deploy=Mock();deploy.state={'catalog_backend':'bunny-sql','resources':{},'stop_at':time.time()+3600}
            deploy.secret_path=root/'credentials.json'
            native.atomic_private(deploy.secret_path,{'api_key':'fixture-api','admin_key':'fixture-admin','database_read_token':'fixture-reader','database_write_token':'fixture-writer'})
            deploy.migrate.return_value=[]
            deploy.verify.side_effect=lambda kind:{'url':'libsql://fixture.lite.bunnydb.net'} if kind=='database' else {'status':'inactive'}
            def provision(body):
                deploy.ensure_database_reader.assert_called_once()
                env={row['name']:row['value'] for row in body['containerTemplates'][0]['environmentVariables']}
                self.assertEqual(env['JACRED_DATABASE_READ_TOKEN'],'fixture-reader')
            deploy.provision_worker.side_effect=provision
            def publish(*args,**kwargs):
                deploy.provision_worker.assert_called_once();deploy.bound_staging_cdn.assert_called_once()
            deploy.wire_edge.side_effect=publish
            api=Mock(side_effect=lambda method,path,*args:{'Balance':100,'TrialBalance':0} if path=='/user' else {})
            args=types.SimpleNamespace(release=None,payload_dir=payload,apply=True,state_dir=root/'state',hours=.1,data_gb=1,max_estimated_usd=2,registry_id=None,source_sha=None)
            with patch.object(native,'API',return_value=api),patch.object(native,'Deployment',return_value=deploy),patch.object(native,'current_pricing'),\
                 patch.object(native,'staging_cost_plan',return_value={'estimated_initial_usd':1.23}),patch.object(cli,'account_key',return_value='fixture-key'),\
                 patch.object(cli,'resolve_native_registry',return_value=('1156','sha256:'+'b'*64)),patch.object(cli,'regions',return_value=[('AMS','fixture')]),\
                 patch.object(operations,'Operations'),patch.object(cli,'native_health',return_value={'infrastructureReady':True}),contextlib.redirect_stdout(io.StringIO()):
                cli._native_install(args)
            deploy.wire_edge.assert_called_once();deploy.worker_action.assert_called_once_with('start')

    def test_start_checks_policy_before_already_active_return(self):
        deploy=Mock();deploy.state={'resources':{'worker':{'id':'owned'}}};deploy.verify.return_value={'status':'active'};deploy.watchdog_available.return_value=True
        with patch.object(native,'Deployment',return_value=deploy),patch.object(cli,'account_key',return_value='fixture-key'),contextlib.redirect_stdout(io.StringIO()):
            cli.native_staging(types.SimpleNamespace(operation='start',state_dir=Path('unused-fixture')))
        deploy.bound_staging_cdn.assert_called_once();deploy.worker_action.assert_not_called()

    def test_policy_failure_never_requests_worker_start(self):
        deploy=Mock();deploy.state={'resources':{'worker':{'id':'owned'}}};deploy.verify.return_value={'status':'inactive'}
        deploy.bound_staging_cdn.side_effect=native.NativeError('unconfirmed policy')
        with patch.object(native,'Deployment',return_value=deploy),patch.object(cli,'account_key',return_value='fixture-key'):
            with self.assertRaises(cli.DeploymentError):cli.native_staging(types.SimpleNamespace(operation='start',state_dir=Path('unused-fixture')))
        deploy.worker_action.assert_not_called();deploy.start_watchdog.assert_not_called()
