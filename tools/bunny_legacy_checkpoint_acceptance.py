#!/usr/bin/env python3
"""Explicit local normal-shutdown/checkpoint/export acceptance. Synthetic data; no Bunny API."""
import argparse,gzip,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',action='store_true')
parser.add_argument('--dotnet',default=shutil.which('dotnet'))
parser.add_argument('--app',type=Path,default=REPO/'bin/Debug/net10.0/JacRed.dll')
parser.add_argument('--output',type=Path)
args=parser.parse_args()
if not __debug__:parser.error('Acceptance requires assertions enabled')
if not args.run:
 print('LOCAL_CHECKPOINT_ACCEPTANCE_PLAN: no process started; use --run with an exact-source .NET build')
 sys.exit(0)
if not args.dotnet or not args.app.is_file():parser.error('Built app and dotnet executable required')
DOTNET=str(Path(args.dotnet).resolve());APP=str(args.app.resolve())
SOURCE=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
SCRATCH=REPO/'temp';SCRATCH.mkdir(exist_ok=True)
sys.path.insert(0,str(REPO/'tests'));sys.path.insert(0,str(REPO/'bunny'))
from test_bunny_native_import import make_source
from legacy import verify_legacy_archive
key='dummy_administrative_key_1234567890'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with tempfile.TemporaryDirectory(dir=SCRATCH,prefix='checkpoint-http-') as directory:
 base=Path(directory);data,*_=make_source(base);config=base/'config';config.mkdir(mode=0o700)
 (data/'temp').mkdir()
 marker=data/'temp/.jacred-bunny-checkpoint.json'
 marker.write_text(json.dumps({'schema':'jacred-legacy-shutdown/v1','owner':'jacred-bunny','boot_id':'stale'}))
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 (config/'init.yaml').write_text(f'listenip: 127.0.0.1\nlistenport: {port}\napikey: dummy_search_key_1234567890\ndevkey: {key}\ntracks: false\nsyncapi: ""\nsyncspidr: false\nweb: false\n')
 env=dict(os.environ,JACRED_CATALOG_BACKEND='filedb',JACRED_CONFIG_DIR=str(config),JACRED_LEGACY_EXPORT_DATA_ROOT=str(data),TZ='UTC')
 command=[DOTNET,APP]
 def get(path,admin=False):
  req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers={'X-Dev-Key':key} if admin else {})
  with opener.open(req,timeout=5) as r:return r.status,dict(r.headers),r.read()
 def wait_version(p):
  for _ in range(100):
   try:return json.loads(get('/version')[2])
   except OSError:
    if p.poll() is not None:raise RuntimeError('Process exited; private log retained only during this test')
    time.sleep(.1)
  raise RuntimeError('Startup deadline')
 def stop(p):
  p.terminate()
  try:p.wait(timeout=15)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5);raise RuntimeError('Graceful stop failed')
 with (base/'normal.log').open('wb') as log:
  p=subprocess.Popen(command,cwd=base,env=env,stdout=log,stderr=log)
  try:
   version=wait_version(p)
   assert version['gitFullSha']==SOURCE,version
   assert version['legacyCheckpointReady']=='1',version
   assert not marker.exists(),'Stale receipt was not invalidated'
   preflight_denials=[]
   for headers in ({},{'X-Dev-Key':'wrong'},{'X-Api-Key':'dummy_search_key_1234567890'}):
    request=urllib.request.Request(f'http://127.0.0.1:{port}/dev/LegacyExportPreflight',headers=headers)
    try:response=opener.open(request,timeout=5)
    except urllib.error.HTTPError as error:response=error
    with response:
     assert response.status==401
     preflight_denials.append(response.status)
   status,headers,body=get('/dev/LegacyExportPreflight',True)
   assert status==200 and headers['Cache-Control']=='private, no-store'
   preflight=json.loads(body)
   assert preflight['status']=='READABLE_SOURCE_METADATA' and preflight['phase']=='complete'
   assert preflight['quiescenceVerified'] is False and preflight['productionReady'] is False
   assert key.encode() not in body and str(base).encode() not in body

  finally:stop(p)
 assert p.returncode==0,p.returncode
 assert marker.exists(),'No verified receipt after normal shutdown'
 proof=json.loads(marker.read_text())
 assert proof['boot_id']==version['legacyBootId']
 assert proof['source_sha']==version['gitFullSha']
 assert proof['master_sha256']==hashlib.sha256((data/'masterDb.bz').read_bytes()).hexdigest()
 with gzip.open(data/'masterDb.bz','rt') as f:assert len(json.load(f))==proof['master_keys']==1
 snapshot={str(x.relative_to(base)):hashlib.sha256(x.read_bytes()).hexdigest() for root in (data,config) for x in root.rglob('*') if x.is_file()}
 export_env=dict(env,JACRED_LEGACY_EXPORT_DEADLINE_UNIX=str(int(time.time())+30),JACRED_LEGACY_EXPORT_DATA_ROOT=str(data),JACRED_LEGACY_EXPORT_LISTEN_IP='127.0.0.1',JACRED_LEGACY_EXPORT_PORT=str(port))
 with (base/'export.log').open('wb') as log:
  p=subprocess.Popen(command+['legacy-export'],cwd=base,env=export_env,stdout=log,stderr=log)
  try:
   export_version=wait_version(p);assert export_version['mode']=='legacy-export-only'
   status,headers,body=get('/migration/archive',True)
   archive=base/'private.tar.gz';archive.write_bytes(body);archive.chmod(0o600)
   receipt=verify_legacy_archive(archive,headers['X-JacRed-Archive-Sha256'],version['gitFullSha'],version['legacyBootId'])
   assert receipt['shutdown_checkpoint_verified']
  finally:stop(p)
 assert p.returncode==0,p.returncode
 after={str(x.relative_to(base)):hashlib.sha256(x.read_bytes()).hexdigest() for root in (data,config) for x in root.rglob('*') if x.is_file()}
 assert snapshot==after
 report={'scope':'Local synthetic normal graceful shutdown -> isolated exporter -> Python archive verifier; synthetic original bytes, not cloud acceptance','normal_version':version,'export_version':export_version,'shutdown_receipt':proof,'archive_receipt':receipt,'stale_receipt_invalidated':True,'source_preflight':preflight,'source_preflight_denials':preflight_denials,'source_bytes_unchanged_during_export':True,'normal_and_exporter_stopped':True}
 if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
