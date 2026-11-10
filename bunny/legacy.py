"""Private original-byte migration transport; never treats a byte archive as SQL acceptance."""
from decimal import Decimal, InvalidOperation
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import unicodedata
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from contextlib import ExitStack

from native import NativeError, NoRedirect, atomic_private, environment_fingerprint, read_private, serialized
from operations import Operations

SOURCE_LIMIT = 256 * 1024 * 1024
ARCHIVE_LIMIT = SOURCE_LIMIT + 32 * 1024 * 1024
EXPANDED_LIMIT = SOURCE_LIMIT + 48 * 1024 * 1024
FILE_LIMIT = 10000
MANIFEST_LIMIT = 16 * 1024 * 1024
EPOCH_TICKS = 621355968000000000


class _BoundedReader:
    def __init__(self, stream, limit):
        self.stream = stream
        self.remaining = limit

    def read(self, size=-1):
        # Tar's streaming reader normally requests 10 KiB. Refuse an unbounded
        # allocation even if a future parser requests a malicious PAX size.
        if size < 0 or size > 64 * 1024:
            raise NativeError('Legacy archive parser requested an unbounded read')
        data = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise NativeError('Legacy archive expansion limit exceeded')
        return data


class _BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Guard BEFORE stdlib parses/allocates PAX payloads. Only the regular
        # files and per-entry PAX headers produced by our .NET writer are valid.
        if self.type == tarfile.XHDTYPE:
            if self.size < 0 or self.size > 16 * 1024:
                raise NativeError('Legacy archive PAX metadata exceeds bound')
        elif self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
            raise NativeError('Legacy archive contains unsupported member type')
        elif self.size < 0 or self.size > SOURCE_LIMIT:
            raise NativeError('Legacy archive member exceeds bound')
        return super()._proc_member(archive)


def _safe_name(name):
    if (not isinstance(name, str) or len(name) > 1024 or
        any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xd800 <= ord(c) <= 0xdfff or c in '\\:' for c in name) or
        any(part in ('', '.', '..') for part in name.split('/')) or
        not name.startswith(('Data/', 'config/'))):
        raise NativeError('Unsafe legacy archive member name; path omitted')


def _digest(stream):
    digest = hashlib.sha256()
    total = 0
    while True:
        block = stream.read(64 * 1024)
        if not block:
            return digest.hexdigest()
        total += len(block)
        if total > ARCHIVE_LIMIT:
            raise NativeError('Legacy compressed archive exceeds bound')
        digest.update(block)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NativeError('Legacy manifest contains duplicate JSON fields')
        result[key] = value
    return result


