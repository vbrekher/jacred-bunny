#!/usr/bin/env python3
"""Explicit local exporter process acceptance with synthetic data and dummy keys. No Bunny API."""
import argparse,hashlib,http.client,io,json,os,shutil,socket,subprocess,sys,tarfile,tempfile,time,urllib.request,urllib.error
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',action='store_true',help='Start an isolated local .NET exporter using synthetic data')
parser.add_argument('--dotnet',default=shutil.which('dotnet'))
parser.add_argument('--app',type=Path,default=REPO/'bin/Debug/net10.0/JacRed.dll')
parser.add_argument('--output',type=Path)
args=parser.parse_args()
if not __debug__:parser.error('Acceptance requires assertions enabled; do not use Python optimization')
if not args.run:
 print('LOCAL_EXPORT_ACCEPTANCE_PLAN: no process started; pass --run with the exact-source .NET build')
 sys.exit(0)
if not args.dotnet or not args.app.is_file():parser.error('A built exact-source .NET application and dotnet executable are required')
DOTNET=str(Path(args.dotnet).resolve());APP=str(args.app.resolve())
SCRATCH=REPO/'temp';SCRATCH.mkdir(exist_ok=True)
sys.path.insert(0,str(REPO/'tests'))
from test_bunny_native_import import make_source
key='dummy_administrative_key_1234567890'; search='dummy_search_key_1234567890'
sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(path):return {str(p.relative_to(path)):digest(p) for p in path.rglob('*') if p.is_file()}
with tempfile.TemporaryDirectory(prefix='legacy-http-',dir=SCRATCH) as directory:
 base=Path(directory);data,*_=make_source(base); config=base/'config';config.mkdir(mode=0o700)
 (config/'init.yaml').write_text('devkey: "'+key+'"\napikey: "'+search+'"\n')
 (data/'temp').mkdir();(data/'temp/resume.json').write_text('{"cursor":7}')
 # Incompressible synthetic bytes make the first transfer exceed TCP buffers.
 (data/'temp/backpressure-fixture.bin').write_bytes(os.urandom(8*1024*1024))
 before=inventory(base)
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 env=dict(os.environ,JACRED_CATALOG_BACKEND='filedb',JACRED_CONFIG_DIR=str(config),JACRED_LEGACY_EXPORT_DATA_ROOT=str(data),JACRED_LEGACY_EXPORT_LISTEN_IP='127.0.0.1',JACRED_LEGACY_EXPORT_PORT=str(port),JACRED_LEGACY_EXPORT_DEADLINE_UNIX=str(int(time.time())+45))
 results=[]; version=None
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def request(path,headers=None,method='GET'):
  try:response=opener.open(urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers or {},method=method),timeout=10)
  except urllib.error.HTTPError as err:response=err
  with response:return response.status,dict(response.headers),response.read()
 with (base/'server.log').open('wb') as log:
  process=subprocess.Popen([DOTNET,APP,'legacy-export'],cwd=base,env=env,stdout=log,stderr=log)
  try:
   for _ in range(100):
    try:
     status,headers,body=request('/version');version=json.loads(body);break
    except (OSError,ValueError):
     if process.poll() is not None:raise RuntimeError('Exporter process exited')
     time.sleep(.1)
   assert version and version['gitFullSha']==sha and version['mode']=='legacy-export-only',version
   cases=[('missing','/migration/archive',{},'GET',401),('wrong','/migration/archive',{'X-Dev-Key':'wrong'},'GET',401),('search-key','/migration/archive',{'X-Api-Key':search},'GET',401),('search-as-admin','/migration/archive',{'X-Dev-Key':search},'GET',401),('query-only','/migration/archive?devkey='+key,{},'GET',401),('foreign-origin','/migration/archive',{'Origin':'https://attacker.invalid'},'GET',401),('normal-config-not-registered','/api/v1.0/config',{'X-Dev-Key':key},'GET',404),('collector-not-registered','/cron/subsplease/parse',{'X-Dev-Key':key},'GET',404),('write-not-registered','/api/v1.0/config',{'X-Dev-Key':key},'POST',404),('public-health','/health',{},'GET',200)]
   for name,path,header,method,expected in cases:
    status,headers,body=request(path,header,method);assert status==expected,(name,status)
    assert headers.get('Cache-Control')=='private, no-store';assert not headers.get('Access-Control-Allow-Origin')
    if name!='public-health':assert key.encode() not in body and search.encode() not in body
    results.append({'case':name,'http':status,'no_store':True,'no_cors_reflection':True})
   # Reading Content-Length bytes can finish before server handler cleanup.
   # Force a real overlapping transfer and distinguish contention from quota.
   connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
   connection.connect();connection.sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
   try:
    connection.request('GET','/migration/archive',headers={'X-Dev-Key':key,'Range':'bytes=0-1'})
    first=connection.getresponse()
    assert first.status==200
    status,headers,body=request('/migration/archive',{'X-Dev-Key':key})
    assert status==429 and headers.get('X-JacRed-Export-Rejection')=='busy',(status,headers.get('X-JacRed-Export-Rejection'))
    assert headers.get('Cache-Control')=='private, no-store' and headers.get('Retry-After')=='1'
    results.append({'case':'concurrent-transfer-refused','http':429,'reason':'busy','does_not_consume_download_budget':True})
    connection.sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024*1024)
    first_response=(first.status,dict(first.headers),first.read())
   finally:connection.close()
   archive_digest=None
   for attempt in range(1,5):
    if attempt==1:status,headers,body=first_response
    else:
     wait_until=time.monotonic()+5
     while True:
      status,headers,body=request('/migration/archive',{'X-Dev-Key':key,'Range':'bytes=0-1'})
      if status!=429 or headers.get('X-JacRed-Export-Rejection')!='busy':break
      if time.monotonic()>=wait_until:raise RuntimeError('Prior transfer did not finish within bounded cleanup wait')
      time.sleep(.02)
    if attempt==4:
     assert status==429 and headers.get('X-JacRed-Export-Rejection')=='download-budget',(status,headers.get('X-JacRed-Export-Rejection'));results.append({'case':'download-budget-exhausted','http':status,'reason':'download-budget'});continue
    assert status==200 and headers.get('Cache-Control')=='private, no-store',(attempt,status,headers.get('Cache-Control'))
    archive_digest=hashlib.sha256(body).hexdigest();assert archive_digest==headers['X-JacRed-Archive-Sha256']
    assert headers['X-JacRed-Source-Sha']==sha
    with tarfile.open(fileobj=io.BytesIO(body),mode='r:gz') as tar:
     members=tar.getmembers();assert all(x.isfile() for x in members)
     manifest=json.load(tar.extractfile('manifest.json'));assert manifest['source_sha']==sha
     assert len(manifest['files'])==len(before)
     for file in manifest['files']:
      content=tar.extractfile(file['path']).read();assert hashlib.sha256(content).hexdigest()==file['sha256']
      assert before[file['path']]==file['sha256'];assert len(content)==file['bytes']
    results.append({'case':'private-download-'+str(attempt),'http':status,'member_hashes_match_source':True,'range_does_not_bypass_full_archive':True,'no_store':True})
  finally:
   process.terminate()
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
 lifecycle=[]
 command=[DOTNET,APP,'legacy-export']
 for name,changes,expected in [('expired',{'JACRED_LEGACY_EXPORT_DEADLINE_UNIX':str(int(time.time())-1)},1),('excessive-window',{'JACRED_LEGACY_EXPORT_DEADLINE_UNIX':str(int(time.time())+1000)},1),('sql-mode-refused',{'JACRED_CATALOG_BACKEND':'bunny-sql'},1),('deadline-shutdown',{'JACRED_LEGACY_EXPORT_DEADLINE_UNIX':str(int(time.time())+3)},0)]:
  run=subprocess.run(command,cwd=base,env=dict(env,**changes),capture_output=True,timeout=10)
  assert run.returncode==expected,(name,run.returncode)
  assert key.encode() not in run.stdout+run.stderr and search.encode() not in run.stdout+run.stderr
  lifecycle.append({'case':name,'exit_code':run.returncode,'no_dummy_key_logged':True})
 after=inventory(base);after.pop('server.log',None);assert after==before
 assert not (data/'log').exists()
 report={'scope':'Actual local loopback exporter process with synthetic FileDB, dummy keys and independent tar/hash verification; no cloud migration or customer data','checkout_source_sha':sha,'service_version':version,'http_checks':results,'lifecycle_processes':lifecycle,'source_files':len(before),'source_bytes_unchanged':True,'normal_startup_log_directory_absent':True,'process_stopped':process.poll() is not None,'archive_sha256':archive_digest,'quiescence':'Single isolated local process; cloud owned-worker lifecycle not exercised','production_ready':False}
 if args.output:
  args.output.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
