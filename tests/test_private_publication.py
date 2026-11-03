"""Real filesystem regressions: private migration artifacts must not clobber files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import bunny_native_import as importer
import bunny_native_export as exporter
import bunny_native_capture as capture
import bunny_native_split as splitter
from test_bunny_native_import import make_source


class PrivatePublicationTests(unittest.TestCase):
    def test_reserved_filename_characters_preserve_read_only_source_identity(self):
        for name in ('source#fragment.db', 'source?query.db', 'source%2Fescaped.db'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data, *_ = make_source(root)
                database = root / name
                importer.stage(data, database)
                before = database.read_bytes()
                existing = set(root.iterdir())
                self.assertEqual(importer.check_file(database)['torrents'], 2)
                self.assertEqual(len(importer.query_file(database, 'Film')), 2)
                self.assertEqual(exporter.export_stage(database, root / 'private.jsonl')['counts']['torrent'], 2)
                self.assertEqual(len(splitter.split(database, root / 'parts', 2)['shards']), 2)
                self.assertEqual(database.read_bytes(), before)
                self.assertEqual(set(root.iterdir()) - existing, {root / 'private.jsonl', root / 'parts'})

    def test_import_refuses_dangling_symlink_even_with_replace(self):
        for replace in (False, True):
            with self.subTest(replace=replace), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data, *_ = make_source(root)
                victim = root / 'must-not-create.db'
                target = root / 'output.db'
                target.symlink_to(victim)
                with self.assertRaises(importer.Refused):
                    importer.stage(data, target, replace=replace)
                self.assertTrue(target.is_symlink())
                self.assertFalse(victim.exists())

    def test_export_refuses_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, *_ = make_source(root)
            database = root / 'source.db'
            importer.stage(data, database)
            victim = root / 'must-not-create.jsonl'
            target = root / 'output.jsonl'
            target.symlink_to(victim)
            with self.assertRaises(exporter.Refused):
                exporter.export_stage(database, target)
            self.assertTrue(target.is_symlink())
            self.assertFalse(victim.exists())

    def test_import_preserves_target_created_during_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, *_ = make_source(root)
            target = root / 'output.db'
            original = importer.check_source
            def concurrent_file(*args):
                result = original(*args)
                target.write_bytes(b'existing-data')
                return result
            with mock.patch.object(importer, 'check_source', side_effect=concurrent_file):
                with self.assertRaises(importer.Refused):
                    importer.stage(data, target)
            self.assertEqual(target.read_bytes(), b'existing-data')
            self.assertEqual(list(root.glob('.jacred-import-*')), [])

    def test_exports_preserve_late_file_or_symlink_and_cleanup(self):
        for kind in ('jsonl', 'capture'):
            for symlink in (False, True):
                with self.subTest(kind=kind, symlink=symlink), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    data, *_ = make_source(root)
                    database = root / 'source.db'
                    importer.stage(data, database)
                    target = root / ('output.' + kind)
                    victim = root / 'victim'
                    victim.write_bytes(b'untouched')
                    fsync = os.fsync
                    def concurrent_file(fd):
                        fsync(fd)
                        if not target.exists() and not target.is_symlink():
                            if symlink:
                                target.symlink_to(victim)
                            else:
                                target.write_bytes(b'existing-data')
                    run = (lambda: exporter.export_stage(database, target)) if kind == 'jsonl' else (
                        lambda: capture.private_output(target, {'private': 'dummy'}))
                    refused = exporter.Refused if kind == 'jsonl' else capture.Refused
                    with mock.patch.object(os, 'fsync', side_effect=concurrent_file):
                        with self.assertRaises(refused):
                            run()
                    self.assertEqual(victim.read_bytes(), b'untouched')
                    self.assertEqual(target.is_symlink(), symlink)
                    if not symlink:
                        self.assertEqual(target.read_bytes(), b'existing-data')
                    self.assertEqual(list(root.glob('.jacred-*')), [])


if __name__ == '__main__':
    unittest.main()
