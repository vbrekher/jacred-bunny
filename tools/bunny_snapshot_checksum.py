"""Independent verifier for the service's length-framed SQL snapshot checksum.

Input: private jacred-canonical-golden-snapshot/v1 JSON, never uploaded here.
This encoding is intentionally distinct from SHA256 of canonical JSON bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path

TABLES=('buckets','bucket_aliases','torrents','track_file_inventory','native_imports','catalog_bucket_versions','catalog_commits')
def snapshot_checksum(tables):
    digest=hashlib.sha256()
    def frame(tag,value):
        raw=b'' if value is None else str(value).encode('utf-8')
        digest.update(tag.encode('ascii')+len(raw).to_bytes(8,'big')+raw)
    frame('s','jacred-sql-relational-snapshot/v1')
    for name in TABLES:
        rows=tables[name]
        frame('s',name);frame('i',len(rows))
        for row in rows:
            frame('i',len(row))
            for column,value in sorted(row.items()):
                frame('s',column)
                if value is None:frame('n',None)
                elif isinstance(value,str):frame('s',value)
                elif type(value) is int and -(2**63)<=value<2**63:frame('i',value)
                else:raise ValueError('Unsupported snapshot scalar')
    return digest.hexdigest()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);args=p.parse_args()
    data=json.loads(args.snapshot.read_text());assert data['schema']=='jacred-canonical-golden-snapshot/v1'
    print(json.dumps({'algorithm':'jacred-sql-relational-snapshot/v1','snapshotSha256':snapshot_checksum(data['tables'])}))
