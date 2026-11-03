import importlib.util
from pathlib import Path
import tempfile
import unittest

P=Path(__file__).resolve().parents[1]/'bunny/native.py'
spec=importlib.util.spec_from_file_location('native_costs',P)
n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)


class CostTests(unittest.TestCase):
    def test_dynamic_published_rates_and_conservative_plan(self):
        rates=n.parse_pricing('CPU cost: **\\$0.02 per CPU per hour**\nRAM Cost: $0.005 per GB per hour\nStorage cost: **$0.10 per GB per month**',
            '$1 monthly minimum', '| $0.02 / 1000s CPU time | $0.20 / million requests |',
            '<p>While in public preview, Bunny Database is free.</p>')
        plan=n.staging_cost_plan({'maxCpuMilliCoresPerPod':8000,'maxMemoryBytesPerPod':32*1024**3},rates=rates)
        self.assertAlmostEqual(plan['estimated_initial_usd'],1.74)
        self.assertFalse(plan['hard_cost_limit'])

    def test_unknown_limits_or_changed_database_billing_fail_closed(self):
        for limits in [{},{'maxCpuMilliCoresPerPod':None,'maxMemoryBytesPerPod':1},
                       {'maxCpuMilliCoresPerPod':float('nan'),'maxMemoryBytesPerPod':1}]:
            with self.assertRaises(n.NativeError):n.staging_cost_plan(limits)
        with self.assertRaisesRegex(n.NativeError,'COST_UNKNOWN'):
            n.parse_pricing('unexpected price format','','','')

    def test_public_download_redirect_host_is_restricted(self):
        for url in ['http://bunny.net/docs','https://bunny.net.attacker.invalid/file','https://key@github.com/file']:
            with self.assertRaises(n.NativeError):n.PublicRedirect.validate(url)
        n.PublicRedirect.validate('https://release-assets.githubusercontent.com/file')

    def test_install_lock_rejects_parallel_orchestration(self):
        with tempfile.TemporaryDirectory() as root:
            with n.installation_lock(root):
                with self.assertRaisesRegex(n.NativeError,'Another install'):
                    with n.installation_lock(root):pass
            with n.installation_lock(root):pass


if __name__=='__main__':unittest.main()
