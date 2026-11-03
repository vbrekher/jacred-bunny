"""Empty staging transition safety; fixture SQLite is not live production acceptance."""
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
import native
from operations import Operations
import test_bunny_native_operations as ops_fixture
from test_bunny_native_sql import SQLiteProtocol


class CutoverTests(unittest.TestCase):
    payload=ops_fixture.OperationTests.payload
    # Reuse only the owned API fixture; inherited operation regressions remain useful.
    def setUp(self):
        ops_fixture.OperationTests.setUp(self)
        self.deploy.state['catalog_backend']='filedb';self.deploy.save()
        env=[{'name':'JACRED_BUNNY_CRON','value':'0'}]
        self.remote['containerTemplates'][0]['environmentVariables']=env;self.remote['status']='inactive'
        native.atomic_private(self.deploy.secret_path,{'api_key':'test-api','admin_key':'test-admin','database_read_token':'test-reader','database_write_token':'test-writer'})
        self.protocol=SQLiteProtocol();self.addCleanup(self.protocol.db.close)
        self.sql=native.SQL('libsql://fixture.lite.bunnydb.net','test-writer',self.protocol)
        self.sql.migrate([ROOT/'bunny-native/migrations'/name for name in ('0001_catalog.sql','0002_search_fts5.sql')])
        self.deploy.sql_client=Mock(return_value=self.sql)
        self.manifest=json.loads((self.new/'manifest.json').read_text());name='0004_catalog_commits.sql'
        (self.new/name).write_bytes((ROOT/'bunny-native/migrations'/name).read_bytes())
        self.manifest['files'][name]=hashlib.sha256((self.new/name).read_bytes()).hexdigest()
        self.manifest.update(schema='jacred-bunny-native-payload/v2',catalog_backend='bunny-sql')
        (self.new/'manifest.json').write_text(json.dumps(self.manifest))
        self.proof={'legacy_source_sha':'a'*40,'observed_at':datetime.now(timezone.utc).isoformat(),
            'catalog_keys':0,'torrents':0,'orphan_shards':0,'track_files':0,'sync_disabled':True,'tracks_disabled':True,'cron_disabled':True}

    def prepare(self):self.deploy.prepare_empty_sql_cutover(self.new,self.proof)

    def test_atomic_settings_transition_preserves_ids_and_denies_legacy_rollback(self):
        ids={key:value['id'] for key,value in self.deploy.state['resources'].items()}
        self.prepare();self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(self.deploy.state['catalog_backend'],'bunny-sql')
        self.assertEqual(len(self.sql.query('SELECT name FROM native_schema_migrations')),3)
        self.assertEqual({key:value['id'] for key,value in self.deploy.state['resources'].items()},ids)
        self.deploy.remember_payload(self.new)
        self.deploy.finish_sql_cutover({'infrastructureReady':True,'worker_source_matches':True,'worker_catalog_backend':'bunny-sql'})
        with self.assertRaisesRegex(native.NativeError,'No confirmed previous'):self.deploy.rollback_payload()
        self.assertNotIn('pending_cutover',self.deploy.state)

    def test_uncertain_patch_adopts_only_exact_target_without_replay(self):
        self.prepare();self.uncertain=True
        self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']),1)
        self.assertEqual(self.deploy.state['catalog_backend'],'bunny-sql')

    def test_crash_after_patch_can_adopt_target_and_budget_stop_identity(self):
        self.prepare();self.fail_get_after_patch=True
        with self.assertRaises(native.NativeError):self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(self.deploy.verify_worker_for_stop()['status'],'inactive')
        self.fail_get_after_patch=False
        self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']),1)

    def test_provider_patch_can_activate_exact_target_under_bounded_watchdog(self):
        original=self.deploy.api
        def api(method,path,body=None):
            result=original(method,path,body)
            if method=='PATCH':self.remote['status']='active'
            return result
        self.deploy.api=api
        self.prepare();self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(self.deploy.state['catalog_backend'],'bunny-sql')
        self.assertTrue(self.deploy.state['pending_cutover']['provider_patch_activated_target'])
        self.assertEqual(len([call for call in self.calls if call[0]=='PATCH']),1)

    def test_autoactivated_target_without_watchdog_requests_stop_and_keeps_sql_authority(self):
        original=self.deploy.api
        def api(method,path,body=None):
            result=original(method,path,body)
            if method=='PATCH':self.remote['status']='active'
            if method=='POST' and path.endswith('/undeploy'):self.remote['status']='inactive'
            return result
        self.deploy.api=api;self.deploy.watchdog_available.return_value=False
        self.prepare()
        with self.assertRaisesRegex(native.NativeError,'Stop requested'):self.deploy.configure_sql_cutover(self.new)
        self.assertIn(('POST','/mc/apps/worker/undeploy'),self.calls)
        self.assertEqual(self.deploy.state['catalog_backend'],'bunny-sql')
        self.assertEqual(self.remote['status'],'inactive')

    def test_nonempty_source_or_target_is_rejected_without_patch(self):
        proof=dict(self.proof,torrents=1)
        with self.assertRaises(native.NativeError):self.deploy.prepare_empty_sql_cutover(self.new,proof)
        self.sql.query("INSERT INTO buckets VALUES('occupied','md5',NULL,0,'sha',0)")
        with self.assertRaises(native.NativeError):self.prepare()
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_running_source_cannot_apply_schema_or_patch(self):
        self.prepare();self.remote['status']='active'
        with self.assertRaisesRegex(native.NativeError,'inactive'):self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(len(self.sql.query('SELECT name FROM native_schema_migrations')),2)
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_target_data_changed_after_preparation_is_not_overwritten(self):
        self.prepare();self.sql.query("INSERT INTO buckets VALUES('occupied','md5',NULL,0,'sha',0)")
        with self.assertRaisesRegex(native.NativeError,'changed after'):self.deploy.configure_sql_cutover(self.new)
        self.assertEqual(self.sql.query('SELECT count(*) AS n FROM buckets')[0]['n'],1)
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_empty_source_probe_requires_real_diagnostics_and_admin_denial(self):
        self.remote['status']='active';status_reads=0
        class Response(io.BytesIO):status=200
        def opened(request,timeout):
            nonlocal status_reads
            path=request.full_url.split('.bunny.run',1)[1]
            admin=request.get_header('X-dev-key')
            if path=='/version':data={'catalogBackend':'filedb','gitFullSha':'a'*40}
            elif path=='/api/v1.0/config':
                if not admin:
                    import urllib.error
                    raise urllib.error.HTTPError(request.full_url,401,'Unauthorized',None,io.BytesIO(b''))
                data={'data':{'tracks':False,'syncapi':None}}
            elif path=='/health/background-jobs':data={'jobs':[]}
            elif path.startswith('/dev/FindCorrupt'):data={'ok':True,'totalFdbKeys':0,'totalTorrents':0}
            elif path.startswith('/cron/maintenance/Check'):data='ok'
            elif path.startswith('/cron/maintenance/Status'):
                status_reads+=1;data={'running':False,'last':None if status_reads==1 else {'ok':True,'mode':'report','finishedAt':'new','totals':{'fdbKeys':0,'torrents':0},'issues':{key:{'count':0} for key in ('orphanShardFiles','missingShardFile','emptyShardListed')}}}
            elif path.startswith('/dev/TracksStats'):data={'ok':True,'stats':{key:0 for key in ('total','filesScanned','fromTracksFiles','fromMemory','fromTorrentDb','torrentsScanned','readErrors','torrentDbErrors')}}
            else:raise AssertionError(path)
            return Response(json.dumps(data).encode())
        with patch('operations.urllib.request.build_opener',return_value=Mock(open=opened)):
            proof=self.deploy.probe_empty_legacy('https://fixture.bunny.run')
        self.assertEqual(proof['torrents'],0);self.assertEqual(proof['orphan_shards'],0)