def verify_legacy_archive(path, expected_sha256, expected_source_sha, expected_boot_id=None, *, _copy_to=None):
    """Stream and verify every original byte. Does not extract or modify sources.

    Parent directories must be trusted. The private archive remains available
    for a later explicitly fenced import; the public receipt contains no paths.
    """
    if (not isinstance(expected_sha256, str) or not isinstance(expected_source_sha, str) or
        not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or not re.fullmatch(r'[0-9a-f]{40}', expected_source_sha)):
        raise NativeError('Missing legacy archive/source identity')
    if expected_boot_id is not None and (not isinstance(expected_boot_id, str) or not re.fullmatch(r'[0-9a-f]{32}', expected_boot_id)):
        raise NativeError('Invalid legacy shutdown process identity')
    fd = None
    try:
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > ARCHIVE_LIMIT:
            raise NativeError('Legacy archive is not a bounded regular file')
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise NativeError('Legacy archive must be privately owned with mode 0600 or stricter')
        with os.fdopen(fd, 'rb') as source:
            fd = None
            if _digest(source) != expected_sha256:
                raise NativeError('Legacy compressed archive checksum mismatch')
            source.seek(0)
            members = {}
            portable_names = set()
            total = 0
            manifest = None
            manifest_digest = None
            checkpoint = None
            with gzip.GzipFile(fileobj=source, mode='rb') as compressed:
                bounded = _BoundedReader(compressed, EXPANDED_LIMIT)
                with tarfile.open(fileobj=bounded, mode='r|', tarinfo=_BoundedTarInfo, encoding='utf-8', errors='strict') as archive:
                    for member in archive:
                        if manifest is not None:
                            raise NativeError('Legacy archive has records after manifest')
                        if not member.isfile() or member.size < 0 or member.size > SOURCE_LIMIT or member.sparse:
                            raise NativeError('Legacy archive contains unsupported file')
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise NativeError('Legacy archive member content missing')
                        with stream, ExitStack() as opened:
                            if member.name == 'manifest.json':
                                if member.size > MANIFEST_LIMIT:
                                    raise NativeError('Legacy archive manifest exceeds bound')
                                raw = stream.read(MANIFEST_LIMIT + 1)
                                if len(raw) != member.size:
                                    raise NativeError('Legacy archive manifest length mismatch')
                                manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
                                manifest_digest = hashlib.sha256(raw).hexdigest()
                                continue
                            _safe_name(member.name)
                            portable = unicodedata.normalize('NFC', member.name).casefold()
                            if portable in portable_names or len(members) >= FILE_LIMIT:
                                raise NativeError('Legacy archive duplicate/colliding name or file limit')
                            portable_names.add(portable)
                            total += member.size
                            if total > SOURCE_LIMIT:
                                raise NativeError('Legacy archive source capacity exceeded')
                            output = None
                            target = None
                            if _copy_to is not None:
                                target = Path(_copy_to)/member.name
                                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                                outfd = os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                                output = opened.enter_context(os.fdopen(outfd,'wb'))
                            digest = hashlib.sha256()
                            length = 0
                            marker = bytearray() if member.name == 'Data/temp/.jacred-bunny-checkpoint.json' else None
                            if marker is not None and member.size > 4096:
                                raise NativeError('Legacy shutdown receipt exceeds bound')
                            while True:
                                block = stream.read(64 * 1024)
                                if not block:
                                    break
                                length += len(block)
                                if length > member.size:
                                    raise NativeError('Legacy archive member length exceeded')
                                digest.update(block)
                                if output is not None:output.write(block)
                                if marker is not None:
                                    marker.extend(block)
                            if length != member.size:
                                raise NativeError('Legacy archive truncated member')
                            ticks = Decimal(str(member.pax_headers.get('mtime', member.mtime))) * 10000000 + EPOCH_TICKS
                            if not ticks.is_finite() or ticks != ticks.to_integral_value():
                                raise NativeError('Legacy archive timestamp precision invalid')
                            if output is not None:
                                output.flush();os.fsync(output.fileno())
                                stamp=(int(ticks)-EPOCH_TICKS)*100
                                os.utime(target,ns=(stamp,stamp),follow_symlinks=False)
                            members[member.name] = (length, digest.hexdigest(), int(ticks))
                            if marker is not None:
                                checkpoint = json.loads(marker.decode('utf-8'), object_pairs_hook=_unique_object)
                    # Tar's streaming adapter may already have read a block
                    # past its end markers. Inspect its buffer too.
                    while True:
                        trailer = archive.fileobj.read(64 * 1024)
                        if not trailer:
                            break
                        if any(trailer):
                            raise NativeError('Legacy archive has trailing payload')
                # Consume trailers as well: CRC failures and gzip expansion bombs
                # after tar's end markers must not be silently accepted.
                while True:
                    trailer = bounded.read(64 * 1024)
                    if not trailer:
                        break
                    if any(trailer):
                        raise NativeError('Legacy archive has trailing payload')
            if not isinstance(manifest, dict) or set(manifest) != {'schema', 'source_sha', 'quiescence', 'files'}:
                raise NativeError('Legacy archive manifest unavailable or unsupported')
            if (manifest['schema'] != 'jacred-legacy-byte-archive/v1' or manifest['source_sha'] != expected_source_sha or
                manifest['quiescence'] != 'REQUIRES_VERIFIED_SINGLE_WORKER_REPLACEMENT' or not isinstance(manifest['files'], list) or
                len(manifest['files']) != len(members)):
                raise NativeError('Legacy archive provenance or inventory mismatch')
            declared = set()
            for item in manifest['files']:
                if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256', 'modified_utc_ticks'}:
                    raise NativeError('Legacy archive manifest member invalid')
                name = item['path']
                _safe_name(name)
                if (name in declared or type(item['bytes']) is not int or type(item['modified_utc_ticks']) is not int or
                    not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']) or
                    members.get(name) != (item['bytes'], item['sha256'], item['modified_utc_ticks'])):
                    raise NativeError('Legacy archive manifest content mismatch')
                declared.add(name)
            if 'config/init.yaml' not in declared or not any(re.fullmatch(r'Data/masterDb[^/]*\.bz', name) for name in declared):
                raise NativeError('Legacy archive required source files absent')
            if expected_boot_id is not None:
                expected_fields = {'schema', 'owner', 'boot_id', 'source_sha', 'master_sha256', 'master_keys', 'checkpointed_utc'}
                if (not isinstance(checkpoint, dict) or set(checkpoint) != expected_fields or
                    checkpoint['schema'] != 'jacred-legacy-shutdown/v1' or checkpoint['owner'] != 'jacred-bunny' or
                    checkpoint['boot_id'] != expected_boot_id or checkpoint['source_sha'] != expected_source_sha or
                    checkpoint['master_sha256'] != members.get('Data/masterDb.bz', (None, None))[1] or
                    type(checkpoint['master_keys']) is not int or checkpoint['master_keys'] < 0 or
                    not isinstance(checkpoint['checkpointed_utc'], str)):
                    raise NativeError('Verified shutdown checkpoint does not match the observed process and original master')
                # .NET emits seven fractional digits; Python 3.9 accepts up to
                # six. Preserve original bytes and normalize only for validation.
                timestamp = re.sub(r'(\.\d{6})\d+(?=(?:Z|[+-]\d\d:\d\d)$)', r'\1', checkpoint['checkpointed_utc'])
                if datetime.fromisoformat(timestamp.replace('Z', '+00:00')).utcoffset() is None:
                    raise NativeError('Legacy shutdown timestamp must include its UTC offset')
            source.seek(0)
            if _digest(source) != expected_sha256 or _identity(os.fstat(source.fileno())) != _identity(info):
                raise NativeError('Legacy archive changed during verification')
            if _copy_to is not None:
                atomic_private(Path(_copy_to)/'archive-manifest.json',manifest)
            return {'schema': 'jacred-legacy-archive-receipt/v1', 'source_sha': expected_source_sha,
                    'gzip_sha256': expected_sha256, 'manifest_sha256': manifest_digest,
                    'file_count': len(members), 'source_bytes': total,
                    'shutdown_checkpoint_verified': expected_boot_id is not None,
                    'status': 'VERIFIED_PRIVATE_BYTES_NOT_SQL_MIGRATION'}
    except NativeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError, InvalidOperation, RecursionError, EOFError, tarfile.TarError):
        raise NativeError('Legacy archive verification failed; private details omitted') from None
    finally:
        if fd is not None:
            os.close(fd)


