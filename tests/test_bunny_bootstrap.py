"""Execute actual POSIX bootstrap with local deterministic public-download fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.fake=self.root/'tools';self.fake.mkdir()
        self.sources=self.root/'sources';self.sources.mkdir()
        self.cli='''# APP_NAME = "jacred-bunny"
# def ensure_registry(
# def ensure_no_duplicate(
# def create_app_safely(
# "imageRegistryId": str(registry)
# def self_test(
import json, os, sys
from pathlib import Path
if sys.argv[1:] != ['self-test']:
    Path(os.environ['BOOTSTRAP_RESULT']).write_text(json.dumps(sys.argv[1:]))
'''
        (self.sources/'cli.py').write_text(self.cli)
        for module in ('native','operations','backup','legacy'):(self.sources/(module+'.py')).write_text('# synthetic test module\n')
        curl=self.fake/'curl';curl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
url=next(x for x in args if x.startswith('https://'))
if url.endswith('/commits/main'):
    print(json.dumps({'sha':'a'*40}));sys.exit(0)
name=url.rsplit('/',1)[-1]
if name==os.environ.get('BOOTSTRAP_FAIL'):sys.exit(22)
output=Path(args[args.index('-o')+1]);output.write_bytes((Path(os.environ['BOOTSTRAP_SOURCES'])/name).read_bytes())
''');curl.chmod(0o755)
        # Quotes, spaces and shell substitution syntax must remain literal paths.
        self.share=self.root/"share ' literal $(false)";self.bin=self.root/"bin ' literal"
        self.result=self.root/'result.json'
        self.env=dict(os.environ,PATH=str(self.fake)+os.pathsep+os.environ['PATH'],
            JACRED_BUNNY_BIN_DIR=str(self.bin),JACRED_BUNNY_SHARE_DIR=str(self.share),
            BOOTSTRAP_SOURCES=str(self.sources),BOOTSTRAP_RESULT=str(self.result))

    def run_bootstrap(self,*args):
        return subprocess.run(['sh',str(ROOT/'install-bunny.sh'),*args],env=self.env,capture_output=True,text=True,timeout=20)

    def test_default_install_and_repeat_version_are_atomic(self):
        result=self.run_bootstrap();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.result.read_text()),['install'])
        wrapper=(self.bin/'jacred-bunny').read_bytes()
        result=self.run_bootstrap('install','--plan');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.result.read_text()),['install','--plan'])
        self.assertEqual((self.bin/'jacred-bunny').read_bytes(),wrapper)
        module=self.share/'versions'/('a'*40)/'cli.py'
        self.assertEqual(module.read_text(),self.cli)
        self.assertEqual(module.stat().st_mode & 0o777,0o600)

    def test_failed_download_preserves_working_installation(self):
        self.assertEqual(self.run_bootstrap().returncode,0)
        wrapper=(self.bin/'jacred-bunny').read_bytes()
        self.env['BOOTSTRAP_FAIL']='legacy.py'
        self.assertNotEqual(self.run_bootstrap().returncode,0)
        self.assertEqual((self.bin/'jacred-bunny').read_bytes(),wrapper)
        self.assertEqual((self.share/'versions'/('a'*40)/'cli.py').read_text(),self.cli)

    def test_altered_existing_version_is_never_overwritten(self):
        self.assertEqual(self.run_bootstrap().returncode,0)
        module=self.share/'versions'/('a'*40)/'cli.py';module.write_text('# unexpected change')
        wrapper=(self.bin/'jacred-bunny').read_bytes()
        result=self.run_bootstrap();self.assertNotEqual(result.returncode,0)
        self.assertIn('refusing overwrite',result.stderr)
        self.assertEqual(module.read_text(),'# unexpected change')
        self.assertEqual((self.bin/'jacred-bunny').read_bytes(),wrapper)
