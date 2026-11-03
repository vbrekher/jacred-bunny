"""Owned-resource operations for the existing JacRed Bunny CLI."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import secrets
import time
import urllib.request
import urllib.error
import urllib.parse

from native import Deployment, NativeError, atomic_private, fetch_public, load_payload, serialized, environment_fingerprint, read_private, NoRedirect


class Operations(Deployment):
    def remember_payload(self, directory):
        manifest = load_payload(directory)
        destination = self.directory / 'releases' / manifest['source_sha']
        if destination.is_symlink():
            raise NativeError('Unsafe release cache')
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ['manifest.json', *manifest['files']]:
            if (destination/name).is_symlink():
                raise NativeError('Unsafe cached payload symlink')
            if (Path(directory)/name).resolve() != (destination/name).resolve():
                shutil.copyfile(Path(directory)/name, destination/name)
        load_payload(destination)
        return destination

    def capture_current_payload(self):
        destination = self.directory/'releases'/self.state['source_sha']
        if destination.is_dir():
            manifest = load_payload(destination)
            if manifest['image']['digest'] != self.state['image_digest']:
                raise NativeError('Current cached image identity differs')
            if manifest.get('catalog_backend','filedb') != self.state.get('catalog_backend','filedb'):
                raise NativeError('Current cached catalog authority differs')
            if manifest.get('edge_mode','preview') != self.state.get('edge_mode','preview'):
                raise NativeError('Current cached Edge mode differs')
            return destination
        edge = self.verify('edge')
        value = self.api('GET', '/compute/script/'+str(self.state['resources']['edge']['id'])+'/code')
        code = value.get('Code') if isinstance(value,dict) else None
        if not isinstance(code,str) or hashlib.sha256(code.encode()).hexdigest() != self.state.get('edge_bundle_sha256'):
            raise NativeError('Current Edge code differs from saved checksum; refusing overwrite')
        if self.state.get('edge_release_id') is not None and edge.get('CurrentReleaseId') != self.state['edge_release_id']:
            raise NativeError('Active Edge release changed outside this deployment')
        destination.mkdir(mode=0o700,parents=True,exist_ok=True)
        (destination/'edge.js').write_text(code,encoding='utf-8')
        expected = {x['name']:x['sha256'] for x in self.state.get('migration_receipts',[])}
        files={'edge.js':hashlib.sha256(code.encode()).hexdigest()}
        for name in (['0001_catalog.sql','0002_search_fts5.sql','0004_catalog_commits.sql'] if self.state.get('catalog_backend') == 'bunny-sql' else ['0001_catalog.sql','0002_search_fts5.sql']):
            body=fetch_public('https://raw.githubusercontent.com/vbrekher/jacred-bunny/'+self.state['source_sha']+'/bunny-native/migrations/'+name)
            digest=hashlib.sha256(body).hexdigest()
            if digest != expected.get(name):
                raise NativeError('Cannot reconstruct verified current migration payload')
            (destination/name).write_bytes(body);files[name]=digest
        manifest={'schema':'jacred-bunny-native-payload/v2' if self.state.get('catalog_backend') == 'bunny-sql' else 'jacred-bunny-native-payload/v1',
            'source_sha':self.state['source_sha'],'image':{'reference':self.state['image'],'digest':self.state['image_digest']},'files':files}
        if self.state.get('catalog_backend') == 'bunny-sql':manifest['catalog_backend']='bunny-sql'
        manifest['edge_mode']=self.state.get('edge_mode','preview')
        atomic_private(destination/'manifest.json',manifest)
        load_payload(destination)
        return destination

    def assert_schema_compatible(self, payload):
        manifest=load_payload(payload)
        if manifest.get('catalog_backend','filedb') != self.state.get('catalog_backend','filedb'):
            raise NativeError('Update/rollback cannot change catalog authority; explicit data migration required')
        expected={name:digest for name,digest in manifest['files'].items() if name.endswith('.sql')}
        rows=self.sql_client(create=False).query('SELECT name,sha256 FROM native_schema_migrations ORDER BY name')
        actual={x['name']:x['sha256'] for x in rows}
        if actual != expected:
            raise NativeError('Rollback/update schema compatibility not established; no destructive SQL rollback is allowed')

    @serialized
    def recover_pending_worker(self):
        pending=self.state.get('pending_update')
        if not pending or pending.get('phase') != 'worker_patch_intent':
            return
        record=self.state['resources']['worker']
        old=deepcopy(record['identity'])
        target=deepcopy(old)
        target['container']['imageTag']='sha-'+pending['to_source']
        target['container']['imageDigest']=pending['to_digest']
        try:
            self.verify('worker')
            return  # The old specification is still present; an explicit retry may PATCH it.
        except NativeError:
            pass
        record['identity']=target
        try:
            self.verify('worker')
        except NativeError:
            record['identity']=old
            raise NativeError('Neither saved nor pending worker identity matches; refusing recovery') from None
        self.state['source_sha']=pending['to_source']
        self.state['image']='ghcr.io/vbrekher/jacred-bunny:sha-'+pending['to_source']
        self.state['image_digest']=pending['to_digest']
        pending['phase']='worker_configuration_confirmed'
        self.save()

    @serialized
    def switch_worker(self, directory):
        manifest=load_payload(directory)
        if self.state['source_sha']==manifest['source_sha'] and self.state['image_digest']==manifest['image']['digest']:
            self.verify('worker')
            return False
        if not self.watchdog_available():
            raise NativeError('A live bounded watchdog is required for image update')
        remote=self.verify('worker')
        record=self.state['resources']['worker']
        old=deepcopy(record['identity'])
        target=deepcopy(old)
        target['container']['imageTag']='sha-'+manifest['source_sha']
        target['container']['imageDigest']=manifest['image']['digest']
        ident=str(record['id']);container_id=str(remote['containerTemplates'][0]['id'])
        self.state['pending_update']={'from_source':self.state['source_sha'],'to_source':manifest['source_sha'],
            'from_digest':self.state['image_digest'],'to_digest':manifest['image']['digest'],'phase':'worker_patch_intent',
            'started_at':datetime.now(timezone.utc).isoformat()}
        self.save()
        uncertain=None
        try:
            self.api('PATCH','/mc/apps/'+ident+'/containers/'+container_id,
                {'imageTag':target['container']['imageTag'],'imageDigest':target['container']['imageDigest']})
        except NativeError as exc:
            uncertain=exc
        # Never replay a possibly successful PATCH. Inspect the same owned ID.
        current=self.api('GET','/mc/apps/'+ident)
        if not isinstance(current,dict) or current.get('name') != record['name']:
            raise NativeError('Updated worker identity could not be confirmed')
        record['identity']=target
        try:
            self.verify('worker')
        except NativeError:
            record['identity']=old
            self.save()
            if uncertain:
                raise NativeError('Image update response uncertain and target not confirmed; no replay') from None
            raise
        self.state['source_sha']=manifest['source_sha']
        self.state['image']=manifest['image']['reference']
        self.state['image_digest']=manifest['image']['digest']
        self.state['pending_update']['phase']='worker_configuration_confirmed'
        self.save()
        return True

    def apply_release(self, directory, rollback=False):
        manifest=load_payload(directory)
        self.assert_schema_compatible(directory)
        self.recover_pending_worker()
        pending=self.state.get('pending_update')
        if pending and not rollback and pending['to_source'] != manifest['source_sha']:
            raise NativeError('Resume the pending release or explicitly roll it back first')
        if pending and not rollback:
            previous=self.directory/'releases'/pending['from_source']
            load_payload(previous)
            old_sha=pending['from_source']
        else:
            previous=self.capture_current_payload()
            old_sha=self.state['source_sha']
        self.remember_payload(directory)
        changed=self.switch_worker(directory)
        changed=changed or bool(pending and old_sha != manifest['source_sha'])
        self.wire_edge(Path(directory)/'edge.js',manifest['files']['edge.js'])
        if changed:
            self.worker_action('start')
        return {'changed':changed,'from_source':old_sha,'to_source':manifest['source_sha'],
                'previous_payload':str(previous),'rollback':rollback}

    @serialized
    def finish_release(self, result, health):
        if not health.get('infrastructureReady') or not health.get('worker_source_matches'):
            raise NativeError('Updated runtime health/source not confirmed')
        if result['changed']:
            self.state.setdefault('release_history',[]).append({
                'from_source':result['from_source'],'to_source':result['to_source'],
                'rollback':result['rollback'],'completed_at':datetime.now(timezone.utc).isoformat()})
        self.state.pop('pending_update',None)
        self.state['last_health']=health
        self.state['phase']='infrastructure_ready_catalog_not_accepted'
        self.save()

    def probe_empty_legacy(self, url):
        """Read actual legacy catalog, orphan and track diagnostics; never infer emptiness from search."""
        remote=self.verify('worker')
        parsed=urllib.parse.urlsplit(url)
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('','/') or not parsed.hostname or not parsed.hostname.endswith(('.bunny.run','.b-cdn.net'))):
            raise NativeError('Unexpected owned worker endpoint')
        saved=self.state.get('last_health',{}).get('worker_url')
        if saved and saved.rstrip('/')!=url.rstrip('/'):raise NativeError('Worker endpoint changed since saved health')
        credentials=read_private(self.secret_path)
        def get(path,admin=True):
            headers={'X-Dev-Key':credentials['admin_key']} if admin else {}
            try:
                req=urllib.request.Request(url.rstrip('/')+path,headers=headers)
                with urllib.request.build_opener(NoRedirect()).open(req,timeout=10) as response:
                    raw=response.read(1024*1024+1);code=response.status
                if len(raw)>1024*1024:raise NativeError('Legacy diagnostic response exceeds bound')
                try:return code,json.loads(raw)
                except ValueError:return code,raw.decode('utf-8','replace')
            except urllib.error.HTTPError as error:
                error.close();return error.code,None
            except (urllib.error.URLError,TimeoutError):raise NativeError('Legacy diagnostic unavailable; response omitted') from None
        code,version=get('/version',False)
        if code!=200 or not isinstance(version,dict) or version.get('catalogBackend')!='filedb' or version.get('gitFullSha')!=self.state['source_sha']:
            raise NativeError('Require verified bridge image reporting exact legacy authority/source')
        code,_=get('/api/v1.0/config',False)
        if code not in (401,403):raise NativeError('Unauthenticated administrative configuration is not denied; cutover refused')
        code,config=get('/api/v1.0/config')
        data=config.get('data') if isinstance(config,dict) else None
        if code!=200 or not isinstance(data,dict) or data.get('syncapi') or data.get('tracks') is not False:
            raise NativeError('Require disabled remote sync and tracks before empty staging transition')
        env=remote['containerTemplates'][0].get('environmentVariables')
        environment_fingerprint(env)
        if {row['name']:row['value'] for row in env}.get('JACRED_BUNNY_CRON')!='0':
            raise NativeError('Require disabled staging cron')
        code,jobs=get('/health/background-jobs',False)
        if code!=200 or not isinstance(jobs,dict) or jobs.get('jobs')!=[]:raise NativeError('Background jobs active or unknown')
        code,diagnostic=get('/dev/FindCorrupt?sampleSize=1')
        if code!=200 or not isinstance(diagnostic,dict) or diagnostic.get('ok') is not True or diagnostic.get('totalFdbKeys')!=0 or diagnostic.get('totalTorrents')!=0:
            raise NativeError('Legacy catalog is nonempty or unknown; use full catalog migration')
        code,before=get('/cron/maintenance/Status')
        if code!=200 or not isinstance(before,dict) or before.get('running') is not False:raise NativeError('Maintenance active or unknown')
        previous=(before.get('last') or {}).get('finishedAt')
        code,started=get('/cron/maintenance/Check?mode=report&sampleSize=1')
        if code!=200 or started!='ok':raise NativeError('Read-only integrity report not started')
        deadline=time.monotonic()+60
        while True:
            code,status=get('/cron/maintenance/Status')
            report=status.get('last') if isinstance(status,dict) else None
            if code==200 and isinstance(status,dict) and status.get('running') is False and isinstance(report,dict) and report.get('finishedAt')!=previous:
                break
            if time.monotonic()>=deadline:raise NativeError('Fresh read-only integrity report not confirmed')
            time.sleep(1)
        issues=report.get('issues',{});totals=report.get('totals',{})
        if (report.get('ok') is not True or report.get('mode')!='report' or totals!={'fdbKeys':0,'torrents':0} or
            any((issues.get(name) or {}).get('count')!=0 for name in ('orphanShardFiles','missingShardFile','emptyShardListed'))):
            raise NativeError('Legacy catalog or filesystem shards are nonempty/unknown')
        code,tracks=get('/dev/TracksStats?includeTorrentDb=true&refresh=true')
        stats=tracks.get('stats') if isinstance(tracks,dict) else None
        if code!=200 or not isinstance(tracks,dict) or tracks.get('ok') is not True or not isinstance(stats,dict) or any(stats.get(key)!=0 for key in ('total','filesScanned','fromTracksFiles','fromMemory','fromTorrentDb','torrentsScanned','readErrors','torrentDbErrors')):
            raise NativeError('Track data or track diagnostics are nonempty/unknown')
        return {'scope':'empty owned staging only','legacy_source_sha':self.state['source_sha'],
                'observed_at':datetime.now(timezone.utc).isoformat(),'report_finished_at':report['finishedAt'],
                'catalog_keys':0,'torrents':0,'orphan_shards':0,'track_files':0,'sync_disabled':True,'tracks_disabled':True,'cron_disabled':True}

    @serialized
    def prepare_empty_sql_cutover(self, directory, proof):
        manifest=load_payload(directory)
        if manifest.get('catalog_backend')!='bunny-sql':raise NativeError('Require SQL authority payload v2')
        if self.state.get('catalog_backend','filedb')!='filedb':raise NativeError('Source catalog is not legacy FileDB')
        if (not isinstance(proof,dict) or proof.get('legacy_source_sha')!=self.state['source_sha'] or
            any(proof.get(key)!=0 for key in ('catalog_keys','torrents','orphan_shards','track_files')) or
            any(proof.get(key) is not True for key in ('sync_disabled','tracks_disabled','cron_disabled'))):
            raise NativeError('No verified empty legacy source proof')
        age=time.time()-datetime.fromisoformat(proof['observed_at']).timestamp()
        if not 0<=age<=90:raise NativeError('Legacy source proof expired')
        if self.state.get('pending_cutover'):
            pending=self.state['pending_cutover']
            if pending['to_source']!=manifest['source_sha'] or pending['to_digest']!=manifest['image']['digest']:
                raise NativeError('Resume the saved cutover target first')
            if pending.get('phase')!='prepared':raise NativeError('Cutover already passed source preparation')
            pending['source_proof']=proof;self.save()
            return pending
        sql=self.sql_client(create=False)
        receipts={row['name']:row['sha256'] for row in sql.query('SELECT name,sha256 FROM native_schema_migrations ORDER BY name')}
        expected={name:value for name,value in manifest['files'].items() if name.endswith('.sql') and name!='0004_catalog_commits.sql'}
        if receipts!=expected:raise NativeError('Expected unchanged isolated SQL staging schema before transition')
        for table in ('buckets','bucket_aliases','torrents','track_file_inventory','native_imports'):
            if sql.query('SELECT count(*) AS n FROM '+table)[0]['n']!=0:raise NativeError('SQL staging catalog is nonempty; transition refused')
        remote=self.verify('worker')
        old=deepcopy(self.state['resources']['worker']['identity'])
        old['environment_sha256']=environment_fingerprint(remote['containerTemplates'][0]['environmentVariables'])
        previous=self.capture_current_payload()
        nonce=secrets.token_hex(8);archive=self.directory/'cutovers'/nonce/'previous-legacy-payload'
        archive.parent.mkdir(mode=0o700,parents=True,exist_ok=True);shutil.copytree(previous,archive)
        pending={'id':nonce,'phase':'prepared','from_source':self.state['source_sha'],'to_source':manifest['source_sha'],
                 'to_digest':manifest['image']['digest'],'old_identity':old,'source_proof':proof,
                 'legacy_payload':str(archive),'started_at':datetime.now(timezone.utc).isoformat()}
        self.state['resources']['worker']['identity']=old
        self.state['pending_cutover']=pending;self.save()
        return pending

    def _adopt_sql_cutover_target(self, manifest, pending, remote):
        if remote.get('status') not in ('active','inactive'):
            raise NativeError('Unknown cutover target lifecycle; saved intent retained')
        self.state.update(source_sha=manifest['source_sha'],image=manifest['image']['reference'],image_digest=manifest['image']['digest'],catalog_backend='bunny-sql',payload_schema=manifest['schema'])
        pending['phase']='worker_configuration_confirmed'
        pending['provider_patch_activated_target']=remote['status']=='active'
        self.save()  # Authority may already be live after the provider's PATCH.
        if remote['status']=='active' and (not self.watchdog_available() or time.time()>=self.state['stop_at']):
            self.api('POST','/mc/apps/'+str(self.state['resources']['worker']['id'])+'/undeploy')
            raise NativeError('Stop requested for approved SQL target without a bounded watchdog; confirm stop before resume')

    @serialized
    def configure_sql_cutover(self, directory):
        manifest=load_payload(directory);pending=self.state.get('pending_cutover')
        if not pending or pending['to_source']!=manifest['source_sha'] or pending['to_digest']!=manifest['image']['digest']:
            raise NativeError('No matching explicit cutover intent')
        record=self.state['resources']['worker'];old=pending['old_identity']
        # A configured target may be adopted after a lost response; the source must remain stopped.
        original=record['identity'];candidate=pending.get('target_identity')
        if candidate:
            record['identity']=candidate
            try:remote=self.verify('worker')
            except NativeError:record['identity']=original
            else:
                self._adopt_sql_cutover_target(manifest,pending,remote)
                return
        record['identity']=old
        remote=self.verify('worker')
        if remote.get('status')!='inactive':raise NativeError('Owned source must be confirmed inactive before SQL changes')
        if pending.get('phase')=='worker_patch_intent':raise NativeError('Uncertain cutover PATCH not confirmed; no automatic replay')
        pending['phase']='source_stopped';self.save()
        sql=self.sql_client(write=True,create=False)
        expected={name:value for name,value in manifest['files'].items() if name.endswith('.sql')}
        actual={row['name']:row['sha256'] for row in sql.query('SELECT name,sha256 FROM native_schema_migrations ORDER BY name')}
        if any(expected.get(name)!=value for name,value in actual.items()) or not {'0001_catalog.sql','0002_search_fts5.sql'}<=set(actual):
            raise NativeError('Unexpected SQL schema during empty staging transition')
        tables=['buckets','bucket_aliases','torrents','track_file_inventory','native_imports']
        if '0004_catalog_commits.sql' in actual:tables+=['catalog_bucket_versions','catalog_commits']
        if any(sql.query('SELECT count(*) AS n FROM '+table)[0]['n']!=0 for table in tables):
            raise NativeError('SQL catalog changed after preparation; transition refused')
        sql.migrate([Path(directory)/name for name in sorted(manifest['files']) if name.endswith('.sql')])
        self.state['migration_receipts']=sql.query('SELECT name,sha256 FROM native_schema_migrations ORDER BY name')
        pending['phase']='schema_applied';self.save()
        credentials=read_private(self.secret_path);database=self.verify('database')
        env=remote['containerTemplates'][0]['environmentVariables']
        keys={'JACRED_CATALOG_BACKEND','JACRED_DATABASE_URL','JACRED_DATABASE_READ_TOKEN','JACRED_DATABASE_WRITE_TOKEN'}
        if any(row['name'] in keys for row in env):raise NativeError('Legacy environment already contains SQL settings; reconcile before transition')
        target_env=env+[
            {'name':'JACRED_CATALOG_BACKEND','value':'bunny-sql'}, {'name':'JACRED_DATABASE_URL','value':database['url']},
            {'name':'JACRED_DATABASE_READ_TOKEN','value':credentials['database_read_token']},
            {'name':'JACRED_DATABASE_WRITE_TOKEN','value':credentials['database_write_token']}]
        target=deepcopy(old);target['container'].update(imageTag='sha-'+manifest['source_sha'],imageDigest=manifest['image']['digest'])
        target['environment_sha256']=environment_fingerprint(target_env)
        pending.update(target_identity=target,phase='worker_patch_intent');self.save()
        try:
            self.api('PATCH','/mc/apps/'+str(record['id'])+'/containers/'+str(remote['containerTemplates'][0]['id']),
                     {'imageTag':target['container']['imageTag'],'imageDigest':target['container']['imageDigest'],'environmentVariables':target_env})
        except NativeError:pass  # Inspect the same owned ID; never replay a lost PATCH here.
        record['identity']=target
        try:
            confirmed=self.verify('worker')
        except NativeError:
            record['identity']=old;self.save();raise NativeError('Cutover target not confirmed; saved intent retained and source left stopped') from None
        self._adopt_sql_cutover_target(manifest,pending,confirmed)

    @serialized
    def finish_sql_cutover(self, health):
        pending=self.state.get('pending_cutover')
        if (not pending or pending.get('phase')!='worker_configuration_confirmed' or not health.get('infrastructureReady') or
            health.get('worker_catalog_backend')!='bunny-sql' or not health.get('worker_source_matches')):
            raise NativeError('SQL authority/source/runtime health not confirmed')
        pending['phase']='sql_runtime_confirmed';pending['completed_at']=datetime.now(timezone.utc).isoformat()
        pending['previous_legacy_release_history']=self.state.get('release_history',[])
        self.state.setdefault('cutover_history',[]).append(pending)
        self.state['release_history']=[]  # A legacy image is not a compatible SQL rollback target.
        self.state.pop('pending_cutover',None)
        self.state['last_health']=health;self.state['phase']='sql_runtime_ready_catalog_not_accepted';self.save()

    def rollback_payload(self):
        if self.state.get('pending_update'):
            path=self.directory/'releases'/self.state['pending_update']['from_source']
            load_payload(path)
            return path
        history=self.state.get('release_history',[])
        if not history:
            raise NativeError('No confirmed previous release to roll back to')
        source=self.state['source_sha'] if history[-1].get('rollback') else history[-1]['from_source']
        path=self.directory/'releases'/source
        load_payload(path)
        return path
