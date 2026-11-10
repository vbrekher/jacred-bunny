"""Publication crash boundaries with fake GitHub only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bunny'))
from native import NativeError
spec=importlib.util.spec_from_file_location('candidate',ROOT/'tools/bunny_candidate_release.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        files={}
        for name in ('edge.js',*('0001_catalog.sql','0002_search_fts5.sql','0004_catalog_commits.sql')):
            (self.root/name).write_text('test-only fixture')
            files[name]=hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        manifest=dict(schema='jacred-bunny-native-payload/v2',source_sha='a'*40,
            catalog_backend='bunny-sql',edge_mode='original-api-gateway',files=files,
            image=dict(reference='ghcr.io/vbrekher/jacred-bunny:sha-'+'a'*40,digest='sha256:'+'b'*64))
        (self.root/'manifest.json').write_text(json.dumps(manifest))
        (self.root/'image-digest.txt').write_text('ghcr.io/vbrekher/jacred-bunny@sha256:'+'b'*64+'\n')
        (self.root/'jacred-bunny-sbom.spdx.json').write_text('{}')
        (self.root/'sbom.sha256').write_text(hashlib.sha256(b'{}').hexdigest()+'  jacred-bunny-sbom.spdx.json\n')
        self.env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=candidate.REPO,GITHUB_SHA='a'*40)

    def test_default_plan_has_no_network_or_subprocess(self):
        with patch.object(candidate,'api',side_effect=AssertionError('network')),patch.object(candidate.subprocess,'run',side_effect=AssertionError('subprocess')):
            self.assertFalse(candidate.publish(self.root)['apply'])

    def test_existing_release_is_never_updated(self):
        with patch.dict(os.environ,self.env),patch.object(candidate,'find_release',return_value={'draft':False}),patch.object(candidate.subprocess,'run') as run:
            with self.assertRaisesRegex(NativeError,'already exists'):candidate.publish(self.root,True)
            run.assert_not_called()

    def test_failed_asset_proof_retains_unpublished_draft(self):
        with patch.dict(os.environ,self.env),patch.object(candidate,'find_release',side_effect=[None,dict(draft=True,prerelease=True,target_commitish='a'*40,assets=[])]),patch.object(candidate.subprocess,'run') as run:
            with self.assertRaisesRegex(NativeError,'Draft identity'):candidate.publish(self.root,True)
            self.assertEqual(run.call_count,1)
            self.assertIn('--draft',run.call_args.args[0])

    def test_complete_digest_proof_allows_publication(self):
        plan=candidate.publish(self.root)
        assets=[dict(name=name,state='uploaded',digest='sha256:'+digest) for name,digest in plan['assets'].items()]
        with patch.dict(os.environ,self.env),patch.object(candidate,'find_release',side_effect=[None,dict(draft=True,prerelease=True,target_commitish='a'*40,assets=assets)]),patch.object(candidate.subprocess,'run') as run:
            result=candidate.publish(self.root,True)
            self.assertEqual(run.call_count,2)
            self.assertIn('--draft=false',run.call_args.args[0]);self.assertFalse(result['production_ready'])

    def test_private_draft_is_found_when_tag_endpoint_omits_it(self):
        draft={'tag_name':'test','draft':True}
        with patch.object(candidate,'api',side_effect=[None,[draft]]):
            self.assertEqual(candidate.find_release('test'),draft)

    def test_corrupt_local_identity_fails_before_api(self):
        (self.root/'image-digest.txt').write_text('unexpected image')
        with patch.object(candidate,'api') as api:
            with self.assertRaisesRegex(NativeError,'identity'):candidate.publish(self.root,True)
            api.assert_not_called()
