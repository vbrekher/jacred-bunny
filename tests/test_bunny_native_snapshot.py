"""Interactive snapshot protocol; no stateless export fallback."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bunny'))
from native import SQLSession,NativeError


def response(baton='baton'):
    return {'baton':baton,'results':[{'type':'ok','response':{'result':{'cols':[],'rows':[]}}}]}


class SessionTests(unittest.TestCase):
    def test_every_page_uses_baton_and_stream_is_closed(self):
        sql=SQLSession('libsql://fixture.lite.bunnydb.net','test-token')
        sql.exchange=Mock(return_value=response())
        with sql:sql.query('SELECT 1')
        calls=sql.exchange.call_args_list
        self.assertIsNone(calls[0].args[1])
        self.assertTrue(all(x.args[1]=='baton' for x in calls[1:]))
        self.assertEqual(calls[-1].args[0],[{'type':'close'}])
        self.assertIsNone(sql.baton)

    def test_failed_export_rolls_back_and_closes(self):
        sql=SQLSession('libsql://fixture.lite.bunnydb.net','test-token')
        sql.exchange=Mock(return_value=response())
        with self.assertRaisesRegex(ValueError,'fixture'):
            with sql:raise ValueError('fixture')
        self.assertEqual(sql.exchange.call_args_list[-2].args[0][0]['stmt']['sql'],'ROLLBACK')
        self.assertEqual(sql.exchange.call_args.args[0],[{'type':'close'}])

    def test_missing_baton_and_foreign_redirect_fail_closed(self):
        for payload in (response(None),dict(response(),base_url='https://attacker.invalid')):
            sql=SQLSession('libsql://fixture.lite.bunnydb.net','test-token')
            sql.exchange=Mock(return_value=payload)
            with self.assertRaises(NativeError):
                with sql:pass
            self.assertEqual(sql.exchange.call_count,1)
