import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('tracker_probe', ROOT / 'tools/bunny_tracker_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TrackerProbeTests(unittest.TestCase):
    def test_roster_and_overrides(self):
        result = probe.report(ROOT)
        self.assertEqual(len(result['trackers']), 25)
        self.assertTrue(all(x['host'] and x['source_sha256'] for x in result['trackers']))
        self.assertTrue(all(not x['parser_ready'] and x['status'] == 'NOT_TESTED' for x in result['trackers']))
        self.assertFalse(result['parser_acceptance_pass'])
        self.assertEqual(probe.configured_hosts(ROOT)['subsplease'], 'https://subsplease.org')

    def test_reachability_is_never_ready(self):
        for status in [200, 301, 404, 405, 429]:
            self.assertEqual(probe.classify(status), 'LIMITED')
        self.assertEqual(probe.classify(401), 'NEEDS_AUTH')
        self.assertEqual(probe.classify(403), 'BLOCKED_POLICY')
        self.assertEqual(probe.classify(503), 'UPSTREAM_UNAVAILABLE')

    def test_unsafe_addresses_and_credentials_blocked(self):
        for url in ['https://host.invalid/?apikey=secret', 'https://user:secret@host.invalid/',
                    'file:///tmp/source', 'https://host.invalid/path']:
            with self.assertRaises(ValueError):
                probe.safe_host(url)
        with patch.object(probe.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                probe.safe_host('https://host.invalid')


if __name__ == '__main__':
    unittest.main()
