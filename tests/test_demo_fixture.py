from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from analysis.legacy_adapter import load_legacy_fixture, ROOT
from analysis.analysis_service import analyze_sessions, calculate_statistics, build_path_efficiency_findings
from backend import session_store as store
from scripts.seed_demo import seed_demo


class FixtureTests(unittest.TestCase):
    def test_legacy_sources_and_golden_result(self):
        sessions, sources = load_legacy_fixture()
        self.assertEqual(len(sources), 9)
        self.assertTrue(all(s["order_unknown"] for s in sources))
        self.assertTrue(all(s["derived_identity"] == "fixture:experiment_002" for s in sources))
        golden = json.loads((ROOT / "analysis/output/analysis_result.json").read_bytes())
        result = analyze_sessions(sessions)
        for key in ("sensitivity_profiles", "findings", "limitations"):
            self.assertEqual(result[key], golden[key])
        manifest = json.loads((ROOT / "tests/fixtures/legacy_manifest.json").read_bytes())
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_sample_standard_deviation_and_finding_boundaries(self):
        self.assertEqual(calculate_statistics([1, 2, 3]), {"sample_count": 3, "mean": 2, "median": 2, "std": 1})
        self.assertEqual(calculate_statistics([7])["std"], 0)
        sessions, _ = load_legacy_fixture()
        profiles = analyze_sessions(sessions)["sensitivity_profiles"]
        self.assertEqual(len(build_path_efficiency_findings(profiles)), 1)
        self.assertEqual(build_path_efficiency_findings(profiles[:1]), [])
        for profile in profiles:
            for session in profile["session_consistency"]["sessions"]:
                session["metrics"]["path_efficiency"]["median"] = 0.5
        self.assertEqual(build_path_efficiency_findings(profiles), [])

    def test_demo_seed_is_repeatable_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "demo.db"
            first = seed_demo(destination)
            second = seed_demo(destination)
            self.assertEqual(first, second)
            with patch.object(store, "DB_PATH", destination):
                self.assertEqual(seed_demo(destination), first)
            self.assertEqual(first["session_count"], 9)
            with closing(sqlite3.connect(destination)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 9)
                raw = connection.execute("SELECT raw_data FROM sessions LIMIT 1").fetchone()[0]
                self.assertTrue(json.loads(raw)["syntheticDemo"])
                connection.execute("UPDATE sessions SET raw_data=? WHERE rowid=1", (json.dumps({"syntheticDemo": False}),))
                connection.commit()
            before = destination.read_bytes()
            with self.assertRaises(ValueError): seed_demo(destination)
            self.assertEqual(before, destination.read_bytes())

    def test_seed_refuses_configured_database_without_connecting(self):
        with patch.object(store, "_connect", side_effect=AssertionError("Must not access training DB")):
            with self.assertRaises(ValueError): seed_demo(store.DB_PATH)


if __name__ == "__main__":
    unittest.main()