def prepare_legacy_working_copy(archive, destination, expected_sha256, expected_source_sha, expected_boot_id):
    """New private working bytes only, never a server-volume or SQL restore.

    The caller supplies a trusted private parent. Existing destinations are never
    adopted or modified. A failed copy is retained privately for diagnosis;
    only the final receipt permits downstream staging.
    """
    if expected_boot_id is None:
        raise NativeError('Working copy requires a verified normal shutdown identity')
    destination=Path(destination)
    parent=destination.parent
    try:info=parent.lstat()
    except OSError:raise NativeError('Working-copy parent unavailable; private path omitted') from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise NativeError('Working-copy parent must be a trusted private owned directory')
    if destination.exists() or destination.is_symlink():
        raise NativeError('Working-copy destination already exists; refusing overwrite')
    # Complete bounded validation before creating output; repeat while copying
    # through the same guarded parser, avoiding tar.extract and unchecked PAX.
    verify_legacy_archive(archive,expected_sha256,expected_source_sha,expected_boot_id)
    try:destination.mkdir(mode=0o700)
    except OSError:raise NativeError('Cannot exclusively create private working copy') from None
    receipt=verify_legacy_archive(archive,expected_sha256,expected_source_sha,expected_boot_id,_copy_to=destination)
    receipt=dict(receipt,status='PRIVATE_WORKING_COPY_VERIFIED_NOT_SQL_OR_VOLUME_RESTORE',
        original_permissions_restored=False,original_ownership_restored=False,production_ready=False)
    atomic_private(destination/'working-copy-receipt.json',receipt)
    return receipt


