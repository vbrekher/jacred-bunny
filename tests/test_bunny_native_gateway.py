"""Control-plane gateway selection; mocks are not Bunny live acceptance."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bunny'))
import native

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.payload=self.root/'payload';self.payload.mkdir()
        files={}
        for name in ['edge.js',*native.SQL_RUNTIME_MIGRATIONS]:
            (self.payload/name).write_text('fixture-only')
            files[name]=hashlib.sha256((self.payload/name).read_bytes()).hexdigest()
        self.manifest={'schema':'jacred-bunny-native-payload/v2','catalog_backend':'bunny-sql','edge_mode':'original-api-gateway','source_sha':'a'*40,'image':{'reference':'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,'digest':'sha256:'+'b'*64},'files':files}
        (self.payload/'manifest.json').write_text(json.dumps(self.manifest))
        self.api=Mock(return_value={})
        self.deploy=native.Deployment(self.root/'state',self.api);self.deploy.initialize('a'*40,'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,'sha256:'+'b'*64,1)
        self.deploy.state.update(catalog_backend='bunny-sql',resources={'database':{'id':'db'},'edge':{'id':1},'worker':{'id':'worker'}});self.deploy.save()
        native.atomic_private(self.deploy.secret_path,{'api_key':'fixture-api','admin_key':'do-not-inject-admin','database_read_token':'fixture-read'})
        self.worker={'displayEndpoint':{'type':'cdn','address':'mc-abc123.bunny.run'}}
        self.deploy.verify=Mock(side_effect=lambda kind:{'worker':self.worker,'database':{'url':'libsql://fixture.test'},'edge':{'CurrentReleaseId':5,'DefaultHostname':'edge.test'}}[kind])
    def wire(self):self.deploy.wire_edge(self.payload/'edge.js',self.manifest['files']['edge.js'])
    def test_owned_origin_is_configured_without_admin_injection_and_repeat_is_noop(self):
        self.wire()
        secrets={call.args[2]['Name']:call.args[2]['Secret'] for call in self.api.call_args_list if call.args[0]=='PUT'}
        self.assertEqual(secrets['JACRED_WORKER_ORIGIN'],'https://mc-abc123.bunny.run')
        self.assertEqual(secrets['JACRED_EDGE_GATEWAY_ENABLED'],'true')
        self.assertNotIn('do-not-inject-admin',json.dumps(secrets))
        self.assertEqual(self.deploy.state['edge_mode'],'original-api-gateway')
        self.api.reset_mock();self.wire();self.api.assert_not_called()
    def test_legacy_authority_is_rejected_before_provider_mutation(self):
        self.deploy.state['catalog_backend']='filedb';self.deploy.save()
        with self.assertRaisesRegex(native.NativeError,'SQL authority'):self.wire()
        self.api.assert_not_called()
    def test_unexpected_worker_origin_is_rejected_before_provider_mutation(self):
        self.worker['displayEndpoint']['address']='https://evil.test'
        with self.assertRaisesRegex(native.NativeError,'owned HTTPS'):self.wire()
        self.api.assert_not_called()
    def test_unknown_mode_or_legacy_gateway_payload_is_invalid(self):
        self.manifest['edge_mode']='typo'
        with self.assertRaises(native.NativeError):native.payload_spec(self.manifest)
        self.manifest['edge_mode']='original-api-gateway';self.manifest.pop('catalog_backend')
        with self.assertRaises(native.NativeError):native.payload_spec(self.manifest)
