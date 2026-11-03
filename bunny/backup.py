"""Private SQL catalog snapshots. Does not claim to back up legacy FileDB."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from native import NativeError, SQL, SQLSession, NoRedirect, atomic_private, load_payload
from operations import Operations

TABLES=('buckets','bucket_aliases','torrents','track_file_inventory','native_imports')
COVERAGE={'sql_catalog':True,'legacy_filedb':False,'legacy_tracks_objects':False,'runtime_config':False,'worker_jobs':False}


def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def quote(name):
    return '"'+name.replace('"','""')+'"'


def export_snapshot(sql, path, metadata):
    """Caller holds a real read transaction; page requests share its baton."""
    path=Path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    counts={};digest=hashlib.sha256();tables={}
    try:
        schema={x['name']:x['sha256'] for x in sql.query('SELECT name,sha256 FROM native_schema_migrations ORDER BY name')}
        for name in TABLES:
            info=sql.query('PRAGMA table_info('+quote(name)+')')
            if not info:raise NativeError('Catalog table missing from snapshot schema')
            tables[name]={'columns':[x['name'] for x in info],
                          'order':[x['name'] for x in sorted(info,key=lambda x:x['pk']) if x['pk']]}
        header={'kind':'header','schema':'jacred-sql-backup/v1','coverage':COVERAGE,
                'metadata':metadata,'migration_sha256':schema,'tables':tables}
        with os.fdopen(fd,'wb') as raw:
            fd=None
            with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as stream:
                def write(record,hashed=True):
                    encoded=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode()
                    if len(encoded)>3*1024*1024:raise NativeError('Backup row exceeds bounded restore size')
                    stream.write(encoded)
                    if hashed:digest.update(encoded)
                write(header)
                for name,definition in tables.items():
                    count=0
                    order=','.join(quote(x) for x in definition['order']) or 'rowid'
                    while True:
                        rows=sql.query('SELECT * FROM '+quote(name)+' ORDER BY '+order+' LIMIT 50 OFFSET ?',(count,))
                        if not rows:break
                        for row in rows:
                            write({'kind':'row','table':name,'values':[SQL.encode(row[x]) for x in definition['columns']]})
                        count+=len(rows)
                    counts[name]=count
                write({'kind':'end','counts':counts,'content_sha256':digest.hexdigest()},False)
            raw.flush();os.fsync(raw.fileno())
        return {'counts':counts,'content_sha256':digest.hexdigest(),'gzip_sha256':sha_file(path),'bytes':path.stat().st_size,'coverage':COVERAGE}
    except BaseException:
        if fd is not None:os.close(fd)
        if path.exists():path.unlink()
        raise


def verify_archive(path, expected_sha256, max_expanded=4*1024**3):
    if sha_file(path)!=expected_sha256:raise NativeError('Backup compressed checksum mismatch')
    digest=hashlib.sha256();counts={name:0 for name in TABLES};header=None;ended=False;expanded=0
    try:
        with gzip.open(path,'rb') as stream:
            while True:
                line=stream.readline(3*1024*1024+1)
                if not line:break
                expanded+=len(line)
                if len(line)>3*1024*1024 or expanded>max_expanded:raise NativeError('Backup expansion or row limit exceeded')
                row=json.loads(line)
                if ended:raise NativeError('Backup has trailing records')
                if header is None:
                    if row.get('kind')!='header' or row.get('schema')!='jacred-sql-backup/v1' or set(row.get('tables',{}))!=set(TABLES):
                        raise NativeError('Unsupported backup schema')
                    header=row;digest.update(line);continue
                if row.get('kind')=='end':
                    if row.get('counts')!=counts or row.get('content_sha256')!=digest.hexdigest():raise NativeError('Backup footer mismatch')
                    ended=True;continue
                name=row.get('table')
                if row.get('kind')!='row' or name not in TABLES or len(row.get('values',[]))!=len(header['tables'][name]['columns']):
                    raise NativeError('Invalid backup row')
                for value in row['values']:SQL.decode(value)
                counts[name]+=1;digest.update(line)
    except (OSError,ValueError,KeyError,TypeError,EOFError):
        raise NativeError('Corrupt or unsupported backup; row values omitted') from None
    if not ended:raise NativeError('Incomplete backup')
    return {'header':header,'counts':counts,'content_sha256':digest.hexdigest(),'expanded_bytes':expanded}


class Storage:
    def __init__(self,zone):
        if zone.get('PullZones'):raise NativeError('Backup zone has public Pull Zones; refusing private backup upload')
        host=zone.get('StorageHostname') or 'storage.bunnycdn.com'
        if not re.fullmatch(r'(?:[a-z0-9-]+\.)?storage\.bunnycdn\.com',host):raise NativeError('Unexpected Storage API hostname')
        self.base='https://'+host+'/'+urllib.parse.quote(zone['Name'],safe='')+'/'
        self.writer=zone['Password'];self.reader=zone.get('ReadOnlyPassword') or self.writer
        self.opener=urllib.request.build_opener(NoRedirect())

    def request(self,method,path,source=None,checksum=None):
        if '..' in path or not re.fullmatch(r'[A-Za-z0-9_./-]+',path):raise NativeError('Unsafe backup object path')
        headers={'AccessKey':self.reader if method=='GET' else self.writer}
        body=None
        if source:
            body=open(source,'rb');headers['Content-Length']=str(Path(source).stat().st_size);headers['Content-Type']='application/gzip'
        if checksum:headers['Checksum']=checksum.upper()
        try:
            req=urllib.request.Request(self.base+path,method=method,headers=headers,data=body)
            return self.opener.open(req,timeout=60)
        except (urllib.error.URLError,TimeoutError):
            raise NativeError('Private Storage transfer failed; response omitted') from None
        finally:
            if body:body.close()

    def download(self,path,destination,expected_bytes):
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as out,self.request('GET',path) as response:
                copied=0
                while True:
                    block=response.read(1024*1024)
                    if not block:break
                    copied+=len(block)
                    if copied>expected_bytes:raise NativeError('Backup download exceeds receipt size')
                    out.write(block)
                if copied!=expected_bytes:raise NativeError('Backup download truncated')
        except BaseException:
            if Path(destination).exists():Path(destination).unlink()
            raise


class Backups(Operations):
    def backup(self):
        zone=self.verify('storage');database=self.verify('database');credentials=__import__('native').read_private(self.secret_path)
        transport=Storage(zone)
        directory=self.directory/'backups';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        ident='catalog-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(4)
        archive=directory/(ident+'.jsonl.gz')
        metadata={'owner':self.state['owner'],'database_id':self.state['resources']['database']['id'],
                  'application_source_sha':self.state['source_sha'],'client_snapshot_started_at':datetime.now(timezone.utc).isoformat()}
        started=time.monotonic()
        with SQLSession(database['url'],credentials['database_read_token']) as sql:
            summary=export_snapshot(sql,archive,metadata)
        verify_archive(archive,summary['gzip_sha256'])
        object_path='backups/'+self.state['name']+'/'+archive.name
        with transport.request('PUT',object_path,archive,summary['gzip_sha256']) as response:
            if response.status not in (200,201):raise NativeError('Storage upload not confirmed')
        downloaded=directory/(ident+'.verify.gz')
        try:
            transport.download(object_path,downloaded,summary['bytes'])
            if sha_file(downloaded)!=summary['gzip_sha256']:raise NativeError('Storage read-after-write checksum mismatch')
        finally:
            if downloaded.exists():downloaded.unlink()
        receipt={'schema':'jacred-backup-receipt/v1','id':ident,'owner':self.state['owner'],'database_id':metadata['database_id'],
                 'storage_id':self.state['resources']['storage']['id'],'object':object_path,**summary,
                 'completed_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-started,
                 'production_backup_acceptance':False}
        atomic_private(directory/(ident+'.json'),receipt)
        return receipt

    def restore_test(self,backup_id,keep=False):
        if not re.fullmatch(r'catalog-[0-9TZ]+-[0-9a-f]{8}',backup_id):raise NativeError('Invalid backup ID')
        from native import read_private
        receipt=read_private(self.directory/'backups'/(backup_id+'.json'))
        if (receipt.get('owner')!=self.state['owner'] or receipt.get('database_id')!=self.state['resources']['database']['id'] or
            receipt.get('storage_id')!=self.state['resources']['storage']['id']):raise NativeError('Backup receipt owner mismatch')
        zone=self.verify('storage');transport=Storage(zone)
        with tempfile.TemporaryDirectory(prefix='.restore-',dir=self.directory) as temp:
            archive=Path(temp)/'snapshot.gz'
            transport.download(receipt['object'],archive,receipt['bytes'])
            checked=verify_archive(archive,receipt['gzip_sha256'])  # Verify fully BEFORE DB creation.
            payload=self.capture_current_payload();manifest=load_payload(payload)
            expected={k:v for k,v in manifest['files'].items() if k.endswith('.sql')}
            if checked['header']['migration_sha256']!=expected:raise NativeError('Restore schema/source compatibility not established')
            limits=self.api('GET','/database/v1/config/limits')
            if limits['current_databases']>=limits['max_databases']:raise NativeError('No quota for isolated restore test')
            name='jr-rst-'+secrets.token_hex(4)
            entry={'name':name,'status':'creating','backup_id':backup_id,'created_at':datetime.now(timezone.utc).isoformat()}
            self.state.setdefault('temporary_resources',[]).append(entry);self.save()
            created=self.api('POST','/database/v2/databases',{'name':name,'storage_region':'eu-west-1','primary_regions':['AMS'],'replicas_regions':[]})
            ident=created['db_id'];entry.update(id=ident,status='created');self.save()
            if ident==self.state['resources']['database']['id']:raise NativeError('Refusing restore into source database')
            verified=False;started=time.monotonic()
            try:
                target=self.api('GET','/database/v2/databases/'+ident)['db']
                if target['name']!=name:raise NativeError('Restore database identity mismatch')
                verified=True
                token=self.api('PUT','/database/v2/databases/'+ident+'/auth/generate',{'authorization':'full-access','expires_at':None})['token']
                sql=SQL(target['url'],token)
                sql.migrate([payload/name for name in sorted(expected)])
                columns={table:[x['name'] for x in sql.query('PRAGMA table_info('+quote(table)+')')] for table in TABLES}
                if any(columns[table]!=checked['header']['tables'][table]['columns'] for table in TABLES):raise NativeError('Restore column layout mismatch')
                with gzip.open(archive,'rt',encoding='utf-8') as stream:
                    for line in stream:
                        row=json.loads(line)
                        if row['kind']!='row':continue
                        table=row['table'];values=[SQL.decode(x) for x in row['values']]
                        sql.query('INSERT INTO '+quote(table)+'('+','.join(quote(x) for x in columns[table])+') VALUES('+','.join('?' for _ in values)+')',values)
                # Export the new database in its own snapshot and compare the
                # canonical row content, excluding different deployment metadata.
                destination=Path(temp)/'restored.gz'
                with SQLSession(target['url'],token) as reader:
                    export_snapshot(reader,destination,checked['header']['metadata'])
                restored=verify_archive(destination,sha_file(destination))
                if restored['content_sha256']!=checked['content_sha256']:raise NativeError('Restored row content differs')
                fts=sql.query("SELECT count(*) AS n FROM torrent_fts WHERE torrent_fts MATCH ?",('JacRed',))[0]['n']
                result={'backup_id':backup_id,'restored_database_id':ident,'restored_counts':restored['counts'],
                    'content_sha256_verified':True,'fts_query_completed':True,'fts_probe_matches':fts,
                    'elapsed_seconds':time.monotonic()-started,'coverage':COVERAGE,'production_restore_acceptance':False}
                entry['restore_verified']=True;self.save()
                return result
            finally:
                if verified and not keep:
                    current=self.api('GET','/database/v2/databases/'+ident)['db']
                    if current['name']!=name:raise NativeError('Restore database changed; refusing cleanup')
                    self.api('DELETE','/database/v2/databases/'+ident)
                    entry['status']='deleted_after_restore_test';self.save()
