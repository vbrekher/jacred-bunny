import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"tools"/"bunny_native_audit.py"
spec=importlib.util.spec_from_file_location("bunny_native_audit",SCRIPT)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class AuditTests(unittest.TestCase):
    def test_full_tracker_contract(self):
        self.assertEqual(len(a.EXPECTED_TRACKERS),25)
        self.assertEqual(len(set(a.EXPECTED_TRACKERS)),25)

    def test_tracker_inventory_preserves_exact_directory_case(self):
        root = SCRIPT.parents[1]
        actual = {p.relative_to(root).as_posix() for p in (root / 'Infrastructure/Trackers').rglob('*.cs')}
        rows = a.trackers(root)
        for row in rows:
            self.assertTrue(row['parsers'] and row['sync_services'], row['id'])
            for path in row['parsers'] + row['sync_services']:
                self.assertIn(path, actual, 'Case-sensitive CI must find ' + path)

    def test_missing_data_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            disk=a.inspect_disk(Path(d))
        self.assertEqual(disk["state"],"NO_REAL_DATASET")
        self.assertEqual(a.capacity(disk)["state"],"BLOCKED_MISSING_REAL_DATA")

    def test_gzip_is_not_bzip2_despite_extension(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"fdb"/"a1").mkdir(parents=True)
            with gzip.open(root/"masterDb.bz","wb") as f:f.write(b"{}")
            with gzip.open(root/"fdb"/"a1"/("b"*30),"wb") as f:f.write(b'{"a":{},"b":{}}')
            result=a.inspect_disk(root,1)
            self.assertEqual(result["master_files"][0]["gzip_magic"],True)
            self.assertEqual(result["sample"]["records_seen"],2)
            self.assertEqual(result["total_records"],"UNKNOWN_NOT_COUNTED")

    def test_invalid_shard_never_leaks_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"fdb"/"c3").mkdir(parents=True)
            (root/"fdb"/"c3"/("f"*30)).write_bytes(b"VERY_PRIVATE_DATA")
            report=a.inspect_disk(root,1)
            self.assertEqual(report["sample"]["invalid"],1)
            self.assertNotIn("VERY_PRIVATE_DATA",json.dumps(report))

    def test_scenarios_are_only_estimates(self):
        d={"state":"MEASURED_LOCAL_FILE_BYTES","total_source_bytes":250_000_000}
        c=a.capacity(d)
        self.assertEqual([x["shards_estimated"] for x in c["scenarios"]],[1,2,3])
        self.assertEqual(c["state"],"HYPOTHETICAL_NOT_ACTUAL_SQL_SIZE")

    def test_local_sqlite_fts5(self):
        p=a.local_sqlite_probe(150)
        self.assertEqual(p["fts_hits"],150)
        self.assertEqual(p["type"],"LOCAL_SQLITE_ONLY_NOT_BUNNY")

    def test_route_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"Controllers").mkdir()
            (root/"Controllers"/"DemoController.cs").write_text("""
[Route("/api/[controller]/[action]")]
public class DemoController {
[HttpGet]
public IActionResult Read() => Ok();
[HttpPost("create")]
public IActionResult Create() => Ok();
[Route("/absolute")]
public IActionResult Custom() => Ok();
}
""")
            values=a.extract_routes(root)
            self.assertEqual({x["route"] for x in values},
                {"/api/Demo/Read","/api/Demo/Create/create","/absolute"})


    def test_git_checkout_placeholder_is_not_a_database(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/"fdb").mkdir()
            (root/"fdb"/".gitignore").write_text("*\n!.gitignore\n")
            report = a.inspect_disk(root, sample_limit=20)
            self.assertEqual(report["state"], "NO_REAL_DATASET")
            self.assertEqual(report["fdb_files"], 0)
            self.assertEqual(report["total_source_bytes"], 0)
            self.assertEqual(report["sample"]["checked"], 0)
            self.assertEqual(report["ignored_non_shard_fdb_files"], 1)
            self.assertEqual(a.capacity(report)["state"], "BLOCKED_MISSING_REAL_DATA")

    def test_only_recognized_md5_shard_paths_are_counted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/"fdb"/"f").mkdir(parents=True)
            (root/"fdb"/"c0").mkdir(parents=True)
            (root/"fdb"/"not-a-shard").mkdir(parents=True)
            with gzip.open(root/"fdb"/"f"/("a"*32), "wb") as fh:
                fh.write(b'{"oldLayout":{}}')
            with gzip.open(root/"fdb"/"c0"/("b"*30), "wb") as fh:
                fh.write(b'{"newLayout":{}}')
            (root/"fdb"/"not-a-shard"/"README.txt").write_text("placeholder")
            report = a.inspect_disk(root, sample_limit=10)
            self.assertEqual(report["state"], "MEASURED_LOCAL_FILE_BYTES")
            self.assertEqual(report["fdb_files"], 2)
            self.assertEqual(report["ignored_non_shard_fdb_files"], 1)
            self.assertEqual(report["sample"]["records_seen"], 2)

    def test_invalid_master_file_is_not_valid_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/"fdb").mkdir()
            (root/"masterDb.bz").write_bytes(b"not a gzip database")
            report = a.inspect_disk(root)
            self.assertEqual(report["state"], "NO_REAL_DATASET")
            self.assertEqual(report["master_files"][0]["gzip_magic"], False)
            self.assertEqual(report["valid_gzip_master_files"], 0)
            self.assertEqual(report["total_source_bytes"], 0)

    def test_track_placeholder_alone_not_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/"tracks").mkdir()
            (root/"tracks"/".gitignore").write_text("*")
            (root/"tracks"/"README.md").write_text("info")
            report = a.inspect_disk(root)
            self.assertEqual(report["state"], "NO_REAL_DATASET")
            self.assertEqual(report["track_files"], 0)

    def test_actual_repo_source_contract(self):
        report=a.audit(SCRIPT.parents[1])
        self.assertEqual(report["tracker_count"],25)
        self.assertGreaterEqual(report["route_count"],15)
        self.assertTrue(any(r["route"].startswith("/api/") for r in report["routes"]))
        self.assertEqual([t["id"] for t in report["trackers"]],a.EXPECTED_TRACKERS)
        self.assertTrue(all(t["live"]=="NOT_TESTED" for t in report["trackers"]))

if __name__=="__main__":
    unittest.main()
