#!/usr/bin/env python3
"""Publish verified public development payload, never a production readiness claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bunny'))
from native import load_payload, NativeError

REPO='vbrekher/jacred-bunny'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise NativeError('Unexpected GitHub API redirect')


def api(path,allow_missing=False):
    token=os.environ.get('GH_TOKEN')
    if not token:raise NativeError('CI GitHub token required')
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+'/'+path,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'JacRed-candidate-publisher'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=30) as response:
            data=response.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise NativeError('GitHub metadata exceeds bound')
        return json.loads(data)
    except urllib.error.HTTPError as error:
        if error.code==404 and allow_missing:return None
        raise NativeError('GitHub API refused: HTTP '+str(error.code)) from None


def find_release(tag):
    value=api('releases/tags/'+tag,allow_missing=True)
    if value is not None:return value
    # The tag endpoint can omit drafts. Enumerate authenticated metadata before
    # concluding that no previous ambiguous publication exists.
    for page in range(1,21):
        releases=api('releases?per_page=100&page='+str(page))
        if not isinstance(releases,list):raise NativeError('Invalid GitHub release listing')
        matches=[r for r in releases if isinstance(r,dict) and r.get('tag_name')==tag]
        if len(matches)>1:raise NativeError('Ambiguous duplicate releases')
        if matches:return matches[0]
        if len(releases)<100:return None
    raise NativeError('Release enumeration bound reached; cannot prove absence')


def publish(directory,run=False):
    manifest=load_payload(directory)
    if manifest.get('catalog_backend')!='bunny-sql' or manifest.get('edge_mode')!='original-api-gateway':
        raise NativeError('SQL gateway payload required')
    source=manifest['source_sha'];tag='v0.5.4-native.'+source
    names={'manifest.json',*manifest['files'],'image-digest.txt','jacred-bunny-sbom.spdx.json','sbom.sha256'}
    assets={}
    for name in sorted(names):
        path=directory/name
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=64*1024*1024:
            raise NativeError('Missing/unsafe public asset: '+name)
        assets[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    expected=manifest['image']['reference'].split(':sha-')[0]+'@'+manifest['image']['digest']
    if (directory/'image-digest.txt').read_text().strip()!=expected:raise NativeError('Image identity differs')
    if (directory/'sbom.sha256').read_text().split()!=[assets['jacred-bunny-sbom.spdx.json'],'jacred-bunny-sbom.spdx.json']:
        raise NativeError('SBOM checksum differs')
    result=dict(tag=tag,source_sha=source,assets=assets,production_ready=False,apply=run)
    if not run:return result
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_SHA')!=source:
        raise NativeError('Only exact-source owned repository CI may publish')
    # Refuse both existing public releases and incomplete drafts; never clobber.
    if find_release(tag) is not None:
        raise NativeError('Release already exists; refusing overwrite or ambiguous replay')
    notes='Development SQL installer candidate at '+source+'. Not v0.6/v1.0 acceptance. Full production gates remain incomplete. No Bunny account key, private archive or real torrent data is included.'
    command=['gh','release','create',tag,'--repo',REPO,'--target',source,'--draft','--prerelease','--title','Native SQL development candidate '+source[:12],'--notes',notes]
    command += [str(directory/name) for name in sorted(names)]
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
    release=find_release(tag)
    remote=release.get('assets',[])
    if not release.get('draft') or release.get('prerelease') is not True or release.get('target_commitish')!=source or len(remote)!=len(assets):
        raise NativeError('Draft identity/assets differ; retained unpublished for inspection')
    for asset in remote:
        name=asset.get('name')
        if name not in assets or asset.get('state')!='uploaded' or asset.get('digest')!='sha256:'+assets[name]:
            raise NativeError('Draft asset checksum differs; retained unpublished for inspection')
    subprocess.run(['gh','release','edit',tag,'--repo',REPO,'--draft=false','--latest=false'],check=True,stdout=subprocess.DEVNULL)
    result['url']='https://github.com/'+REPO+'/releases/tag/'+tag
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    try:print(json.dumps(publish(args.directory,args.run),indent=2))
    except (NativeError,OSError,ValueError,subprocess.CalledProcessError) as error:
        print('Candidate publication refused: '+type(error).__name__,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
