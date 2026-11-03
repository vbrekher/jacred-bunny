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
        self.calls=[];self.edge_link=42;self.uncertain=False;self.drop_rule=False
        self.zones={42:{'Name':'owned-edge','MonthlyBandwidthLimit':0,'CacheErrorResponses':True},
                    43:{'Name':'owned-worker','MonthlyBandwidthLimit':0,'CacheErrorResponses':True}}
        def api(method,path,body=None):
            self.calls.append((method,path))
            if path=='/compute/script/1':return {'Name':'fixture','LinkedPullZones':[{'Id':self.edge_link}]}
            if path=='/mc/apps/worker':return {'name':'fixture','containerTemplates':[{'endpoints':[{'type':'cdn','pullZoneId':'43'}]}]}
            if '/edgerules/' in path:
                ident=int(path.split('/')[2])
                if not self.drop_rule:
                    rule=dict(body,Guid='fixture-rule-'+str(ident))
                    self.zones[ident].setdefault('EdgeRules',[]).append(rule)
                if self.uncertain:raise n.NativeError('simulated response loss')
                return None
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
        self.assertEqual([x for x in self.calls if x[0]=='POST'],[('POST','/pullzone/42'),('POST','/pullzone/43'),('POST','/pullzone/42/edgerules/addOrUpdate'),('POST','/pullzone/43/edgerules/addOrUpdate')])

    def test_changed_link_never_mutates_another_zone(self):
        self.deploy.bound_staging_cdn()
        self.edge_link=999
        self.calls.clear()
        with self.assertRaisesRegex(n.NativeError,'zone changed'):
            self.deploy.bound_staging_cdn()
        self.assertFalse(any(method=='POST' for method,_ in self.calls))

    def test_repeat_reuses_exact_owned_rules_without_duplicate_creation(self):
        self.deploy.bound_staging_cdn();self.deploy.bound_staging_cdn()
        self.assertEqual(len([path for method,path in self.calls if '/edgerules/' in path]),2)
        self.assertEqual(len(self.zones[42]['EdgeRules']),1)
        self.assertEqual(self.deploy.state['cdn_no_store_rules']['edge_cdn']['phase'],'confirmed')

    def test_lost_response_adopts_only_confirmed_rule_without_replay(self):
        self.uncertain=True;self.deploy.bound_staging_cdn()
        self.assertEqual(len([path for method,path in self.calls if '/edgerules/' in path]),2)

    def test_missing_uncertain_rule_retains_intent_without_replaying_post(self):
        self.uncertain=True;self.drop_rule=True
        with self.assertRaisesRegex(n.NativeError,'not confirmed'):self.deploy.bound_staging_cdn()
        before=len([path for method,path in self.calls if '/edgerules/' in path])
        with self.assertRaisesRegex(n.NativeError,'no POST replay'):self.deploy.bound_staging_cdn()
        self.assertEqual(before,len([path for method,path in self.calls if '/edgerules/' in path]))

    def test_rule_drift_is_not_overwritten(self):
        self.deploy.bound_staging_cdn();self.calls.clear()
        self.zones[42]['EdgeRules'][0]['ActionParameter2']='public,max-age=999'
        with self.assertRaisesRegex(n.NativeError,'drift'):self.deploy.bound_staging_cdn()
        self.assertFalse(any('/edgerules/' in path for _,path in self.calls))


if __name__=='__main__':unittest.main()
