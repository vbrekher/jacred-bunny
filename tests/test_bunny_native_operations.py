"""Image/Edge rollback safety; mocked API checks are not live acceptance."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
import native
from operations import Operations


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.calls=[];self.uncertain=False;self.fail_get_after_patch=False;self.pending_get_failure=False
        self.old=self.payload('a','b','old bundle');self.new=self.payload('c','d','new bundle')
        self.remote=None;self.edge_code='old bundle';self.edge_release=1
        def api(method,path,body=None):
            self.calls.append((method,path))
            if path=='/mc/apps/worker':
                if self.pending_get_failure:
                    self.pending_get_failure=False;raise native.NativeError('network error')
                return self.remote
            if method=='PATCH':
                self.remote['containerTemplates'][0].update(body)
                if self.fail_get_after_patch:self.pending_get_failure=True
                if self.uncertain:raise native.NativeError('response lost')
                return None
            if path=='/compute/script/1':return {'Name':self.deploy.state['name'],'CurrentReleaseId':self.edge_release,'DefaultHostname':'https://fixture.bunny.run'}
            if path.endswith('/code'):
                if method=='GET':return {'Code':self.edge_code}
                self.edge_code=body['Code'];return None
            if path.endswith('/publish'):self.edge_release+=1;return None
            if path=='/database/v2/databases/db':return {'db':{'name':self.deploy.state['name'],'url':'libsql://fixture.lite.bunnydb.net'}}
            return {'token':'test-token'} if path.endswith('/auth/generate') else None
        self.deploy=Operations(self.root/'state',api)
        self.deploy.initialize('a'*40,'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,'sha256:'+'b'*64)
        container={'id':'container','name':'jacred','imageNamespace':'vbrekher','imageName':'jacred-bunny',
            'imageTag':'sha-'+'a'*40,'imageDigest':'sha256:'+'b'*64,'imageRegistryId':'1156','volumeMounts':[{'name':'data','mountPath':'/app/Data'}]}
        identity={'container':{k:container[k] for k in ['name','imageNamespace','imageName','imageTag','imageDigest','imageRegistryId']},
            'region':'AMS','volumeMounts':container['volumeMounts'],'volumes':[{'name':'data','size':1}]}
        self.remote={'name':self.deploy.state['name'],'autoScaling':{'min':1,'max':1},'regionSettings':{'requiredRegionIds':['AMS'],'allowedRegionIds':['AMS']},
                     'containerTemplates':[container],'volumes':identity['volumes'],'status':'active'}
        self.deploy.state['resources']={
            'worker':{'id':'worker','name':self.deploy.state['name'],'status':'created','get_path':'/mc/apps/{id}','name_field':'name','identity':identity},
            'database':{'id':'db','name':self.deploy.state['name'],'status':'created','get_path':'/database/v2/databases/{id}','name_field':'name','response_field':'db'},
            'edge':{'id':1,'name':self.deploy.state['name'],'status':'created','get_path':'/compute/script/{id}','name_field':'Name'}}
        self.deploy.state['edge_bundle_sha256']=hashlib.sha256(b'old bundle').hexdigest()
        self.deploy.save();self.deploy.remember_payload(self.old)
        self.deploy.watchdog_available=Mock(return_value=True)
        receipts=[{'name':x,'sha256':json.loads((self.old/'manifest.json').read_text())['files'][x]} for x in ['0001_catalog.sql','0002_search_fts5.sql']]
        self.sql=Mock();self.sql.query.return_value=receipts
        self.deploy.sql_client=Mock(return_value=self.sql)

    def payload(self,sha,digest,bundle):
        folder=self.root/sha;folder.mkdir();files={}
        (folder/'edge.js').write_text(bundle)
        for name in ['0001_catalog.sql','0002_search_fts5.sql']:shutil.copyfile(ROOT/'bunny-native/migrations'/name,folder/name)
        for p in folder.iterdir():files[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
        (folder/'manifest.json').write_text(json.dumps({'schema':'jacred-bunny-native-payload/v1','source_sha':sha*40,
            'image':{'reference':'ghcr.io/vbrekher/jacred-bunny:sha-'+sha*40,'digest':'sha256:'+digest*64},'files':files}))
        return folder

    def finish(self,result):
        self.deploy.finish_release(result,{'infrastructureReady':True,'worker_source_matches':True})

    def test_update_and_repeat_rollback_preserve_all_ids_and_sql(self):
        ids={k:v['id'] for k,v in self.deploy.state['resources'].items()}
        self.finish(self.deploy.apply_release(self.new))
        self.assertEqual(self.deploy.state['source_sha'],'c'*40)
        self.finish(self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True))
        self.assertEqual(self.deploy.state['source_sha'],'a'*40)
        result=self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True)
        self.assertFalse(result['changed'])
        self.assertEqual({k:v['id'] for k,v in self.deploy.state['resources'].items()},ids)
        self.assertTrue(all('SELECT' in call.args[0] for call in self.sql.query.call_args_list))
        self.assertFalse(any(method=='DELETE' or (method=='POST' and path in ['/mc/apps','/database/v2/databases']) for method,path in self.calls))

    def test_repeat_cli_rollback_preserves_stopped_lifecycle_without_rearming(self):
        import contextlib
        import types
        import cli
        self.deploy.state['edge_release_id']=self.edge_release
        self.deploy.state['release_history']=[{'from_source':'c'*40,'to_source':'a'*40,'rollback':True}]
        self.remote['status']='inactive'
        self.deploy.save()
        self.deploy.renew_window=Mock()
        args=types.SimpleNamespace(state_dir=self.root/'state',apply=True,operation='rollback',release=None,payload_dir=None,source_sha=None)
        with patch('native.installation_lock',return_value=contextlib.nullcontext()),patch('operations.Operations',return_value=self.deploy),\
             patch.object(cli,'account_key',return_value='test-key'),patch.object(cli,'native_health') as health,\
             patch.object(cli,'resolve_native_registry') as registry:
            cli.native_update(args)
        self.deploy.renew_window.assert_not_called()
        health.assert_not_called();registry.assert_not_called()
        self.assertFalse(any(method in ('PATCH','POST','DELETE') for method,path in self.calls))

    def test_private_environment_readback_is_hashed_and_drift_is_rejected(self):
        env=[{'name':'JACRED_CATALOG_BACKEND','value':'bunny-sql'},{'name':'PRIVATE_TEST','value':'dummy-secret-only'}]
        self.remote['containerTemplates'][0]['environmentVariables']=env
        identity=self.deploy.state['resources']['worker']['identity']
        identity['environment_sha256']=native.environment_fingerprint(env)
        self.deploy.verify('worker')
        env[1]['value']='different-test-value'
        with self.assertRaisesRegex(native.NativeError,'private worker configuration') as failure:self.deploy.verify('worker')
        self.assertNotIn('dummy-secret-only',str(failure.exception))
        self.assertNotIn('different-test-value',str(failure.exception))

    def test_sql_authority_cannot_roll_back_to_legacy_payload(self):
        self.deploy.state['catalog_backend']='bunny-sql'
        with self.assertRaisesRegex(native.NativeError,'catalog authority'):
            self.deploy.assert_schema_compatible(self.old)
        self.sql.query.assert_not_called()

    def test_uncertain_patch_is_inspected_and_not_replayed(self):
        self.uncertain=True
        self.finish(self.deploy.apply_release(self.new))
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']),1)
        self.assertEqual(self.deploy.state['source_sha'],'c'*40)

    def test_interruption_after_patch_can_resume_without_second_patch(self):
        self.fail_get_after_patch=True
        with self.assertRaises(native.NativeError):self.deploy.apply_release(self.new)
        self.fail_get_after_patch=False
        self.finish(self.deploy.apply_release(self.new))
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']),1)
        self.assertEqual(self.deploy.state['release_history'][-1]['from_source'],'a'*40)

    def test_budget_stop_can_confirm_pending_image_without_adopting_foreign_worker(self):
        self.fail_get_after_patch=True
        with self.assertRaises(native.NativeError):self.deploy.apply_release(self.new)
        self.deploy.worker_action('stop')
        self.assertIn(('POST','/mc/apps/worker/undeploy'),self.calls)
        self.assertEqual(self.deploy.state['source_sha'],'a'*40)
        self.remote['containerTemplates'][0]['imageName']='foreign-product'
        before=len(self.calls)
        with self.assertRaises(native.NativeError):self.deploy.worker_action('stop')
        self.assertFalse(any(method=='POST' for method,_ in self.calls[before:]))

    def test_unknown_schema_and_changed_owner_block_before_patch(self):
        self.sql.query.return_value.append({'name':'0009_unknown.sql','sha256':'f'*64})
        with self.assertRaisesRegex(native.NativeError,'compatibility'):self.deploy.apply_release(self.new)
        self.assertFalse(any(method=='PATCH' for method,_ in self.calls))
