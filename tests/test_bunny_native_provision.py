"""Control-plane safety tests; these mocks never establish live acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

P = Path(__file__).resolve().parents[1] / 'bunny' / 'native.py'
spec = importlib.util.spec_from_file_location('native', P)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.remote = {}
        self.sequence = 0
        self.uncertain = False

        def api(method, path, body=None):
            self.calls.append((method, path, body))
            if method == 'POST':
                self.sequence += 1
                if self.uncertain:
                    raise native.NativeError('HTTP 500')
                if 'database' in path:
                    self.remote['/database/v2/databases/db_test'] = {'db': {'name': body['name'], 'url': 'libsql://fixture.invalid'}}
                    return {'db_id': 'db_test'}
                if path == '/storagezone':
                    self.remote['/storagezone/1'] = {'Name': body['Name']}
                    return {'Id': 1}
                if path == '/compute/script':
                    self.remote['/compute/script/2'] = {'Name': body['Name'], 'DefaultHostname': 'https://fixture.invalid'}
                    return {'Id': 2}
                return None
            if method == 'GET':
                return self.remote[path]
            return {'token': 'test-only-token'}

        self.api = api
        self.deploy = native.Deployment(self.tmp.name, api)
        self.deploy.initialize('a' * 40, 'ghcr.io/vbrekher/jacred-bunny:main', 'sha256:' + 'b' * 64)

    def test_rerun_does_not_duplicate_resources(self):
        first = self.deploy.provision_data_plane()
        second = native.Deployment(self.tmp.name, self.api).provision_data_plane()
        self.assertEqual(first, second)
        self.assertEqual(len([x for x in self.calls if x[0] == 'POST']), 3)
        self.assertFalse(self.deploy.status()['production_ready'])

    def test_ambiguous_post_is_never_replayed(self):
        self.uncertain = True
        with self.assertRaises(native.NativeError):
            self.deploy.provision_data_plane()
        self.uncertain = False
        with self.assertRaisesRegex(native.NativeError, 'Uncertain'):
            native.Deployment(self.tmp.name, self.api).provision_data_plane()
        self.assertEqual(len([x for x in self.calls if x[0] == 'POST']), 1)

    def test_changed_identity_blocks_all_subsequent_creates(self):
        self.deploy.provision_data_plane()
        self.calls.clear()
        self.remote['/database/v2/databases/db_test']['db']['name'] = 'another-product'
        with self.assertRaisesRegex(native.NativeError, 'identity changed'):
            self.deploy.provision_data_plane()
        self.assertEqual([x[0] for x in self.calls], ['GET'])

    def test_manifest_has_no_secrets(self):
        data = native.read_private(self.deploy.path)
        secrets = native.read_private(self.deploy.secret_path)
        text = json.dumps(data)
        for value in secrets.values():
            self.assertNotIn(value, text)
        self.assertNotEqual(secrets['api_key'], secrets['admin_key'])
        self.assertEqual(os.stat(self.deploy.path).st_mode & 0o777, 0o600)

    def test_wrong_owner_fails_closed(self):
        state = native.read_private(self.deploy.path)
        state['owner'] = 'someone/else'
        native.atomic_private(self.deploy.path, state)
        with self.assertRaisesRegex(native.NativeError, 'owner mismatch'):
            native.Deployment(self.tmp.name, self.api)

    def test_bundle_checksum_checked_before_any_api_call(self):
        bundle = Path(self.tmp.name) / 'edge.js'
        bundle.write_text('fake-test-only')
        self.calls.clear()
        with self.assertRaisesRegex(native.NativeError, 'checksum'):
            self.deploy.wire_edge(bundle, 'f' * 64)
        self.assertEqual(self.calls, [])

    def test_concurrent_command_does_not_mutate_resources(self):
        import fcntl
        lock_path = Path(self.tmp.name) / '.deployment.lock'
        with open(lock_path, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(native.NativeError, 'Another deployment'):
                self.deploy.provision_data_plane()
        self.assertEqual(self.calls, [])

    def test_trigger_migration_parser(self):
        sql = "CREATE TABLE t(v TEXT); CREATE TRIGGER tr AFTER INSERT ON t BEGIN UPDATE t SET v='a;b'; SELECT 1; END; -- trailing comment"
        statements = list(native.sql_statements(sql))
        self.assertEqual(len(statements), 2)
        self.assertIn("SELECT 1; END;", statements[1])
        with self.assertRaises(native.NativeError):
            list(native.sql_statements('CREATE TABLE partial('))

    def test_symlink_and_public_state_rejected(self):
        path = Path(self.tmp.name) / 'link'
        path.symlink_to(self.deploy.path)
        for fn, args in [(native.atomic_private, (path, {})), (native.read_private, (path,))]:
            with self.assertRaises(native.NativeError):
                fn(*args)
        os.chmod(self.deploy.path, 0o644)
        with self.assertRaises(native.NativeError):
            native.read_private(self.deploy.path)


if __name__ == '__main__':
    unittest.main()
