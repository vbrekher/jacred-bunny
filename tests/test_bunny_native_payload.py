import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

P = Path(__file__).resolve().parents[1]/'bunny/native.py'
spec = importlib.util.spec_from_file_location('native_payload', P)
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        files = {}
        for name in ['edge.js', '0001_catalog.sql', '0002_search_fts5.sql']:
            (self.root/name).write_text('test-only contents')
            files[name] = hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        self.manifest = {'schema':'jacred-bunny-native-payload/v1','source_sha':'a'*40,
                         'image':{'reference':'ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,'digest':'sha256:'+'b'*64},'files':files}
        self.save()

    def save(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))

    def test_valid_payload_and_corruption(self):
        self.assertEqual(n.load_payload(self.root), self.manifest)
        (self.root/'edge.js').write_text('tampered')
        with self.assertRaisesRegex(n.NativeError, 'checksum'):
            n.load_payload(self.root)

    def test_external_image_or_wrong_source_rejected(self):
        for image in ['ghcr.io/another-owner/jacred-bunny:sha-'+'a'*40, 'ghcr.io/vbrekher/jacred-bunny:main']:
            self.manifest['image']['reference'] = image
            self.save()
            with self.assertRaises(n.NativeError):
                n.load_payload(self.root)

    def test_symlink_and_traversal_files_rejected(self):
        (self.root/'edge.js').unlink()
        (self.root/'edge.js').symlink_to(self.root/'0001_catalog.sql')
        with self.assertRaises(n.NativeError):
            n.load_payload(self.root)
        self.manifest['files']['../elsewhere'] = 'a'*64
        self.save()
        with self.assertRaises(n.NativeError):
            n.load_payload(self.root)

    def test_cli_corrupted_payload_fails_before_credentials_or_paid_api(self):
        import argparse
        from unittest.mock import patch
        cli_spec=importlib.util.spec_from_file_location('payload_cli',P.parent/'cli.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        (self.root/'edge.js').write_text('corrupted')
        args=argparse.Namespace(apply=True,payload_dir=self.root,release=None,state_dir=self.root/'state')
        with patch.dict('sys.modules',{'native':n}), patch.object(cli,'account_key',side_effect=AssertionError('must not request credentials')):
            with self.assertRaisesRegex(n.NativeError,'checksum'):
                cli.native_install(args)

    def test_public_registry_is_discovered_without_pat_or_registry_creation(self):
        from unittest.mock import patch
        cli_spec=importlib.util.spec_from_file_location('registry_cli',P.parent/'cli.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        with patch.object(cli,'request',side_effect=[{'items':[
            {'hostName':'ghcr.io','id':1,'displayName':'Private connection'},
            {'hostName':'ghcr.io','id':2,'displayName':'GitHub Public'}]}, {'digest':'sha256:'+'b'*64}]) as req:
            rid,digest=cli.resolve_native_registry('test-key',self.manifest['image']['reference'],'sha256:'+'b'*64)
        self.assertEqual(rid,'2')
        self.assertEqual(req.call_args_list[1].args[1],'/mc/registries/digest')
        self.assertEqual(req.call_args_list[1].args[3]['registryId'],'2')

    def test_packager_produces_manifest_accepted_by_installer(self):
        package_spec=importlib.util.spec_from_file_location('package_tool',P.parents[1]/'tools/bunny_native_package.py')
        pkg=importlib.util.module_from_spec(package_spec);package_spec.loader.exec_module(pkg)
        root=self.root/'source';(root/'bunny-native/edge/dist').mkdir(parents=True)
        (root/'bunny-native/migrations').mkdir(parents=True)
        (root/'bunny-native/edge/dist/index.js').write_text('test-only bundle')
        for name in ['0001_catalog.sql','0002_search_fts5.sql']:(root/'bunny-native/migrations'/name).write_text('SELECT 1;')
        out=self.root/'packaged'
        manifest=pkg.package(root,out,'a'*40,'sha256:'+'b'*64)
        self.assertEqual(n.load_payload(out),manifest)

    def test_sql_payload_requires_exact_runtime_migrations_and_backend(self):
        name='0004_catalog_commits.sql'
        (self.root/name).write_text('SELECT 1;')
        self.manifest['files'][name]=hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        self.manifest.update(schema='jacred-bunny-native-payload/v2',catalog_backend='bunny-sql')
        self.save();self.assertEqual(n.load_payload(self.root),self.manifest)
        self.manifest['catalog_backend']='filedb';self.save()
        with self.assertRaises(n.NativeError):n.load_payload(self.root)

    def test_sql_packager_roundtrip_excludes_optional_shard_migration(self):
        package_spec=importlib.util.spec_from_file_location('sql_package_tool',P.parents[1]/'tools/bunny_native_package.py')
        pkg=importlib.util.module_from_spec(package_spec);package_spec.loader.exec_module(pkg)
        source=P.parents[1]
        # Use a private dummy bundle; all three actual SQL migration files are copied.
        from unittest.mock import patch
        import shutil
        root=self.root/'source';(root/'bunny-native/edge/dist').mkdir(parents=True)
        (root/'bunny-native/migrations').mkdir(parents=True)
        (root/'bunny-native/edge/dist/index.js').write_text('test-only bundle')
        for name in n.SQL_RUNTIME_MIGRATIONS:shutil.copyfile(source/'bunny-native/migrations'/name,root/'bunny-native/migrations'/name)
        out=self.root/'sql-payload';manifest=pkg.package(root,out,'a'*40,'sha256:'+'b'*64,'bunny-sql')
        self.assertEqual(n.load_payload(out),manifest)
        self.assertNotIn('0003_shard_identity.sql',manifest['files'])

    def test_existing_legacy_install_cannot_silently_adopt_sql_authority(self):
        from unittest.mock import patch,Mock
        import argparse
        name='0004_catalog_commits.sql';(self.root/name).write_text('SELECT 1;')
        self.manifest['files'][name]=hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        self.manifest.update(schema='jacred-bunny-native-payload/v2',catalog_backend='bunny-sql');self.save()
        state=self.root/'state';deploy=n.Deployment(state,Mock())
        deploy.initialize('a'*40,self.manifest['image']['reference'],self.manifest['image']['digest'])
        cli_spec=importlib.util.spec_from_file_location('backend_cli',P.parent/'cli.py')
        cli=importlib.util.module_from_spec(cli_spec);cli_spec.loader.exec_module(cli)
        args=argparse.Namespace(apply=True,payload_dir=self.root,release=None,state_dir=state)
        with patch.dict('sys.modules',{'native':n}),patch.object(cli,'account_key',return_value='test-only'),patch.object(n,'API') as api:
            with self.assertRaisesRegex(n.NativeError,'authority change'):cli.native_install(args)
        api.return_value.assert_not_called()

    def test_environment_hash_is_order_independent_and_rejects_duplicate_names(self):
        values=[{'name':'A','value':'private-test-value'},{'name':'B','value':'test'}]
        self.assertEqual(n.environment_fingerprint(values),n.environment_fingerprint(list(reversed(values))))
        with self.assertRaises(n.NativeError):n.environment_fingerprint(values+values)

    def test_release_name_cannot_escape_cache_or_repository(self):
        for tag in ['../other','latest','v1.0.0/../../other','https://attacker.invalid']:
            with self.assertRaises(n.NativeError):
                n.fetch_release_payload(tag,self.root/'cache')


if __name__ == '__main__':
    unittest.main()
