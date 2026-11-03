import importlib.util
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

P=Path(__file__).resolve().parents[1]/'bunny/native.py'
spec=importlib.util.spec_from_file_location('native_watchdog',P)
n=importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls=[]
        self.remote={'name':'fixture','status':'active'}
        def api(method,path,body=None):
            self.calls.append((method,path))
            if method=='POST':
                self.remote['status']='inactive'
                return None
            return self.remote
        self.api=api
        self.deploy=n.Deployment(self.tmp.name,api)
        self.deploy.initialize('a'*40,'ghcr.io/vbrekher/jacred-bunny:main','sha256:'+'b'*64)

    def test_expiry_stops_only_verified_worker_and_confirms_remote_state(self):
        self.deploy.state['stop_at']=10
        self.deploy.state['watchdog_nonce']='test-nonce'
        self.deploy.state['resources']['worker']={'id':'owned-worker','status':'created','name':'fixture',
            'get_path':'/mc/apps/{id}','name_field':'name'}
        self.deploy.save()
        now=[0]
        def sleep(seconds): now[0]+=seconds
        with patch.object(n,'API',return_value=self.api):
            n.run_watchdog(self.tmp.name,'test-key','test-nonce',sleep=sleep,clock=lambda:now[0])
        self.assertEqual([x for x in self.calls if x[0]=='POST'],[('POST','/mc/apps/owned-worker/undeploy')])
        self.assertEqual(n.read_private(Path(self.tmp.name)/'watchdog.json')['status'],'stopped_confirmed')

    def test_owner_or_nonce_change_cannot_stop_any_resource(self):
        self.deploy.state['watchdog_nonce']='different'
        self.deploy.save()
        with self.assertRaisesRegex(n.NativeError,'token changed'):
            n.run_watchdog(self.tmp.name,'test-key','old-nonce',sleep=lambda _:None)
        self.assertEqual(self.calls,[])

    def test_no_watchdog_blocks_deploy(self):
        with self.assertRaisesRegex(n.NativeError,'No live budget watchdog'):
            self.deploy.worker_action('start')
        self.assertEqual(self.calls,[])

    def test_detached_process_private_handshake_without_account_key_in_manifest(self):
        # No remote resources exist, so this child cannot make network requests.
        self.deploy.api=n.API('test-only-account-key-not-real')
        self.deploy.start_watchdog()
        heartbeat=n.read_private(Path(self.tmp.name)/'watchdog.json')
        pid=heartbeat['pid']
        try:
            self.assertTrue(self.deploy.watchdog_available())
            self.assertIn('authority_intent_stop',heartbeat['capabilities'])
            self.assertNotIn('test-only-account-key',self.deploy.path.read_text())
            self.deploy.start_watchdog()
            self.assertEqual(n.read_private(Path(self.tmp.name)/'watchdog.json')['pid'],pid)
        finally:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:os.waitpid(pid,0)
            except ChildProcessError:pass


if __name__=='__main__':unittest.main()
