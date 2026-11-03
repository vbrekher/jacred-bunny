#!/usr/bin/env python3
"""Create a public installer payload from exact-source image/build artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def package(root, output, source_sha, image_digest, catalog_backend="filedb"):
    if not re.fullmatch(r'[0-9a-f]{40}',source_sha) or not re.fullmatch(r'sha256:[0-9a-f]{64}',image_digest):
        raise ValueError('Require exact source SHA and immutable OCI digest')
    if catalog_backend not in ("filedb", "bunny-sql"):
        raise ValueError('Unsupported catalog backend')
    output.mkdir(parents=True,exist_ok=True)
    files={}
    inputs=[(root/'bunny-native/edge/dist/index.js','edge.js')]
    inputs += [(root/'bunny-native/migrations'/name,name) for name in (['0001_catalog.sql','0002_search_fts5.sql'] + (['0004_catalog_commits.sql'] if catalog_backend == 'bunny-sql' else []))]
    for path,name in inputs:
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=2*1024*1024:
            raise ValueError('Missing or unsafe build artifact')
        shutil.copyfile(path,output/name)
        files[name]=hashlib.sha256((output/name).read_bytes()).hexdigest()
    manifest={'schema':'jacred-bunny-native-payload/'+('v2' if catalog_backend == 'bunny-sql' else 'v1'),'source_sha':source_sha,
        'image':{'reference':'ghcr.io/vbrekher/jacred-bunny:sha-'+source_sha,'digest':image_digest},'files':files}
    if catalog_backend == 'bunny-sql':manifest['catalog_backend']='bunny-sql'
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-sha',required=True)
    p.add_argument('--image-digest',required=True)
    p.add_argument('--catalog-backend',choices=('filedb','bunny-sql'),default='filedb')
    a=p.parse_args()
    head=subprocess.run(['git','-C',str(a.root),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if head != a.source_sha or subprocess.run(['git','-C',str(a.root),'diff','--quiet','HEAD']).returncode:
        raise SystemExit('Public payload requires a clean tracked source tree at the requested exact SHA')
    package(a.root,a.output,a.source_sha,a.image_digest,a.catalog_backend)
    print('Public native installer payload created (no deployment or acceptance claim)')


if __name__=='__main__':main()
