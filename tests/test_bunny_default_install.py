"""Standard installer boundaries; simulated metadata/API, no live acceptance claim."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
import cli
import native

class DefaultInstallTests(unittest.TestCase):
    def release(self,tag='v0.5.4-native.'+'a'*40,**extra):
        return dict(tag_name=tag,draft=False,prerelease=True,
            assets=[dict(name=n) for n in ('manifest.json','edge.js',*native.SQL_RUNTIME_MIGRATIONS)],**extra)

    def test_release_selection_anonymous_and_prefers_stable_sql(self):
        candidates=[self.release(),self.release('v0.6.0')]
        candidates[1]['prerelease']=False
        with patch.object(native,'fetch_public',return_value=json.dumps(candidates).encode()) as fetch:
            self.assertEqual(native.discover_sql_release(),('v0.6.0',None))
        self.assertEqual(len(fetch.call_args.args),1)
        with patch.object(native,'fetch_public',return_value=json.dumps(candidates[:1]).encode()):
            self.assertEqual(native.discover_sql_release()[1],'a'*40)

    def test_missing_or_malformed_release_refuses(self):
        draft=self.release();draft['draft']=True
        missing=self.release();missing['assets']=[]
        for body in ([],{},[draft,missing],['invalid'],[self.release('../escape')]):
            with self.subTest(body=body),patch.object(native,'fetch_public',return_value=json.dumps(body).encode()):
                with self.assertRaises(native.NativeError):native.discover_sql_release()

    def test_no_public_payload_stops_before_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            parser=argparse.ArgumentParser();cli.standard_install_options(parser)
            args=parser.parse_args(['--state-dir',d])
            with patch.object(cli,'CONFIG',Path(d)/'absent'),patch.object(native,'discover_sql_release',side_effect=native.NativeError('No public SQL')),\
                 patch.object(cli,'account_key',side_effect=AssertionError('credentials requested')):
                with self.assertRaisesRegex(cli.DeploymentError,'No public SQL'):cli.default_install(args)

    def test_legacy_profile_requires_explicit_authority_choice(self):
        with tempfile.TemporaryDirectory() as d:
            profile=Path(d)/'config.json';profile.write_text('{}')
            parser=argparse.ArgumentParser();cli.standard_install_options(parser)
            args=parser.parse_args(['--state-dir',str(Path(d)/'new')])
            with patch.object(cli,'CONFIG',profile),patch.object(native,'discover_sql_release') as discover:
                with self.assertRaisesRegex(cli.DeploymentError,'legacy configuration'):cli.default_install(args)
            discover.assert_not_called()

    def test_top_level_native_status_routes_saved_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'deployment.json').write_text('{}')
            with patch.object(sys,'argv',['jacred-bunny','status','--state-dir',d]),patch.object(cli,'native_staging') as route,patch.object(cli,'status',side_effect=AssertionError('legacy status called')):
                self.assertEqual(cli.main(),0)
                self.assertEqual(route.call_args.args[0].operation,'status')

    def test_native_credentials_never_print_account_or_sql_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'deployment.json').write_text('{}')
            from unittest.mock import Mock
            installed=Mock();installed.secret_path=Path(d)/'credentials.json'
            secrets=dict(api_key='dummy-search',admin_key='dummy-admin',key='dummy-account',database_read_token='dummy-sql')
            output=io.StringIO()
            with patch.object(sys,'argv',['jacred-bunny','credentials','--state-dir',d]),patch.object(native,'Deployment',return_value=installed),patch.object(native,'read_private',return_value=secrets),contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(),0)
            self.assertEqual(json.loads(output.getvalue()),dict(api_key='dummy-search',admin_key='dummy-admin'))

    def test_packager_defaults_to_sql_gateway(self):
        spec=importlib.util.spec_from_file_location('default_package',ROOT/'tools/bunny_native_package.py')
        pkg=importlib.util.module_from_spec(spec);spec.loader.exec_module(pkg)
        with tempfile.TemporaryDirectory() as d:
            import shutil
            root=Path(d)/'source';(root/'bunny-native/edge/dist').mkdir(parents=True)
            (root/'bunny-native/edge/dist/index.js').write_text('dummy-bundle')
            shutil.copytree(ROOT/'bunny-native/migrations',root/'bunny-native/migrations')
            result=pkg.package(root,Path(d)/'out','a'*40,'sha256:'+'b'*64)
            self.assertEqual(result['catalog_backend'],'bunny-sql')
            self.assertEqual(result['edge_mode'],'original-api-gateway')
            self.assertEqual(native.load_payload(Path(d)/'out'),result)
