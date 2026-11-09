#!/usr/bin/env python3
"""Explicit synthetic Docker acceptance for the managed read-only preparation image. No Bunny API."""
import argparse,hashlib,json,os,re,subprocess,sys,tempfile,time,urllib.request,urllib.error,uuid
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');p.add_argument('--image');p.add_argument('--source-sha');p.add_argument('--output',type=Path);args=p.parse_args()
if not args.run:print('IMAGE_ACCEPTANCE_PLAN: no Docker/network/resources; use --run with an immutable image and source SHA');sys.exit(0)
if not __debug__:p.error('Do not disable assertions')
if not args.image or not re.fullmatch(r'(?:ghcr\.io/vbrekher/jacred-bunny@)?sha256:[0-9a-f]{64}',args.image):p.error('Require this fork at registry digest, or an existing local image ID')
if not re.fullmatch(r'[0-9a-f]{40}',args.source_sha or ''):p.error('Exact source SHA required')
sys.path[:0]=[str(REPO/'tests'),str(REPO/'bunny')]
from test_bunny_native_import import make_source
from legacy import verify_legacy_archive
key='dummy_administrative_key_1234567890';nonce=uuid.uuid4().hex;label='io.jacred.acceptance='+nonce
containers=[];volumes=[];opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def docker(*argv,timeout=40):return subprocess.check_output(['docker',*argv],text=True,timeout=timeout).strip()
if args.image.startswith('sha256:'):
 assert docker('image','inspect','--format','{{.Id}}',args.image)==args.image
else:docker('pull',args.image,timeout=180)
identity=json.loads(docker('image','inspect',args.image))[0]
assert identity.get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision')==args.source_sha
protected=subprocess.run(['docker','run','--rm','--user','1000:1000','--entrypoint','/bin/sh',args.image,'-c','chmod u+w /usr/local/libexec/jacred-export'],capture_output=True,timeout=20)
assert protected.returncode!=0,'Privileged helper is writable by the application user'
report={'schema':'jacred-legacy-image-acceptance/v1','scope':'Synthetic owned Docker volumes, dummy keys, real managed image processes; no Bunny API or customer data','image':args.image,'source_sha':args.source_sha,'production_ready':False}
def own(kind,name):
 value=json.loads(docker(kind,'inspect',name))[0]
 labels=value.get('Labels',{}) if kind=='volume' else value['Config'].get('Labels',{})
 assert labels.get('io.jacred.acceptance')==nonce,'Refuse cleanup of an unowned test resource'
 return value
