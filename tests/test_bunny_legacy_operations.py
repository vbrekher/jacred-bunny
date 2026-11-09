"""Owned lifecycle state-machine tests with fake API; not live Bunny acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'bunny'))
from legacy import LegacyExports, verify_legacy_archive
from native import NativeError, atomic_private, read_private
import test_bunny_legacy_archive as fixtures


class LegacyOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.patch_lost = False
        self.patch_applies = True
        self.deploy = LegacyExports(self.root/'state', self.api)
        self.deploy.initialize('a'*40, 'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40, 'sha256:'+'b'*64, hours=.1)
        self.environment = [{'name': 'JACRED_BUNNY_CRON', 'value': '0'},
            {'name': 'JACRED_BUNNY_DEV_KEY', 'value': read_private(self.deploy.secret_path)['admin_key']}]
        container = {'id': 'container', 'name': 'jacred', 'imageNamespace': 'vbrekher', 'imageName': 'jacred-bunny',
            'imageTag': 'sha-'+'a'*40, 'imageDigest': 'sha256:'+'b'*64, 'imageRegistryId': '1156',
            'volumeMounts': [{'name': 'data', 'mountPath': '/app/Data'}], 'environmentVariables': deepcopy(self.environment)}
        identity = {'container': {key: container[key] for key in ['name','imageNamespace','imageName','imageTag','imageDigest','imageRegistryId']},
            'region': 'AMS', 'volumeMounts': container['volumeMounts'], 'volumes': [{'name':'data','size':1}]}
        self.remote = {'name': self.deploy.state['name'], 'autoScaling': {'min':1,'max':1},
            'regionSettings': {'requiredRegionIds':['AMS'],'allowedRegionIds':['AMS']},
            'containerTemplates': [container], 'volumes': identity['volumes'], 'status':'active'}
        self.deploy.state['resources'] = {'worker': {'id':'worker', 'name': self.remote['name'], 'status':'created',
            'get_path':'/mc/apps/{id}', 'name_field':'name', 'identity': identity}}
        self.deploy.save()
        self.deploy.watchdog_available = Mock(return_value=True)
        atomic_private(self.deploy.directory/'watchdog.json', {'capabilities':['authority_intent_stop','legacy_export_stop']})
        self.overview = {'status':'progressing','desiredInstances':1,'activeInstances':{'indicator':0},'regions':[]}
        self.proof = {'source_sha':'a'*40, 'boot_id':'c'*32}
        self.deploy._normal_proof = Mock(return_value=self.proof)
        self.deploy._download_export = Mock(side_effect=self.download)
        self.fixture = fixtures.LegacyArchiveTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if path == '/mc/apps/worker': return deepcopy(self.remote)
        if path == '/mc/limits': return {}
        if path == '/mc/apps/worker/overview': return deepcopy(self.overview)
        if method == 'PATCH':
            if self.patch_applies: self.remote['containerTemplates'][0].update(deepcopy(body))
            if self.patch_lost: raise NativeError('uncertain')
            return None
        if path == '/mc/apps/worker/undeploy': self.remote['status']='inactive'; return None
        if path == '/mc/apps/worker/deploy': self.remote['status']='active'; return None
        raise AssertionError('Unrecognized fake API request')

    def download(self):
        digest = self.fixture.checkpoint()
        pending = self.deploy.state['pending_legacy_export']
        directory = self.deploy._export_directory(pending['nonce'])
        output = directory/'legacy.tar.gz'
        shutil.copyfile(self.fixture.path, output); output.chmod(0o600)
        receipt = verify_legacy_archive(output, digest, pending['source_sha'], pending['boot_id'])
        atomic_private(directory/'receipt.json', receipt)
        return receipt

    def test_export_preserves_spec_volumes_source_and_private_boundaries(self):
        before = deepcopy(self.remote)
        result = self.deploy.export_legacy()
        self.assertEqual(result['status'], 'PRIVATE_ARCHIVE_VERIFIED')
        self.assertTrue(result['receipt']['shutdown_checkpoint_verified'])
        self.assertTrue(result['compute_stopped'])
        self.assertEqual(self.remote, dict(before, status='inactive'))
        self.assertEqual(self.deploy.state['source_sha'], 'a'*40)
        self.assertNotIn('pending_legacy_export', self.deploy.state)
        self.assertNotIn(self.environment[1]['value'], self.deploy.path.read_text())
        directory = self.deploy.directory/'legacy-exports'/result['export_id']
        self.assertEqual((directory/'environment.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory/'legacy.tar.gz').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']), 2)

    def test_applied_patch_with_lost_response_is_read_back_not_replayed(self):
        self.patch_lost = True
        result = self.deploy.export_legacy()
        self.assertEqual(result['status'], 'PRIVATE_ARCHIVE_VERIFIED')
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']), 2)

    def test_unapplied_uncertain_patch_is_stopped_and_compensated_without_replay(self):
        self.patch_applies = False; self.patch_lost = True
        with self.assertRaises(NativeError): self.deploy.export_legacy()
        self.assertEqual(self.remote['status'], 'inactive')
        self.assertEqual(self.remote['containerTemplates'][0]['environmentVariables'], self.environment)
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']), 1)
        self.deploy._download_export.assert_not_called()
        recovered = self.deploy.export_legacy()
        self.assertEqual(recovered['status'], 'RECOVERED_RETRY_REQUIRED')
        self.assertIsNone(recovered['receipt'])
        self.assertEqual(len([x for x in self.calls if x[0]=='PATCH']), 1)

    def test_download_failure_restores_normal_environment_and_stops_compute(self):
        self.deploy._download_export.side_effect = NativeError('bad checksum')
        with self.assertRaises(NativeError): self.deploy.export_legacy()
        self.assertEqual(self.remote['status'], 'inactive')
        self.assertEqual(self.remote['containerTemplates'][0]['environmentVariables'], self.environment)
        self.assertEqual(self.deploy.state['pending_legacy_export']['phase'], 'normal_environment_restored')

    def test_pending_preparation_is_recovered_without_starting_or_exporting(self):
        self.deploy.prepare_legacy_export(self.proof)
        result = self.deploy.export_legacy()
        self.assertEqual(result['status'], 'RECOVERED_RETRY_REQUIRED')
        self.assertNotIn(('POST','/mc/apps/worker/deploy'), self.calls)
        self.assertNotIn('pending_legacy_export', self.deploy.state)

    def test_stop_reconciles_known_target_after_crash_between_patch_and_journal(self):
        self.deploy.prepare_legacy_export(self.proof)
        pending = self.deploy.state['pending_legacy_export']
        private = read_private(self.deploy._export_directory(pending['nonce'])/'environment.json')
        self.remote['containerTemplates'][0]['environmentVariables'] = private['target']
        self.remote['status'] = 'active'
        pending['phase'] = 'export_patch_intent'; self.deploy.save()
        result = self.deploy.export_legacy()
        self.assertEqual(result['status'], 'RECOVERED_RETRY_REQUIRED')
        self.assertEqual(self.remote['containerTemplates'][0]['environmentVariables'], self.environment)
        self.assertEqual(self.remote['status'], 'inactive')

    def test_unknown_environment_or_volumes_never_get_stopped(self):
        for field in ('environment', 'volumes'):
            with self.subTest(field=field):
                self.deploy.prepare_legacy_export(self.proof)
                original = deepcopy(self.remote)
                if field=='environment': self.remote['containerTemplates'][0]['environmentVariables'].append({'name':'unrecorded','value':'change'})
                else: self.remote['volumes'] = [{'name':'foreign','size':1}]
                before = len(self.calls)
                with self.assertRaises(NativeError): self.deploy.worker_action('stop')
                self.assertNotIn(('POST','/mc/apps/worker/undeploy'), self.calls[before:])
                self.remote = original
                self.deploy.export_legacy()
                self.remote['status']='active'

    def test_ordinary_start_refuses_pending_export(self):
        self.deploy.prepare_legacy_export(self.proof)
        with self.assertRaises(NativeError): self.deploy.worker_action('start')
        with self.assertRaises(NativeError): self.deploy.worker_action('start', allow_legacy_export=True)
        self.assertNotIn(('POST','/mc/apps/worker/deploy'), self.calls)

    def test_sql_authority_and_expired_watchdog_are_not_exported(self):
        self.deploy.state['catalog_backend']='bunny-sql'; self.deploy.save()
        with self.assertRaises(NativeError): self.deploy.prepare_legacy_export(self.proof)
        self.deploy.state['catalog_backend']='filedb'; self.deploy.save()
        self.deploy.watchdog_available.return_value=False
        with self.assertRaises(NativeError): self.deploy.prepare_legacy_export(self.proof)
        self.assertFalse((self.deploy.directory/'legacy-exports').exists())

    def test_tampered_private_environment_cannot_be_applied(self):
        self.deploy.prepare_legacy_export(self.proof)
        self.deploy.worker_action('stop')
        pending = self.deploy.state['pending_legacy_export']
        path = self.deploy._export_directory(pending['nonce'])/'environment.json'
        private = read_private(path); private['target'].append({'name':'unrecorded','value':'change'}); atomic_private(path, private)
        with self.assertRaises(NativeError): self.deploy.configure_legacy_export()
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_dry_run_does_not_read_account_key_or_make_requests(self):
        import argparse, cli
        with patch.object(cli,'account_key',side_effect=AssertionError('must not read key')), contextlib.redirect_stdout(io.StringIO()) as output:
            cli.native_legacy_export(argparse.Namespace(apply=False))
        self.assertFalse(json.loads(output.getvalue())['apply'])

    def test_old_watchdog_cannot_authorize_a_transient_environment(self):
        atomic_private(self.deploy.directory/'watchdog.json', {'capabilities':['authority_intent_stop']})
        with self.assertRaises(NativeError): self.deploy.prepare_legacy_export(self.proof)
        self.assertNotIn('pending_legacy_export', self.deploy.state)
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_account_credential_is_never_retransmitted_to_a_worker(self):
        self.deploy.api = Mock(side_effect=self.api)
        self.deploy.api.key = self.environment[1]['value']
        with self.assertRaises(NativeError): self.deploy.prepare_legacy_export(self.proof)
        self.assertFalse(any(method=='PATCH' for method,path in self.calls))

    def test_cli_rejects_sql_authority_before_rates_watchdog_or_cloud_calls(self):
        import argparse, cli
        self.deploy.state['catalog_backend'] = 'bunny-sql'; self.deploy.save()
        before = list(self.calls)
        with patch('legacy.LegacyExports', return_value=self.deploy), patch.object(cli, 'account_key', return_value='unused-local-root'), \
             patch('native.staging_cost_plan', side_effect=AssertionError('must not estimate or renew')):
            with self.assertRaises(NativeError):
                cli.native_legacy_export(argparse.Namespace(apply=True, state_dir=self.deploy.directory))
        self.assertEqual(self.calls, before)

    def test_cli_apply_runs_bounded_export_using_the_existing_price_guard(self):
        import argparse, cli
        self.deploy.bound_staging_cdn = Mock()
        with patch('legacy.LegacyExports', return_value=self.deploy), patch.object(cli, 'account_key', return_value='unused-local-root'), \
             patch('native.current_pricing', return_value={}), patch('native.staging_cost_plan', return_value={'estimated_initial_usd':.1}) as cost, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.native_legacy_export(argparse.Namespace(apply=True, state_dir=self.deploy.directory, hours=1, max_estimated_usd=2))
        self.assertEqual(cost.call_args.args[1], .15)
        self.assertEqual(json.loads(output.getvalue())['status'], 'PRIVATE_ARCHIVE_VERIFIED')
        self.assertEqual(self.remote['status'], 'inactive')

    def test_readiness_waits_for_control_plane_before_accepting_http(self):
        self.remote['status'] = 'progressing'
        def activate(_):
            self.deploy._normal_proof.assert_not_called()
            self.remote['status'] = 'active'
        before = self.deploy.state['stop_at']
        with patch('legacy.time.sleep', side_effect=activate):
            self.assertEqual(self.deploy.wait_normal_bridge(), self.proof)
        self.assertEqual(self.deploy.state['stop_at'], before)
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))

    def test_http_success_during_control_plane_regression_does_not_authorize_export(self):
        self.deploy.verify = Mock(side_effect=[{'status':'active'}, {'status':'progressing'},
            {'status':'active'}, {'status':'active'}])
        with patch('legacy.time.sleep') as sleep:
            self.assertEqual(self.deploy.wait_normal_bridge(), self.proof)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(self.deploy._normal_proof.call_count, 2)
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))

    def test_transition_timeout_and_small_window_refuse_without_mutation(self):
        self.remote['status'] = 'progressing'
        with self.assertRaisesRegex(NativeError, 'control plane and HTTP'):
            self.deploy.wait_normal_bridge(timeout=0)
        self.deploy._normal_proof.assert_not_called()
        self.deploy.state['stop_at'] = 0
        with self.assertRaisesRegex(NativeError, 'original window'):
            self.deploy.wait_normal_bridge()
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))

    def test_cli_does_not_redeploy_a_transitioning_normal_bridge(self):
        import argparse, cli
        self.remote['status'] = 'progressing'
        self.deploy.bound_staging_cdn = Mock()
        def activate(_): self.remote['status'] = 'active'
        with patch('legacy.LegacyExports', return_value=self.deploy), patch.object(cli,'account_key',return_value='unused-local-root'), \
             patch('native.current_pricing', return_value={}), patch('native.staging_cost_plan', return_value={'estimated_initial_usd':.1}), \
             patch('legacy.time.sleep', side_effect=activate), contextlib.redirect_stdout(io.StringIO()):
            cli.native_legacy_export(argparse.Namespace(apply=True,state_dir=self.deploy.directory,hours=.15,max_estimated_usd=2))
        # Only the fenced exporter is deployed, never a second normal rollout.
        self.assertEqual(self.calls.count(('POST','/mc/apps/worker/deploy')), 1)
        self.assertEqual(self.remote['status'], 'inactive')

    def ready_progressing_fixture(self):
        self.remote['status'] = 'progressing'
        self.remote['containerInstances'] = [{'podId':'pod-one','templateId':'container'}]
        self.overview = {'status':'progressing','desiredInstances':1,'activeInstances':{'indicator':1},
            'regions':[{'region':'AMS','status':'progressing','instances':1,'pods':[
                {'name':'pod-one','status':'ready','message':'','lastHeartBeat':datetime.now(timezone.utc).isoformat(),'containers':[
                    {'id':'container','name':'jacred','status':'ready','reason':'','message':'','exitCode':None}]}]}]}

    def test_progressing_grade_requires_exact_owned_ready_pod_before_export(self):
        self.ready_progressing_fixture()
        self.assertTrue(self.deploy.normal_worker_ready())
        self.assertEqual(self.deploy.wait_normal_bridge(timeout=0), self.proof)
        self.deploy.prepare_legacy_export(self.proof)
        self.assertEqual(self.deploy.state['pending_legacy_export']['phase'], 'prepared')
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))

    def test_progressing_proof_rejects_missing_extra_foreign_or_unready_instances(self):
        cases = [
            lambda: self.remote.update(containerInstances=[]),
            lambda: self.remote['containerInstances'].append({'podId':'extra','templateId':'container'}),
            lambda: self.remote['containerInstances'][0].update(podId='foreign'),
            lambda: self.remote['containerInstances'][0].update(templateId='foreign'),
            lambda: self.overview.update(desiredInstances=2),
            lambda: self.overview.update(desiredInstances=True),
            lambda: self.overview['activeInstances'].update(indicator=0),
            lambda: self.overview['regions'][0].update(region='FRA'),
            lambda: self.overview['regions'][0]['pods'].append(deepcopy(self.overview['regions'][0]['pods'][0])),
            lambda: self.overview['regions'][0]['pods'][0].update(status='scheduled'),
            lambda: self.overview['regions'][0]['pods'][0].update(lastHeartBeat=None),
            lambda: self.overview['regions'][0]['pods'][0].update(lastHeartBeat='2000-01-01T00:00:00Z'),
            lambda: self.overview['regions'][0]['pods'][0].update(lastHeartBeat='2099-01-01T00:00:00Z'),
            lambda: self.overview['regions'][0]['pods'][0]['containers'][0].update(id='foreign'),
            lambda: self.overview['regions'][0]['pods'][0]['containers'][0].update(status='waiting'),
            lambda: self.overview['regions'][0]['pods'][0]['containers'][0].update(reason='CrashLoopBackOff'),
            lambda: self.overview['regions'][0]['pods'][0]['containers'][0].update(message='error'),
            lambda: self.overview['regions'][0]['pods'][0]['containers'][0].update(exitCode=137),
        ]
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                self.ready_progressing_fixture(); mutate()
                self.assertFalse(self.deploy.normal_worker_ready())
                with self.assertRaises(NativeError): self.deploy.prepare_legacy_export(self.proof)
                self.assertNotIn('pending_legacy_export', self.deploy.state)
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))

    def test_cli_does_not_stop_an_active_pre_bridge_source_for_watcher_upgrade(self):
        import argparse, cli
        self.deploy.bound_staging_cdn = Mock()
        self.deploy._normal_proof.side_effect = NativeError('pre-bridge source')
        atomic_private(self.deploy.directory/'watchdog.json', {'capabilities':['authority_intent_stop']})
        with patch('legacy.LegacyExports', return_value=self.deploy), patch.object(cli,'account_key',return_value='unused-local-root'), \
             patch('native.current_pricing', return_value={}), patch('native.staging_cost_plan', return_value={'estimated_initial_usd':.1}):
            with self.assertRaises(NativeError):
                cli.native_legacy_export(argparse.Namespace(apply=True,state_dir=self.deploy.directory,hours=1,max_estimated_usd=2))
        self.assertEqual(self.remote['status'], 'active')
        self.assertFalse(any(method in ('POST','PATCH') for method,path in self.calls))
