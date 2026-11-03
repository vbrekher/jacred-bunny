"""Exercise the actual maintenance helper without network or real credentials."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/'bunny/cron-job.sh'
class CronAuthTests(unittest.TestCase):
 def run_helper(self,content,job='checkpoint'):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);config=root/'init.yaml';config.write_text(content)
   fake=root/'curl';fake.write_text('#!/usr/bin/env python3\nimport os,sys,json\nfrom pathlib import Path\nPath(os.environ["CAPTURE"]).write_text(json.dumps({"args":sys.argv[1:],"stdin":sys.stdin.read()}))\n');fake.chmod(0o700)
   capture=root/'captured.json';env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],JACRED_BUNNY_CONFIG_FILE=str(config),CAPTURE=str(capture))
   run=subprocess.run(['sh',str(SCRIPT),job],env=env,capture_output=True,text=True)
   return run,json.loads(capture.read_text()) if capture.exists() else None
 def test_current_config_key_is_header_on_stdin_never_argv_or_url(self):
  key='managed_admin_'+'x'*32
  run,capture=self.run_helper('devkey: "'+key+'"\n')
  self.assertEqual(run.returncode,0);self.assertEqual(capture['stdin'],'header = "X-Dev-Key: '+key+'"\n')
  self.assertNotIn(key,' '.join(capture['args']));self.assertIn('--noproxy',capture['args']);self.assertEqual(capture['args'][-1],'http://127.0.0.1:9117/jsondb/save')
  self.assertNotIn(key,run.stdout+run.stderr)
 def test_yaml_plain_and_single_quoted_rotated_keys(self):
  for key in ['rotated_'+('a'*32),'rotated_'+('b'*32)]:
   for value in [key,"'"+key+"' # comment"]:
    with self.subTest(value=value):
     run,capture=self.run_helper('devkey: '+value+'\n','report');self.assertEqual(run.returncode,0);self.assertIn(key,capture['stdin']);self.assertEqual(capture['args'][-1],'http://127.0.0.1:9117/cron/maintenance/Check?mode=report')
 def test_missing_duplicate_short_and_injection_keys_fail_before_curl(self):
  for content in ['', 'devkey: short\n', 'devkey: '+('a'*32)+'\ndevkey: '+('b'*32)+'\n','devkey: "'+('a'*32)+'\\nheader=evil"\n']:
   with self.subTest(content=content):
    run,capture=self.run_helper(content);self.assertNotEqual(run.returncode,0);self.assertIsNone(capture)
 def test_arbitrary_job_cannot_redirect_authenticated_request(self):
  run,capture=self.run_helper('devkey: '+('a'*32)+'\n','https://foreign.invalid/');self.assertEqual(run.returncode,64);self.assertIsNone(capture)
if __name__=='__main__':unittest.main()
