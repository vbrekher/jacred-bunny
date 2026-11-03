import importlib.util
from pathlib import Path
import tempfile
import unittest

P=Path(__file__).resolve().parents[1]/'bunny/native.py'
spec=importlib.util.spec_from_file_location('native_cdn',P)
n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)


class CDNTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.calls=[];self.edge_link=42
        self.zones={42:{'Name':'owned-edge','MonthlyBandwidthLimit':0,'CacheErrorResponses':True},
                    43:{'Name':'owned-worker','MonthlyBandwidthLimit':0,'CacheErrorResponses':True}}
        def api(method,path,body=None):
            self.calls.append((method,path))
            if path=='/compute/script/1':return {'Name':'fixture','LinkedPullZones':[{'Id':self.edge_link}]}
            if path=='/mc/apps/worker':return {'name':'fixture','containerTemplates':[{'endpoints':[{'type':'cdn','pullZoneId':'43'}]}]}
            ident=int(path.rsplit('/',1)[-1])
            if method=='POST':self.zones[ident].update(body)
            return self.zones[ident]
        self.deploy=n.Deployment(self.tmp.name,api)
        self.deploy.initialize('a'*40,'ghcr.io/vbrekher/jacred-bunny:main','sha256:'+'b'*64)
        self.deploy.state['resources']={
            'edge':{'id':1,'name':'fixture','status':'created','get_path':'/compute/script/{id}','name_field':'Name'},
            'worker':{'id':'worker','name':'fixture','status':'created','get_path':'/mc/apps/{id}','name_field':'name'}}
        self.deploy.save()

    def test_caps_are_read_back_and_only_linked_zones_changed(self):
        self.deploy.bound_staging_cdn()
        self.assertEqual(self.zones[42]['MonthlyBandwidthLimit'],10_000_000)
        self.assertEqual(self.zones[43]['MonthlyBandwidthLimit'],25_000_000)
        self.assertFalse(self.zones[42]['CacheErrorResponses'])
        self.assertFalse(self.zones[43]['CacheErrorResponses'])
        self.assertEqual([x for x in self.calls if x[0]=='POST'],[('POST','/pullzone/42'),('POST','/pullzone/43')])

    def test_changed_link_never_mutates_another_zone(self):
        self.deploy.bound_staging_cdn()
        self.edge_link=999
        self.calls.clear()
        with self.assertRaisesRegex(n.NativeError,'zone changed'):
            self.deploy.bound_staging_cdn()
        self.assertFalse(any(method=='POST' for method,_ in self.calls))


if __name__=='__main__':unittest.main()