class LegacyExports(Operations):
    def _endpoint(self):
        from cli import extract_endpoint
        remote = self.verify_worker_for_stop()
        url = extract_endpoint(remote)
        parsed = urllib.parse.urlsplit(url or '')
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ('', '/') or
            not parsed.hostname.endswith(('.bunny.run', '.b-cdn.net'))):
            raise NativeError('No verified owned HTTPS legacy endpoint')
        saved = self.state.get('last_health', {}).get('worker_url')
        if not isinstance(saved, str) or saved.rstrip('/') != url.rstrip('/'):
            raise NativeError('Legacy endpoint differs from saved verified health; reconcile without sending credentials')
        return url.rstrip('/')

    def _request(self, path, admin=False):
        headers = {'Cache-Control': 'no-store'}
        if admin:
            headers['X-Dev-Key'] = read_private(self.secret_path)['admin_key']
        request = urllib.request.Request(self._endpoint() + path, headers=headers)
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError):
            raise NativeError('Owned legacy endpoint unavailable; response omitted') from None
        if not any(part.strip().lower() == 'no-store' for part in response.headers.get('Cache-Control', '').split(',')):
            response.close()
            raise NativeError('Actual legacy HTTP no-store policy not confirmed')
        return response

    def _json(self, path, admin=False):
        with self._request(path, admin) as response:
            if response.status != 200:
                raise NativeError('Owned legacy diagnostic denied or unavailable; response omitted')
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise NativeError('Owned legacy diagnostic exceeds bound')
        try:
            return json.loads(raw, object_pairs_hook=_unique_object)
        except ValueError:
            raise NativeError('Owned legacy diagnostic is not valid JSON; response omitted') from None

    def _normal_proof(self):
        version = self._json('/version')
        if (not isinstance(version, dict) or version.get('gitFullSha') != self.state['source_sha'] or
            version.get('catalogBackend') != 'filedb' or version.get('legacyByteExportVersion') != '1' or
            version.get('legacyCheckpointVersion') != '1' or version.get('legacyCheckpointReady') != '1' or
            not isinstance(version.get('legacyBootId'), str) or not re.fullmatch(r'[0-9a-f]{32}', version['legacyBootId'])):
            raise NativeError('Upgrade the owned normal worker to the verified export/checkpoint bridge first')
        with self._request('/api/v1.0/config') as response:
            if response.status not in (401, 403):
                raise NativeError('Legacy administrative configuration is not protected')
        configuration = self._json('/api/v1.0/config', admin=True)
        if not isinstance(configuration, dict) or not isinstance(configuration.get('data'), dict):
            raise NativeError('Current legacy administrative credential not confirmed')
        return {'source_sha': version['gitFullSha'], 'boot_id': version['legacyBootId']}

    def normal_worker_ready(self, remote=None):
        """Read-only proof for an owned ready pod while the global grade converges."""
        remote = self.verify('worker') if remote is None else remote
        status = str(remote.get('status', '')).lower()
        if status == 'active':
            return True
        if status != 'progressing':
            return False
        overview = self.api('GET', '/mc/apps/' + str(self.state['resources']['worker']['id']) + '/overview')
        if (overview.get('status') not in ('active', 'progressing') or
            type(overview.get('desiredInstances')) is not int or overview['desiredInstances'] != 1 or
            type((overview.get('activeInstances') or {}).get('indicator')) is not int or
            overview['activeInstances']['indicator'] != 1):
            return False
        regions = overview.get('regions')
        instances = remote.get('containerInstances')
        templates = remote.get('containerTemplates')
        if (not isinstance(regions, list) or len(regions) != 1 or not isinstance(instances, list) or len(instances) != 1 or
            not isinstance(templates, list) or len(templates) != 1):
            return False
        region = regions[0]
        if (region.get('region') != self.state['resources']['worker']['identity']['region'] or
            region.get('status') not in ('active', 'progressing') or
            type(region.get('instances')) is not int or region['instances'] != 1):
            return False
        pods = region.get('pods')
        if not isinstance(pods, list) or len(pods) != 1:
            return False
        pod = pods[0]
        containers = pod.get('containers')
        if (pod.get('status') != 'ready' or pod.get('message') not in (None, '') or
            not isinstance(containers, list) or len(containers) != 1):
            return False
        try:
            heartbeat = datetime.fromisoformat(pod['lastHeartBeat'].replace('Z', '+00:00'))
            if heartbeat.utcoffset() is None or not -5 <= time.time() - heartbeat.timestamp() <= 60:
                return False
        except (KeyError, AttributeError, ValueError, TypeError, OverflowError):
            return False
        instance, template, container = instances[0], templates[0], containers[0]
        return (isinstance(pod.get('name'), str) and bool(pod['name']) and
            instance.get('podId') == pod['name'] and
            isinstance(template.get('id'), str) and bool(template['id']) and
            instance.get('templateId') == template['id'] == container.get('id') and
            container.get('name') == template.get('name') and
            container.get('status') == 'ready' and container.get('reason') in (None, '') and
            container.get('message') in (None, '') and container.get('exitCode') is None)

    def wait_normal_bridge(self, timeout=120):
        """Require control-plane activation and current HTTP identity together.

        A responding old/starting pod alone cannot authorize replacing writers.
        Keep the existing three-minute export reserve and never renew it here.
        """
        deadline = time.monotonic() + timeout
        while True:
            if self.state['stop_at'] - time.time() < 180:
                raise NativeError('Insufficient original window for normal bridge readiness; no renewal')
            remote = self.verify('worker')
            if self.normal_worker_ready(remote):
                try:
                    proof = self._normal_proof()
                    if self.normal_worker_ready():
                        return proof
                except NativeError:
                    pass
            if time.monotonic() >= deadline:
                raise NativeError('Owned normal control plane and HTTP bridge not ready; no export configuration applied')
            time.sleep(2)

    def _export_directory(self, nonce):
        if not re.fullmatch(r'[0-9a-f]{16}', nonce):
            raise NativeError('Invalid private export identity')
        parent = self.directory / 'legacy-exports'
        output = parent / nonce
        if parent.is_symlink() or output.is_symlink():
            raise NativeError('Private legacy export directory cannot be a symlink')
        parent.mkdir(mode=0o700, exist_ok=True)
        output.mkdir(mode=0o700, exist_ok=True)
        if output.stat().st_mode & 0o077:
            raise NativeError('Legacy export directory must be private')
        return output

    @serialized
    def prepare_legacy_export(self, proof):
        if self.state.get('catalog_backend', 'filedb') != 'filedb' or self.state.get('pending_update') or self.state.get('pending_cutover'):
            raise NativeError('Legacy authority or deployment transition is not suitable for byte export')
        if self.state.get('pending_legacy_export'):
            raise NativeError('Recover the existing legacy export before preparing another')
        if (not isinstance(proof, dict) or proof.get('source_sha') != self.state['source_sha'] or
            not isinstance(proof.get('boot_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', proof['boot_id'])):
            raise NativeError('Verified normal legacy process identity required')
        remote = self.verify('worker')
        if not self.normal_worker_ready(remote):
            raise NativeError('Normal legacy source has no authoritative running proof')
        if not self.watchdog_available() or self.state['stop_at'] - time.time() < 180:
            raise NativeError('A live bounded watchdog window with at least three minutes is required')
        capabilities = read_private(self.directory / 'watchdog.json').get('capabilities') or []
        if not all(value in capabilities for value in ('authority_intent_stop', 'legacy_export_stop')):
            raise NativeError('A legacy-export-aware live watchdog is required before environment mutation')
        nonce = secrets.token_hex(8)
        directory = self._export_directory(nonce)
        environment = remote['containerTemplates'][0].get('environmentVariables')
        old_hash = environment_fingerprint(environment)
        account_key = getattr(self.api, 'key', None)
        if isinstance(account_key, str) and account_key and any(item['value'] == account_key for item in environment):
            raise NativeError('Account credential found in existing cloud environment; refusing to retransmit it')
        if any(item['name'].startswith('JACRED_LEGACY_EXPORT') for item in environment):
            raise NativeError('Unrecorded legacy export mode already present')
        expiry = int(min(time.time() + 540, self.state['stop_at'] - 15))
        target_environment = deepcopy(environment) + [
            {'name': 'JACRED_LEGACY_EXPORT', 'value': '1'},
            {'name': 'JACRED_LEGACY_EXPORT_DEADLINE_UNIX', 'value': str(expiry)}]
        # Environment values contain app credentials: keep them separate from
        # the public-shape deployment journal, in private 0600 files only.
        atomic_private(directory / 'environment.json', {'old': environment, 'target': target_environment})
        old = deepcopy(self.state['resources']['worker']['identity'])
        old['environment_sha256'] = old_hash
        target = deepcopy(old)
        target['environment_sha256'] = environment_fingerprint(target_environment)
        self.state['resources']['worker']['identity'] = old
        self.state['pending_legacy_export'] = dict(proof, nonce=nonce, phase='prepared',
            old_identity=old, target_identity=target, expiry=expiry)
        self.save()

    def _wait_inactive(self):
        deadline = time.monotonic() + 90
        while str(self.verify_worker_for_stop().get('status', '')).lower() != 'inactive':
            if time.monotonic() >= deadline:
                raise NativeError('Owned legacy worker stop not confirmed')
            time.sleep(2)

    @serialized
    def configure_legacy_export(self, restore=False):
        pending = self.state.get('pending_legacy_export')
        if not pending:
            raise NativeError('No recorded legacy export intent')
        remote = self.verify_worker_for_stop()
        if str(remote.get('status', '')).lower() != 'inactive':
            raise NativeError('Worker must be authoritatively inactive before an environment change')
        wanted = pending['old_identity'] if restore else pending['target_identity']
        current_hash = environment_fingerprint(remote['containerTemplates'][0].get('environmentVariables'))
        if current_hash == wanted['environment_sha256']:
            self.state['resources']['worker']['identity'] = deepcopy(wanted)
            pending['phase'] = 'normal_environment_restored' if restore else 'export_environment_confirmed'
            self.save()
            return
        if not restore and pending['phase'] != 'prepared':
            raise NativeError('Uncertain export PATCH not replayed; recover the normal environment')
        directory = self._export_directory(pending['nonce'])
        environment = read_private(directory / 'environment.json')['old' if restore else 'target']
        if environment_fingerprint(environment) != wanted['environment_sha256']:
            raise NativeError('Private legacy environment recovery checksum differs')
        pending['phase'] = 'restore_patch_intent' if restore else 'export_patch_intent'
        self.save()
        container = remote['containerTemplates'][0]['id']
        ident = self.state['resources']['worker']['id']
        try:
            self.api('PATCH', '/mc/apps/' + str(ident) + '/containers/' + str(container), {'environmentVariables': environment})
        except NativeError:
            pass  # Inspect the exact owned ID; never replay an uncertain PATCH.
        old = self.state['resources']['worker']['identity']
        self.state['resources']['worker']['identity'] = deepcopy(wanted)
        try:
            self.verify('worker')
        except NativeError:
            self.state['resources']['worker']['identity'] = old
            self.save()
            raise NativeError('Legacy environment PATCH not confirmed; intent retained for recovery') from None
        pending['phase'] = 'normal_environment_restored' if restore else 'export_environment_confirmed'
        self.save()

    def _download_export(self):
        pending = self.state['pending_legacy_export']
        deadline = min(time.monotonic() + 90, time.monotonic() + pending['expiry'] - time.time())
        while True:
            try:
                version = self._json('/version')
                if (version.get('gitFullSha') == pending['source_sha'] and version.get('mode') == 'legacy-export-only'):
                    break
            except NativeError:
                pass
            if time.monotonic() >= deadline:
                raise NativeError('Isolated legacy exporter source/mode not confirmed')
            time.sleep(2)
        directory = self._export_directory(pending['nonce'])
        output = directory / 'legacy.tar.gz'
        created = False
        try:
            with self._request('/migration/archive', admin=True) as response:
                digest = response.headers.get('X-JacRed-Archive-Sha256', '')
                if (response.status != 200 or response.headers.get('X-JacRed-Source-Sha') != pending['source_sha'] or
                    not re.fullmatch(r'[0-9a-f]{64}', digest) or
                    not re.fullmatch(r'[0-9]{1,9}', response.headers.get('Content-Length', ''))):
                    raise NativeError('Legacy archive response identity/length unavailable')
                expected = int(response.headers['Content-Length'])
                if expected > ARCHIVE_LIMIT:
                    raise NativeError('Legacy archive transfer capacity exceeded')
                fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created = True
                with os.fdopen(fd, 'wb') as destination:
                    size = 0
                    transfer_deadline = time.monotonic() + 90
                    while True:
                        if time.monotonic() > transfer_deadline or time.time() >= pending['expiry']:
                            raise NativeError('Legacy archive transfer deadline exceeded')
                        block = response.read(64 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > expected or size > ARCHIVE_LIMIT:
                            raise NativeError('Legacy archive transfer length exceeded')
                        destination.write(block)
                    if size != expected:
                        raise NativeError('Legacy archive transfer truncated')
                    destination.flush()
                    os.fsync(destination.fileno())
            receipt = verify_legacy_archive(output, digest, pending['source_sha'], pending['boot_id'])
            atomic_private(directory / 'receipt.json', receipt)
            return receipt
        except BaseException:
            if created:
                output.unlink()
            raise

    def export_legacy(self):
        if self.state.get('pending_legacy_export'):
            # Recovery never starts another writer or replays an uncertain
            # enable PATCH. Restore the saved normal specification while stopped.
            self.worker_action('stop')
            self._wait_inactive()
            self.configure_legacy_export(restore=True)
            return self.finish_legacy_export(None, recovered=True)
        proof = self._normal_proof()
        self.prepare_legacy_export(proof)
        receipt = None
        try:
            self.worker_action('stop')
            self._wait_inactive()
            self.configure_legacy_export()
            self.worker_action('start', allow_legacy_export=True)
            receipt = self._download_export()
        finally:
            self.worker_action('stop')
            self._wait_inactive()
            self.configure_legacy_export(restore=True)
        return self.finish_legacy_export(receipt)

    @serialized
    def finish_legacy_export(self, receipt, recovered=False):
        pending = self.state['pending_legacy_export']
        if pending['phase'] != 'normal_environment_restored' or str(self.verify('worker').get('status', '')).lower() != 'inactive':
            raise NativeError('Legacy compensation/stop not confirmed; export intent retained')
        if not recovered:
            directory = self._export_directory(pending['nonce'])
            saved = read_private(directory / 'receipt.json')
            actual = verify_legacy_archive(directory / 'legacy.tar.gz', saved.get('gzip_sha256'), pending['source_sha'], pending['boot_id'])
            if actual != saved or actual != receipt:
                raise NativeError('Final private legacy archive receipt differs; no acceptance recorded')
        record = dict(pending, status='RECOVERED_RETRY_REQUIRED' if recovered else 'PRIVATE_ARCHIVE_VERIFIED',
                      compute_stopped=True, sql_imported=False)
        self.state.setdefault('legacy_export_history', []).append(record)
        self.state.pop('pending_legacy_export')
        self.save()
        return {'status': record['status'], 'export_id': record['nonce'], 'receipt': receipt,
                'normal_environment_restored': True, 'compute_stopped': True, 'productionReady': False}
