"""Remaining engineering regressions; no real DB, provider or API key is used."""
import asyncio
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import test_session_contract as contract
import test_experiment_consistency as fixtures
from analysis.analysis_service import analyze_sessions, calculate_statistics
from backend import session_store as store
from scripts.backup_database import backup_database


class AnalysisBoundaryTests(unittest.TestCase):
    def test_statistics_overflow_is_a_controlled_data_error(self):
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            calculate_statistics([1e308, 1e308])

    def test_duplicate_observations_do_not_inflate_samples(self):
        session = contract.valid_session()
        with self.assertRaisesRegex(ValueError, "duplicate sessionId"):
            analyze_sessions([session, deepcopy(session)])
        self.assertEqual(analyze_sessions([session])["experiment"]["attempt_count"], 1)

    def test_finite_coordinates_with_overflowing_path_are_rejected(self):
        session = contract.valid_session()
        session["attempts"][0]["target"]["cursorStartX"] = -1e308
        session["attempts"][0]["movements"][0]["x"] = 1e308
        self.assertEqual(contract.validate_session_integrity(session), [])
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            analyze_sessions([session])


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.directory / "source.db"
        self.enterContext(patch.object(store, "DB_PATH", self.db))
        self.enterContext(patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}))
        store.init_database()

    def test_backup_restore_preserves_exact_rows_and_never_overwrites(self):
        state = store.get_or_create_experiment(800, 600)
        store.save_session(fixtures.make_session(state))
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        snapshot, restored = self.directory / "backup.db", self.directory / "restored.db"
        self.assertEqual(backup_database(self.db, snapshot)["session_count"], 1)
        backup_database(snapshot, restored)
        def rows(path):
            with closing(sqlite3.connect(path)) as connection:
                return {name: connection.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                        for name in ("sessions", "experiments")}
        self.assertEqual(rows(self.db), rows(restored))
        for destination in (self.db, snapshot):
            with self.subTest(destination=destination), self.assertRaises((ValueError, FileExistsError)):
                backup_database(snapshot, destination)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_backup_includes_committed_wal_and_rejects_invalid_source(self):
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO sessions(session_id, experiment_id, schema_version, started_at, trainer_sensitivity, target_count, raw_data) VALUES ('wal','wal','0.1','now',1,1,'{}')")
            connection.commit()
            snapshot = self.directory / "wal-backup.db"
            self.assertEqual(backup_database(self.db, snapshot)["session_count"], 1)
        invalid = self.directory / "invalid.db"
        invalid.write_text("invalid sqlite", encoding="utf-8")
        failed = self.directory / "failed.db"
        with self.assertRaises(sqlite3.Error):
            backup_database(invalid, failed)
        self.assertFalse(failed.exists())

    def test_health_and_storage_errors_are_safe_without_model_calls(self):
        main = importlib.import_module("backend.main")
        def request(method, path, data=None):
            return asyncio.run(contract.asgi_request(main.app, method, path, data))
        with patch.object(main, "run_experiment_planner", side_effect=AssertionError("No paid calls")):
            self.assertEqual(request("GET", "/health"), (200, {"storage": "ready", "agent": "unavailable"}))
        missing = self.directory / "missing.db"
        with patch.object(store, "DB_PATH", missing):
            self.assertEqual(request("GET", "/health")[0], 503)
            self.assertFalse(missing.exists())
        with patch.object(main, "get_session", side_effect=sqlite3.OperationalError("secret path")):
            status, response = request("GET", "/sessions/example")
            self.assertEqual(status, 503)
            self.assertNotIn("secret", json.dumps(response))
        with patch.object(main, "get_session", side_effect=json.JSONDecodeError("bad", "{", 0)):
            self.assertEqual(request("GET", "/sessions/example")[0], 409)
        state = store.get_or_create_experiment(800, 600)
        session = fixtures.make_session(state)
        store.save_session(session)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE sessions SET raw_data=?", (json.dumps({**session, "sessionId": "wrong"}),))
        self.assertEqual(request("GET", "/sessions/" + session["sessionId"])[0], 409)
        for payload in ({"goal": " ", "experiment_id": "id"},
                        {"goal": "ok", "experiment_id": " "},
                        {"goal": 1, "experiment_id": "id"},
                        {"goal": "ok", "experiment_id": "id", "extra": True}):
            with self.subTest(payload=payload):
                self.assertEqual(request("POST", "/agent/plan", payload)[0], 422)

    def test_model_configuration_and_client_cleanup_on_both_paths(self):
        from agent import experiment_planner as planner
        with patch.dict(os.environ, {"AIMCOACH_MODEL": "configured-model", "AIMCOACH_MODEL_BASE_URL": "https://example.invalid/v1"}):
            self.assertEqual(planner.model_settings(), ("configured-model", "https://example.invalid/v1"))
        for endpoint in ("file:///secret", "https://user:secret@example.com", "https://example.com?key=secret", "https://example.com:bad"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"AIMCOACH_MODEL_BASE_URL": endpoint}):
                with self.assertRaises(planner.ModelUnavailable):
                    planner.model_settings()
        state = store.get_or_create_experiment(800, 600)
        store.save_session(fixtures.make_session(state))
        for success in (True, False):
            client = fixtures.FakeClient(None if success else lambda **kw: fixtures.response("No evidence"))
            client.close = Mock()
            runtime = fixtures.load_planner(client)
            if success:
                result = runtime.run_experiment_planner("goal", state["experiment_id"])
                self.assertEqual(result["usage"]["model"], runtime.MODEL)
            else:
                with self.assertRaises(runtime.PlannerFailure):
                    runtime.run_experiment_planner("goal", state["experiment_id"])
            client.close.assert_called_once_with()

    def test_tool_extra_claims_and_storage_failure_are_not_provider_success(self):
        state = store.get_or_create_experiment(800, 600)
        store.save_session(fixtures.make_session(state))
        runtime = fixtures.load_planner(fixtures.FakeClient())
        from analysis.analysis_runtime import create_analysis_snapshot
        snapshot = create_analysis_snapshot(state["experiment_id"])
        summary = runtime.get_analysis_summary(snapshot=snapshot)
        with self.assertRaises(ValueError):
            runtime.verify_tool_result("get_analysis_summary", {}, {**summary, "best_sensitivity": 999}, snapshot)
        with patch.object(runtime, "create_analysis_snapshot", side_effect=sqlite3.OperationalError("secret")):
            with self.assertRaises(runtime.PlannerFailure) as failure:
                runtime.run_experiment_planner("goal", state["experiment_id"])
        self.assertEqual(failure.exception.http_status, 503)
        self.assertEqual(failure.exception.detail["code"], "storage_unavailable")
        self.assertNotIn("secret", json.dumps(failure.exception.detail))
