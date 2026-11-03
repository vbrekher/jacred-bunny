"""No-network JacRed Bunny installer regression suite."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "bunny" / "cli.py"
spec = importlib.util.spec_from_file_location("jacred_bunny_cli", MODULE)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

class InstallerTests(unittest.TestCase):
    def test_image_parser(self):
        self.assertEqual(cli.image_parts(cli.IMAGE)["imageNamespace"], "vbrekher")
        for bad in ("ghcr.io/vbrekher/jacred-bunny", "https://ghcr.io/vbrekher/jacred-bunny:main",
                    "ghcr.io/x/y:main;rm", "docker.io/x/y:main"):
            with self.subTest(bad=bad), self.assertRaises(cli.DeploymentError):
                cli.image_parts(bad)

    def test_numeric_registry_only(self):
        values = {"items": [
            {"hostName": "ghcr.io", "id": "github"},
            {"hostName": "https://ghcr.io", "id": 11126},
            {"hostName": "ghcr.io", "id": 11126},
            {"hostName": "ghcr.io.invalid", "id": 123}
        ]}
        self.assertEqual(cli.registry_ids(values), ["11126"])

    def test_registry_invalid_response_fails_closed(self):
        with self.assertRaises(cli.DeploymentError):
            cli.registry_ids({})

    def test_payload_persistent_volumes_and_https(self):
        x = cli.build_payload("AMS", cli.IMAGE, "11126", "a"*50, "b"*50, 10)
        self.assertEqual(x["autoScaling"], {"min": 1, "max": 1})
        self.assertEqual({v["name"] for v in x["volumes"]}, {"data", "config"})
        self.assertEqual({m["mountPath"] for m in x["containerTemplates"][0]["volumeMounts"]},
                         {"/app/Data", "/app/config"})
        self.assertEqual(x["containerTemplates"][0]["endpoints"][0]["cdn"]["portMappings"],
                         [{"containerPort": 9117, "exposedPort": 80}])
        self.assertEqual(x["containerTemplates"][0]["imageRegistryId"], "11126")

    def test_wrong_registry_rejected(self):
        with self.assertRaises(cli.DeploymentError):
            cli.build_payload("AMS", cli.IMAGE, "github", "a", "b", 10)

    def test_unsafe_endpoints_filtered(self):
        self.assertIsNone(cli.safe_https("http://example.com"))
        self.assertIsNone(cli.safe_https("https://example.com:8090"))
        self.assertIsNone(cli.safe_https("https://127.0.0.1"))
        self.assertIsNone(cli.safe_https("https://example.com/@admin"))
        self.assertIsNone(cli.extract_endpoint({"items": [{"type": "anycast", "publicHost": "1.2.3.4"}]}))
        self.assertEqual(cli.extract_endpoint({"items": [{"type": "cdn", "isSslEnabled": True,
                                                         "publicHost": "mc-abc.bunny.run"}]}),
                         "https://mc-abc.bunny.run")

    def test_duplicate_blocks_without_mutation(self):
        with patch.object(cli, "request", return_value={"items": [{"name": cli.APP_NAME, "id": "x"}]}) as req:
            with self.assertRaisesRegex(cli.DeploymentError, "Existing"):
                cli.ensure_no_duplicate("dummy")
            self.assertEqual([x.args[0] for x in req.call_args_list], ["GET"])

    def test_pagination_refuses_loop(self):
        with patch.object(cli, "request", return_value={"items": [], "cursor": "repeat"}):
            with self.assertRaisesRegex(cli.DeploymentError, "cursor"):
                cli.list_apps("dummy")

    def test_missing_digest_prevents_registry_use(self):
        with patch.object(cli, "request", side_effect=[
            {"items": [{"hostName": "ghcr.io", "id": 11126}]},
            {"digest": None}
        ]):
            with self.assertRaisesRegex(cli.DeploymentError, "cannot access"):
                cli.ensure_registry("dummy", cli.IMAGE)

    def test_bunny_500_matching_app_recovered_once(self):
        app = {"name": cli.APP_NAME, "containerTemplates": [
            {"name": "jacred", **cli.image_parts(cli.IMAGE), "imageRegistryId": "11126"}
        ], "regionSettings": {"requiredRegionIds": ["AMS"]}}
        with patch.object(cli, "request", side_effect=[
            cli.DeploymentError("Bunny HTTP 500"),
            {"items": [{"name": cli.APP_NAME, "id": "some-app-id"}]},
            app
        ]) as req:
            ident = cli.create_app_safely("key", {}, "AMS", cli.IMAGE, "11126")
        self.assertEqual(ident, "some-app-id")
        self.assertEqual(len([x for x in req.call_args_list if x.args[0] == "POST"]), 1)

    def test_bunny_500_unrelated_app_not_adopted(self):
        with patch.object(cli, "request", side_effect=[
            cli.DeploymentError("Bunny HTTP 500"),
            {"items": [{"name": cli.APP_NAME, "id": "x"}]},
            {"name": "unrelated", "containerTemplates": []}
        ]) as req:
            with self.assertRaisesRegex(cli.DeploymentError, "may already"):
                cli.create_app_safely("key", {}, "AMS", cli.IMAGE, "11126")
        self.assertEqual(len([x for x in req.call_args_list if x.args[0] == "POST"]), 1)

    def test_config_file_mode_600_and_secret_not_world_readable(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config.json"
            with patch.object(cli, "CONFIG", cfg):
                cli.save_config({"api_key": "test-secret", "app_id": "uuid"})
                self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
                self.assertEqual(cli.load_config()["api_key"], "test-secret")
                os.chmod(cfg, 0o644)
                with self.assertRaises(cli.DeploymentError):
                    cli.load_config()

    def test_lifecycle_rejects_changed_remote_identity(self):
        config = {"api_key": "private", "app_id": "owned-id", "region": "AMS",
                  "image": cli.IMAGE, "registry_id": "11126"}
        with patch.object(cli, "load_config", return_value=config), patch.object(
                cli, "request", return_value={"name": "other-product"}) as req:
            with self.assertRaises(cli.DeploymentError):
                cli.lifecycle("stop")
            self.assertTrue(all(x.args[0] == "GET" for x in req.call_args_list))

    def test_health_failure_is_not_success(self):
        with patch.object(cli.urllib.request, "urlopen", side_effect=TimeoutError):
            with self.assertRaises(cli.DeploymentError):
                cli.probe("https://mc-example.bunny.run")

    def test_config_symlink_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "other-product.json"
            target.write_text("do not change")
            config = Path(root) / "config.json"
            config.symlink_to(target)
            with patch.object(cli, "CONFIG", config):
                with self.assertRaises(cli.DeploymentError):
                    cli.save_config({"api_key": "secret"})
            self.assertEqual(target.read_text(), "do not change")

    def test_self_test(self):
        cli.self_test()

if __name__ == "__main__":
    unittest.main()
