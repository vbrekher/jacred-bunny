"""Owned-resource operations for the existing JacRed Bunny CLI."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from native import Deployment, NativeError, atomic_private, fetch_public, load_payload, serialized


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
        for name in ['0001_catalog.sql','0002_search_fts5.sql']:
            body=fetch_public('https://raw.githubusercontent.com/vbrekher/jacred-bunny/'+self.state['source_sha']+'/bunny-native/migrations/'+name)
            digest=hashlib.sha256(body).hexdigest()
            if digest != expected.get(name):
                raise NativeError('Cannot reconstruct verified current migration payload')
            (destination/name).write_bytes(body);files[name]=digest
        atomic_private(destination/'manifest.json',{'schema':'jacred-bunny-native-payload/v1',
            'source_sha':self.state['source_sha'],'image':{'reference':self.state['image'],'digest':self.state['image_digest']},'files':files})
        load_payload(destination)
        return destination

    def assert_schema_compatible(self, payload):
        manifest=load_payload(payload)
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
