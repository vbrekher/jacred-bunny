"""Actual private tar/gzip fixtures, not live migration acceptance."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bunny'))
import legacy
from native import NativeError

SOURCE = 'a' * 40
TIME = 1700000000


class LegacyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'private.tar.gz'

    def archive(self, extra=(), modify=None, trailing=b'', pax=None, transform_manifest=None):
        files = [('Data/masterDb.bz', b'original-compressed-bytes'),
                 ('config/init.yaml', b'devkey: dummy-only\n'), *extra]
        manifest = {'schema': 'jacred-legacy-byte-archive/v1', 'source_sha': SOURCE,
                    'quiescence': 'REQUIRES_VERIFIED_SINGLE_WORKER_REPLACEMENT', 'files': []}
        with gzip.open(self.path, 'wb') as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, data in files:
                    info = tarfile.TarInfo(name)
                    info.mtime = TIME
                    info.size = len(data)
                    if pax:
                        info.pax_headers.update(pax)
                    archive.addfile(info, io.BytesIO(data))
                    manifest['files'].append({'path': name, 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'modified_utc_ticks': legacy.EPOCH_TICKS + TIME * 10000000})
                if modify:
                    modify(manifest)
                raw = json.dumps(manifest).encode()
                if transform_manifest:
                    raw = transform_manifest(raw)
                info = tarfile.TarInfo('manifest.json')
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            compressed.write(trailing)
        self.path.chmod(0o600)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_original_bytes_unicode_empty_and_fractional_mtime(self):
        digest = self.archive([('Data/tracks/日本語.json', b'\x00\xff\x80'), ('Data/temp/checkpoint', b'')])
        before = self.path.read_bytes()
        result = legacy.verify_legacy_archive(self.path, digest, SOURCE)
        self.assertEqual(result['file_count'], 4)
        self.assertEqual(result['status'], 'VERIFIED_PRIVATE_BYTES_NOT_SQL_MIGRATION')
        self.assertEqual(result['gzip_sha256'], digest)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('config/init.yaml', json.dumps(result))
        self.assertEqual(list(self.root.iterdir()), [self.path])
        digest = self.archive(pax={'mtime': str(TIME)+'.1234567'}, modify=lambda obj: [
            item.update(modified_utc_ticks=legacy.EPOCH_TICKS+TIME*10000000+1234567) for item in obj['files']])
        self.assertEqual(legacy.verify_legacy_archive(self.path, digest, SOURCE)['file_count'], 2)

    def test_manifest_content_and_provenance_must_match(self):
        for field, value in [('source_sha', 'b'*40), ('schema', 'unrecognized'),
                             ('quiescence', 'operator-PASS'), ('files', [])]:
            with self.subTest(field=field):
                digest = self.archive(modify=lambda obj: obj.update({field: value}))
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def test_member_checksums_lengths_timestamps_and_types_are_verified(self):
        for field, value in [('sha256', 'f'*64), ('bytes', 99), ('bytes', True),
                             ('modified_utc_ticks', 0), ('modified_utc_ticks', True), ('path', 'Data/absent')]:
            with self.subTest(field=field, value=value):
                digest = self.archive(modify=lambda obj: obj['files'][0].update({field: value}))
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def test_unsafe_paths_refused_without_extraction(self):
        for name in ['../outside', '/absolute', 'Data/../outside', 'Data//double', 'Data/./dot',
                     'Data\\windows', 'config/C:drive', 'unknown/namespace', 'Data/control\x01']:
            with self.subTest(name=name):
                digest = self.archive([(name, b'private')])
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, digest, SOURCE)
                self.assertFalse((self.root/'outside').exists())

    def test_duplicate_and_portable_colliding_paths_are_refused(self):
        for extra in [[('Data/a', b'x'), ('Data/a', b'y')],
                      [('Data/A', b'x'), ('Data/a', b'y')],
                      [('Data/é', b'x'), ('Data/e\u0301', b'y')]]:
            with self.subTest(extra=extra):
                digest = self.archive(extra)
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def test_compressed_checksum_is_verified(self):
        self.archive()
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, '0'*64, SOURCE)

    def test_gzip_crc_and_payload_after_tar_end_are_verified(self):
        self.archive()
        raw = bytearray(self.path.read_bytes()); raw[-8] ^= 255; self.path.write_bytes(raw)
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, hashlib.sha256(raw).hexdigest(), SOURCE)
        digest = self.archive(trailing=b'not-tar-private-payload')
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def test_pax_metadata_allocation_and_total_expansion_are_bounded(self):
        digest = self.archive(pax={'comment': 'x'*20000})
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, digest, SOURCE)
        digest = self.archive([('Data/large', b'\x00'*20000)])
        with mock.patch.object(legacy, 'EXPANDED_LIMIT', 4096):
            with self.assertRaises(NativeError):
                legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def test_symlinks_hardlinks_directories_and_sparse_types_are_refused(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.GNUTYPE_SPARSE]:
            with self.subTest(kind=kind):
                with tarfile.open(self.path, 'w:gz') as archive:
                    info = tarfile.TarInfo('Data/unsafe'); info.type = kind; info.linkname = '/private'
                    archive.addfile(info)
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), SOURCE)

    def test_archive_symlink_and_fifo_are_not_opened_as_regular_files(self):
        digest = self.archive()
        link = self.root/'link'; link.symlink_to(self.path)
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(link, digest, SOURCE)
        pipe = self.root/'pipe'; os.mkfifo(pipe)
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(pipe, digest, SOURCE)

    def test_private_mode_and_truncated_gzip_are_required(self):
        digest = self.archive()
        self.path.chmod(0o644)
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, digest, SOURCE)
        self.path.chmod(0o600)
        self.path.write_bytes(self.path.read_bytes()[:-5])
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), SOURCE)

    def test_duplicate_manifest_json_fields_are_not_last_value_wins(self):
        digest = self.archive(transform_manifest=lambda raw: raw.replace(
            b'"source_sha":', b'"source_sha":"' + b'b'*40 + b'","source_sha":', 1))
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, digest, SOURCE)

    def checkpoint(self, **changes):
        value = {'schema': 'jacred-legacy-shutdown/v1', 'owner': 'jacred-bunny', 'boot_id': 'c'*32,
            'source_sha': SOURCE, 'master_sha256': hashlib.sha256(b'original-compressed-bytes').hexdigest(),
            'master_keys': 1, 'checkpointed_utc': '2026-11-04T00:00:00.1234567Z'}
        value.update(changes)
        return self.archive([('Data/temp/.jacred-bunny-checkpoint.json', json.dumps(value).encode())])

    def test_verified_shutdown_binds_the_observed_process_source_and_master(self):
        digest = self.checkpoint()
        result = legacy.verify_legacy_archive(self.path, digest, SOURCE, 'c'*32)
        self.assertTrue(result['shutdown_checkpoint_verified'])
        for changes in [{'boot_id': 'd'*32}, {'source_sha': 'b'*40}, {'master_sha256': '0'*64},
                        {'master_keys': True}, {'master_keys': -1}, {'owner': 'other'},
                        {'checkpointed_utc': 'operator-asserted'}, {'checkpointed_utc': '2026-11-04'}]:
            with self.subTest(changes=changes):
                digest = self.checkpoint(**changes)
                with self.assertRaises(NativeError):
                    legacy.verify_legacy_archive(self.path, digest, SOURCE, 'c'*32)

    def test_an_ordinary_byte_archive_is_not_a_shutdown_checkpoint(self):
        digest = self.archive()
        with self.assertRaises(NativeError):
            legacy.verify_legacy_archive(self.path, digest, SOURCE, 'c'*32)
        self.assertFalse(legacy.verify_legacy_archive(self.path, digest, SOURCE)['shutdown_checkpoint_verified'])


if __name__ == '__main__':
    unittest.main()