(REPO/'temp').mkdir(exist_ok=True)
try:
 with tempfile.TemporaryDirectory(prefix='jacred-image-',dir=REPO/'temp') as td:
  base=Path(td);data,*_=make_source(base);config=base/'config';config.mkdir()
  (config/'init.yaml').write_text('listenip: 0.0.0.0\nlistenport: 9117\napikey: dummy_search_key_1234567890\ndevkey: '+key+'\ntracks: false\nsyncapi: ""\nsyncspidr: false\nweb: false\n')
  for part in ['data','config']:
   name='jacred-acceptance-'+nonce+'-'+part
   docker('volume','create','--label',label,name);volumes.append(name);own('volume',name)
  mounts=['--mount','type=volume,source='+volumes[0]+',target=/app/Data','--mount','type=volume,source='+volumes[1]+',target=/app/config']
  readonly=[x+',readonly' if x.startswith('type=volume,') else x for x in mounts]
  def start(part,extra):
   name='jacred-acceptance-'+nonce+'-'+part
   docker('create','--name',name,'--label',label,'--publish','127.0.0.1::9117',*mounts,*extra)
   containers.append(name);own('container',name);docker('start',name)
   url='http://127.0.0.1:'+docker('port',name,'9117/tcp').split(':')[-1];deadline=time.monotonic()+40
   while True:
    try:
     with opener.open(url+'/version',timeout=2) as r:version=json.load(r)
     assert version['gitFullSha']==args.source_sha
     return name,url,version
    except OSError:
     state=own('container',name)['State']
     if not state['Running']:raise RuntimeError('Image process exited before source proof')
     if time.monotonic()>=deadline:raise RuntimeError('Image startup deadline exceeded')
     time.sleep(.2)
  normal,url,version=start('normal',['--mount','type=bind,source='+str(base)+',target=/fixture,readonly','--entrypoint','/bin/sh',args.image,'-c',
   'cp -a /fixture/Data/. /app/Data/ && cp -a /fixture/config/. /app/config/ && chown -R 1000:1000 /app/Data /app/config && mkdir -p /app/Data/lost+found && printf synthetic-root-only-bytes > /app/Data/lost+found/private.bin && chown 0:0 /app/Data/lost+found /app/Data/lost+found/private.bin && chmod 700 /app/Data/lost+found && chmod 600 /app/Data/lost+found/private.bin && exec /usr/local/libexec/jacred-container-entry ./JacRed'])
  assert version['legacyCheckpointReady']=='1';report['normal_version']=version
  req=urllib.request.Request(url+'/dev/LegacyExportPreflight',headers={'X-Dev-Key':key})
  with opener.open(req,timeout=10) as r:
   assert r.headers['Cache-Control']=='private, no-store';diagnosis=json.load(r)
  report['normal_preflight']=diagnosis
  assert diagnosis['status']=='READ_ONLY_PREPARATION_REQUIRED' and diagnosis['failureCode']=='ACCESS_REFUSED',json.dumps(diagnosis,sort_keys=True)
  assert diagnosis['readOnlyPreparationAvailable'] is True and diagnosis['quiescenceVerified'] is False
  report['normal_preflight']=diagnosis
  denied=subprocess.run(['docker','run','--rm','--user','1000:1000',*readonly,'--entrypoint','/bin/sh',args.image,'-c','cat /app/Data/lost+found/private.bin'],capture_output=True,timeout=20)
  assert denied.returncode!=0;report['application_user_cannot_read_protected_source']=True
  docker('stop','--time','25',normal);assert own('container',normal)['State']['ExitCode']==0
  def snapshot():
   return docker('run','--rm',*readonly,'--entrypoint','/bin/sh',args.image,'-c',
    "find /app/Data /app/config -exec stat -c '%n|%u|%g|%a|%y|%s' '{}' ';'; find /app/Data /app/config -type f -exec sha256sum '{}' ';'",timeout=30).splitlines()
  before=sorted(snapshot())
  export,url,version=start('export',['--env','JACRED_LEGACY_EXPORT=1','--env','JACRED_LEGACY_EXPORT_DEADLINE_UNIX='+str(int(time.time())+90),args.image])
  assert version['mode']=='legacy-export-only' and version['preparationMode']=='root-readonly-handoff' and version['effectiveUid']==1000
  report['export_version']=version
  request=urllib.request.Request(url+'/migration/archive',headers={'X-Dev-Key':key})
  with opener.open(request,timeout=15) as response:
   assert response.headers['Cache-Control']=='private, no-store'
   raw=response.read();digest=response.headers['X-JacRed-Archive-Sha256']
  archive=base/'private.tar.gz';archive.write_bytes(raw);archive.chmod(0o600)
  receipt=verify_legacy_archive(archive,digest,args.source_sha,report['normal_version']['legacyBootId'])
  assert receipt['shutdown_checkpoint_verified'];report['archive_receipt']=receipt
  # The protected source bytes must actually be present, not skipped for permissions.
  import tarfile
  with tarfile.open(archive,'r:gz') as tar:assert tar.extractfile('Data/lost+found/private.bin').read()==b'synthetic-root-only-bytes'
  assert sorted(snapshot())==before;report['all_source_bytes_modes_owners_and_mtimes_preserved']=True
  docker('stop','--time','25',export);report['export_exit_code']=own('container',export)['State']['ExitCode']
  assert report['export_exit_code'] in (0,143),'Exporter failed or required forced kill'
  assert 'jacred-legacy-handoff.' not in docker('diff',export),'Private handoff was not cleaned by the bootstrap'
  report['private_handoff_cleaned']=True
  report['status']='SYNTHETIC_PRIVILEGED_READ_UNPRIVILEGED_HTTP_VERIFIED'
finally:
 for name in reversed(containers):
  own('container',name);subprocess.run(['docker','rm','--force','--volumes',name],capture_output=True,check=True,timeout=30)
 for name in reversed(volumes):own('volume',name);docker('volume','rm',name)
 report['owned_test_containers_and_volumes_removed']=True
 if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
