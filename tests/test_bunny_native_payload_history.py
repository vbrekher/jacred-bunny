"""Immutable configuration identity and crash/compensation history regressions."""
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_bunny_native_operations as fixture
import native

class PayloadHistoryTests(unittest.TestCase):
    payload=fixture.OperationTests.payload
    finish=fixture.OperationTests.finish
    def setUp(self):
        fixture.OperationTests.setUp(self)
        self.deploy.state['catalog_backend']='bunny-sql';self.deploy.state['edge_mode']='preview'
        self.remote['displayEndpoint']={'type':'cdn','address':'mc-fixture.bunny.run'}
        for directory in (self.old,self.new):
            name='0004_catalog_commits.sql';shutil.copyfile(fixture.ROOT/'bunny-native/migrations'/name,directory/name)
            manifest=json.loads((directory/'manifest.json').read_text());manifest.update(schema='jacred-bunny-native-payload/v2',catalog_backend='bunny-sql',edge_mode='preview')
            manifest['files'][name]=hashlib.sha256((directory/name).read_bytes()).hexdigest()
            (directory/'manifest.json').write_text(json.dumps(manifest))
        self.sql.query.return_value=[{'name':name,'sha256':digest} for name,digest in json.loads((self.old/'manifest.json').read_text())['files'].items() if name.endswith('.sql')]
        self.deploy.save();self.original=self.deploy.remember_payload(self.old)
        self.gateway=self.root/'gateway';shutil.copytree(self.old,self.gateway)
        m=json.loads((self.gateway/'manifest.json').read_text());m['edge_mode']='original-api-gateway';(self.gateway/'manifest.json').write_text(json.dumps(m))

    def test_mode_only_update_preserves_old_payload_and_repeat_rollback_is_noop(self):
        before=(self.original/'manifest.json').read_bytes()
        result=self.deploy.apply_release(self.gateway);self.finish(result)
        self.assertTrue(result['changed']);self.assertEqual(result['from_source'],result['to_source'])
        self.assertNotEqual(result['from_payload_id'],result['to_payload_id'])
        self.assertEqual(self.deploy.state['edge_mode'],'original-api-gateway')
        self.assertEqual((self.original/'manifest.json').read_bytes(),before)
        self.assertEqual(self.deploy.rollback_payload(),self.original)
        self.finish(self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True))
        self.assertEqual(self.deploy.state['edge_mode'],'preview')
        self.assertFalse(self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True)['changed'])
        self.assertFalse(any(method=='PATCH' for method,_ in self.calls))

    def test_failure_after_mode_publish_compensates_from_exact_old_manifest(self):
        original=self.deploy.wire_edge
        def interrupted(*args,**kwargs):original(*args,**kwargs);raise native.NativeError('simulated post-publish interruption')
        with patch.object(self.deploy,'wire_edge',side_effect=interrupted):
            with self.assertRaises(native.NativeError):self.deploy.apply_release(self.gateway)
        self.assertEqual(self.deploy.state['edge_mode'],'original-api-gateway')
        self.assertEqual(self.deploy.rollback_payload(),self.original)
        self.finish(self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True))
        self.assertEqual(self.deploy.state['edge_mode'],'preview')
        self.assertFalse(self.deploy.state.get('release_history'))

    def test_pending_mode_release_can_resume_without_losing_previous_payload(self):
        original=self.deploy.wire_edge
        with patch.object(self.deploy,'wire_edge',side_effect=native.NativeError('simulated publish failure')):
            with self.assertRaises(native.NativeError):self.deploy.apply_release(self.gateway)
        self.assertEqual(self.deploy.rollback_payload(),self.original)
        result=self.deploy.apply_release(self.gateway);self.finish(result)
        self.assertEqual(self.deploy.rollback_payload(),self.original)
        self.assertNotIn('pending_update',self.deploy.state)

    def test_legacy_source_cache_is_migrated_without_overwrite(self):
        destination=self.deploy.directory/'releases'/('a'*40);destination.parent.mkdir();shutil.copytree(self.old,destination)
        root=self.deploy.directory/'payload-objects';shutil.rmtree(root)
        found=self.deploy.capture_current_payload()
        self.assertEqual(found.parent.name,'payload-objects')
        self.assertEqual(native.load_payload(destination)['edge_mode'],'preview')
        self.assertEqual(native.load_payload(found),native.load_payload(destination))

    def test_corrupt_or_symlinked_object_cannot_be_used_for_rollback(self):
        identity=self.deploy.payload_id(native.load_payload(self.original))
        (self.original/'edge.js').write_text('tampered')
        with self.assertRaises(native.NativeError):self.deploy.cached_payload(identity)
        with self.assertRaises(native.NativeError):self.deploy.cached_payload('../foreign')

    def test_cli_mode_only_update_is_not_mistaken_for_repeat_noop(self):
        import cli,types,contextlib
        self.deploy.state['edge_release_id']=self.edge_release;self.deploy.save()
        args=types.SimpleNamespace(state_dir=self.root/'state',apply=True,operation='update',release=None,
            payload_dir=self.gateway,source_sha=None,registry_id=None,hours=.1,max_estimated_usd=2,keep_on_error=True)
        with patch('native.installation_lock',return_value=contextlib.nullcontext()),patch('operations.Operations',return_value=self.deploy),\
             patch.object(cli,'account_key',return_value='test-key'),patch.object(cli,'resolve_native_registry'),\
             patch.object(self.deploy,'bound_staging_cdn'),patch.object(cli,'native_health',return_value={'infrastructureReady':True,'worker_source_matches':True}) as health:
            cli.native_update(args)
        health.assert_called_once()
        self.assertEqual(self.deploy.state['edge_mode'],'original-api-gateway')
        self.assertNotEqual(self.deploy.state['release_history'][-1]['from_payload_id'],self.deploy.state['active_payload_id'])

    def test_partial_secret_write_cannot_make_compensation_skip_republish(self):
        original=self.deploy.api;enabled=[];failed=False
        def api(method,path,body=None):
            nonlocal failed
            result=original(method,path,body)
            if method=='PUT' and isinstance(body,dict) and body.get('Name')=='JACRED_EDGE_GATEWAY_ENABLED':
                enabled.append(body['Secret'])
                if not failed:
                    failed=True
                    raise native.NativeError('simulated lost response after secret write')
            return result
        self.deploy.api=api
        with self.assertRaises(native.NativeError):self.deploy.apply_release(self.gateway)
        self.assertEqual(self.deploy.state['edge_mode'],'preview')
        self.finish(self.deploy.apply_release(self.deploy.rollback_payload(),rollback=True))
        self.assertEqual(enabled,['true','false'])
        self.assertEqual(self.edge_release,2)
        self.assertEqual(self.deploy.state['edge_mode'],'preview')
