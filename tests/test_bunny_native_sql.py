"""Real SQLite-backed protocol/migration tests; not live Bunny evidence."""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_sql', ROOT / 'bunny/native.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class SQLiteProtocol:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.requests = []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append(body)
        assert body['requests'][-1] == {'type': 'close'}
        operation = body['requests'][0]
        try:
            if operation['type'] == 'sequence':
                self.db.executescript(operation['sql'])
                result = None
            else:
                stmt = operation['stmt']
                cur = self.db.execute(stmt['sql'], [native.SQL.decode(x) for x in stmt['args']])
                result = {'cols': [{'name': x[0]} for x in (cur.description or [])],
                          'rows': [[native.SQL.encode(v) for v in row] for row in cur.fetchall()]}
                self.db.commit()
            response = {'type': 'ok', 'response': {'result': result}}
        except sqlite3.Error:
            self.db.rollback()
            response = {'type': 'error', 'error': {'message': 'private-row-value-should-not-leak'}}
        return io.BytesIO(json.dumps({'results': [response, {'type': 'ok'}]}).encode())


class SQLTests(unittest.TestCase):
    def setUp(self):
        self.protocol = SQLiteProtocol()
        self.addCleanup(self.protocol.db.close)
        self.sql = native.SQL('libsql://fixture.lite.bunnydb.net', 'test-token-only', self.protocol)

    def test_no_credential_urls_or_arbitrary_hosts(self):
        for url in ['http://fixture.lite.bunnydb.net', 'https://localhost',
                    'https://bunnydb.net.attacker.invalid', 'https://user:key@fixture.bunnydb.net',
                    'libsql://fixture.bunnydb.net?authToken=secret', 'https://fixture.bunnydb.net/path']:
            with self.assertRaises(native.NativeError):
                native.SQL(url, 'test')

    def test_successful_sequence_has_no_result_payload(self):
        from unittest.mock import Mock
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({'results': [{'type': 'ok', 'response': {'type': 'sequence'}}]}).encode())
        sql = native.SQL('libsql://fixture.lite.bunnydb.net', 'test', opener)
        self.assertIsNone(sql.script('SELECT 1;'))

    def test_bind_roundtrip_keeps_exact_int64_blob_and_unicode(self):
        self.sql.script('CREATE TABLE sample(id INTEGER PRIMARY KEY,n INTEGER,t TEXT,b BLOB);')
        self.sql.query('INSERT INTO sample VALUES(?,?,?,?)', (1, 2**63-1, "Ёж ' \" — 雪", b'\x00\xff'))
        row = self.sql.query('SELECT n,t,b FROM sample')[0]
        self.assertEqual(row, {'n': 2**63-1, 't': "Ёж ' \" — 雪", 'b': b'\x00\xff'})
        for bad in [float('inf'), float('nan'), 2**63, -(2**63)-1, {'bad': 1}]:
            with self.assertRaises(native.NativeError):
                native.SQL.encode(bad)

    def test_catalog_migrations_and_fts_are_idempotent(self):
        files = [ROOT/'bunny-native/migrations/0001_catalog.sql', ROOT/'bunny-native/migrations/0002_search_fts5.sql']
        self.assertEqual(len(self.sql.migrate(files)), 2)
        self.assertEqual(self.sql.migrate(files), [])
        names = {x['name'] for x in self.sql.query("SELECT name FROM sqlite_master")}
        self.assertTrue({'torrents', 'buckets', 'torrent_fts', 'torrent_ai'} <= names)
        self.assertEqual(len(self.sql.query('SELECT * FROM native_schema_migrations')), 2)

    def test_failed_migration_rolls_back_tables_and_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'0001_failure.sql'
            path.write_text('CREATE TABLE attempted(v TEXT); INSERT INTO missing VALUES(1);')
            with self.assertRaises(native.NativeError) as raised:
                self.sql.migrate([path])
            self.assertNotIn('private-row-value', str(raised.exception))
            self.assertEqual(self.sql.query("SELECT name FROM sqlite_master WHERE name='attempted'"), [])
            self.assertEqual(self.sql.query('SELECT * FROM native_schema_migrations'), [])

    def test_applied_checksum_drift_is_rejected_without_new_schema(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'0001_example.sql'
            path.write_text('CREATE TABLE example(v TEXT);')
            self.sql.migrate([path])
            path.write_text('CREATE TABLE should_not_exist(v TEXT);')
            with self.assertRaisesRegex(native.NativeError, 'checksum changed'):
                self.sql.migrate([path])
            self.assertEqual(self.sql.query("SELECT name FROM sqlite_master WHERE name='should_not_exist'"), [])


if __name__ == '__main__':
    unittest.main()
