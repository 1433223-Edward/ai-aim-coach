"""Offline regression tests. All storage/API writes use TemporaryDirectory DBs.

Run: python -B -m unittest discover -s tests -v
Requires the project's FastAPI environment, but no pytest/httpx/Qwen key/network.
"""
import asyncio
from contextlib import closing
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from analysis.analysis_service import analyze_sessions
from analysis.analysis_runtime import analyze_experiment
from analysis.session_validator import validate_session_integrity
from backend import session_store as store


ROOT = Path(__file__).resolve().parents[1]


def valid_session():
    return {
        "schemaVersion": "0.1", "sessionId": "fixture-session",
        "experimentId": "fixture-experiment",
        "protocolVersion": "1", "inputMode": "unadjusted_requested",
        "experimentOrder": 1, "sensitivityRepeat": 1,
        "startedAt": "2026-09-06T00:00:00.000Z",
        "endedAt": "2026-09-06T00:00:01.000Z",
        "startTime": 0, "endTime": 40,
        "trainerSensitivity": 1.0, "targetCount": 1,
        "trainerWidth": 100, "trainerHeight": 100,
        "attempts": [{
            "target": {"x": 50, "y": 50, "radius": 10,
                       "cursorStartX": 0, "cursorStartY": 50,
                       "appearTime": 10, "disappearTime": 31},
            "movements": [
                {"time": 20, "rawDX": 25, "rawDY": 0, "x": 25, "y": 50},
                {"time": 30, "rawDX": 25, "rawDY": 0, "x": 50, "y": 50},
            ],
            "clicks": [
                {"time": 20, "x": 25, "y": 50, "hit": False},
                {"time": 30, "x": 50, "y": 50, "hit": True},
            ],
        }],
    }


def invalid_sessions():
    """Each case starts from an independent, valid synthetic Session."""
    changes = {
        "negative_hit_time": lambda s, a: a["clicks"][-1].update(time=9),
        "forged_hit_coordinates": lambda s, a: a["clicks"][-1].update(x=1e9, y=1e9),
        "false_miss": lambda s, a: a["clicks"][0].update(x=50),
        "movement_null": lambda s, a: a.update(movements=[None]),
        "movement_string": lambda s, a: a.update(movements=["mousemove"]),
        "click_list": lambda s, a: a.update(clicks=[[]]),
        "attempt_null": lambda s, a: s.update(attempts=[None]),
        "target_list": lambda s, a: a.update(target=[]),
        "nonnumeric_target": lambda s, a: a["target"].update(x="bad"),
        "numeric_string": lambda s, a: a["movements"][0].update(time="20"),
        "bool_delta": lambda s, a: a["movements"][0].update(rawDX=True),
        "numeric_hit": lambda s, a: a["clicks"][-1].update(hit=1),
        "string_hit": lambda s, a: a["clicks"][-1].update(hit="true"),
        "missing_hit": lambda s, a: a["clicks"][-1].pop("hit"),
        "missing_delta": lambda s, a: a["movements"][0].pop("rawDY"),
        "missing_time": lambda s, a: s.pop("startTime"),
        "empty_clicks": lambda s, a: a.update(clicks=[]),
        "missing_movements": lambda s, a: a.pop("movements"),
        "empty_attempts": lambda s, a: s.update(attempts=[]),
        "attempts_object": lambda s, a: s.update(attempts={}),
        "wrong_count": lambda s, a: s.update(targetCount=2),
        "bool_count": lambda s, a: s.update(targetCount=True),
        "float_count": lambda s, a: s.update(targetCount=1.0),
        "zero_sensitivity": lambda s, a: s.update(trainerSensitivity=0),
        "string_sensitivity": lambda s, a: s.update(trainerSensitivity="1.0"),
        "negative_radius": lambda s, a: a["target"].update(radius=-10),
        "zero_width": lambda s, a: s.update(trainerWidth=0),
        "missing_experiment": lambda s, a: s.pop("experimentId"),
        "empty_session_id": lambda s, a: s.update(sessionId=" "),
        "invalid_wall_time": lambda s, a: s.update(startedAt="bad"),
        "wall_time_type": lambda s, a: s.update(endedAt=42),
        "wall_time_reversed": lambda s, a: s.update(endedAt="2026-09-05T00:00:00Z"),
        "mixed_timezones": lambda s, a: s.update(endedAt="2026-09-06T00:00:01"),
        "reversed_session": lambda s, a: s.update(endTime=-1),
        "target_before_session": lambda s, a: s.update(startTime=11),
        "target_after_session": lambda s, a: s.update(endTime=30),
        "disappear_before_appear": lambda s, a: a["target"].update(disappearTime=9),
        "event_after_disappear": lambda s, a: a["target"].update(disappearTime=29),
        "movement_before_appear": lambda s, a: a["movements"][0].update(time=9),
        "movement_after_final_hit": lambda s, a: a["movements"][-1].update(time=31),
        "movement_not_monotonic": lambda s, a: a["movements"][0].update(time=31),
        "click_not_monotonic": lambda s, a: a["clicks"][0].update(time=31),
        "early_hit": lambda s, a: a["clicks"][0].update(hit=True, x=50),
        "final_miss": lambda s, a: a["clicks"][-1].update(hit=False, x=25),
        "zero_repeat": lambda s, a: s.update(sensitivityRepeat=0),
        "bool_order": lambda s, a: s.update(experimentOrder=True),
        "nan_coordinate": lambda s, a: a["target"].update(x=float("nan")),
        "infinite_time": lambda s, a: a["clicks"][-1].update(time=float("inf")),
        "nan_count": lambda s, a: s.update(targetCount=float("nan")),
        "infinite_sensitivity": lambda s, a: s.update(trainerSensitivity=float("inf")),
        "nan_extra": lambda s, a: s.update(extra=float("nan")),
    }
    for name, change in changes.items():
        session = valid_session()
        change(session, session["attempts"][0])
        yield name, session


class ContractTests(unittest.TestCase):
    def test_hand_calculated_metrics(self):
        session = valid_session()
        original = deepcopy(session)
        self.assertEqual(validate_session_integrity(session), [])
        result = analyze_sessions([session])
        metrics = result["sensitivity_profiles"][0]["metrics"]
        for name, expected in {"final_hit_time_ms": 20, "miss_count": 1,
                               "first_click_error_px": 25, "path_efficiency": 1}.items():
            self.assertEqual(metrics[name]["mean"], expected)
        self.assertEqual(session, original)

    def test_all_negative_cases_rejected_by_validator_and_analysis(self):
        for name, session in invalid_sessions():
            with self.subTest(case=name):
                self.assertTrue(validate_session_integrity(session))
                with self.assertRaises(ValueError):
                    analyze_sessions([session])

    def test_invalid_root_types_are_controlled_errors(self):
        for value in (None, [], "session", 1, True):
            with self.subTest(value=value):
                self.assertTrue(validate_session_integrity(value))
                with self.assertRaises(ValueError):
                    analyze_sessions([value])
        for value in (None, [], {}, "dataset"):
            with self.assertRaises(ValueError):
                analyze_sessions(value)

    def test_zero_movement_equal_timestamps_and_circle_boundary(self):
        session = valid_session()
        a = session["attempts"][0]
        a["target"].update(cursorStartX=60, appearTime=10, disappearTime=10)
        a.update(movements=[], clicks=[{"time": 10, "x": 60, "y": 50, "hit": True}])
        session["endTime"] = 10
        self.assertEqual(validate_session_integrity(session), [])
        metrics = analyze_sessions([session])["sensitivity_profiles"][0]["metrics"]
        self.assertEqual(metrics["final_hit_time_ms"]["mean"], 0)
        self.assertEqual(metrics["path_efficiency"]["mean"], 1)

    def test_multiple_misses_are_valid(self):
        session = valid_session()
        session["attempts"][0]["clicks"].insert(1, {"time": 20, "x": 25, "y": 50, "hit": False})
        self.assertEqual(validate_session_integrity(session), [])
        result = analyze_sessions([session])
        self.assertEqual(result["sensitivity_profiles"][0]["metrics"]["miss_count"]["mean"], 2)

    def test_self_consistent_client_data_is_not_proof_of_real_actions(self):
        session = valid_session()
        # A client can fabricate an entire internally consistent report.
        # The validator deliberately does not pretend to authenticate it.
        a = session["attempts"][0]
        a["target"]["x"] += 5
        a["target"]["cursorStartX"] += 5
        for event in a["movements"] + a["clicks"]:
            event["x"] += 5
        self.assertEqual(validate_session_integrity(session), [])

    def test_attempt_ordering_and_mixed_experiments(self):
        session = valid_session()
        session["targetCount"] = 2
        session["attempts"].append(deepcopy(session["attempts"][0]))
        self.assertTrue(validate_session_integrity(session))
        with self.assertRaises(ValueError):
            analyze_sessions([session])
        other = valid_session()
        other.update(sessionId="other", experimentId="other")
        with self.assertRaises(ValueError):
            analyze_sessions([valid_session(), other])

    def test_all_versioned_historical_json_without_mutation(self):
        files = sorted((ROOT / "data").glob("experiment_*/**/*.json"))
        self.assertEqual(len(files), 19)
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                content = path.read_bytes()
                session = json.loads(content)
                original = deepcopy(session)
                # Explicit inspection mode only, never used by storage/analysis.
                self.assertEqual(validate_session_integrity(session, require_experiment_id=False), [])
                if "experimentId" not in session:
                    self.assertTrue(validate_session_integrity(session))
                    with self.assertRaises(ValueError):
                        analyze_sessions([session])
                self.assertEqual(session, original)
                self.assertEqual(path.read_bytes(), content)

    def test_historical_analysis_matches_retained_golden_output(self):
        sessions = [json.loads(p.read_bytes()) for p in sorted(
            (ROOT / "data" / "experiment_002").glob("*.json"))]
        # Synthetic identity on in-memory copies is NOT recovered provenance.
        for session in sessions:
            session["experimentId"] = "test-only-legacy-group"
        result = analyze_sessions(sessions)
        golden = json.loads((ROOT / "analysis/output/analysis_result.json").read_bytes())
        for key in ("sensitivity_profiles", "findings", "limitations"):
            self.assertEqual(result[key], golden[key])
        self.assertEqual(result["experiment"]["attempt_count"], 180)


class TemporaryDatabaseCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db_path = Path(directory) / "contract-test.db"
        self.enterContext(patch.object(store, "DB_PATH", self.db_path))
        store.init_database()
        # Register the existing one-target fixture as a managed test protocol.
        # This is synthetic setup in a temporary DB, not migration of real sessions.
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            protocol = {
                "version": "1", "session_schema_version": "0.1",
                "target_count": 1, "target_radius": 10,
                "trainer_width": 100, "trainer_height": 100,
            }
            connection.execute(
                "INSERT INTO experiments (experiment_id, protocol, schedule, active_slot) VALUES (?, ?, ?, 1)",
                ("fixture-experiment", json.dumps(protocol),
                 json.dumps([{"sensitivity": 1.0, "repeat": 1}])),
            )

    def row_count(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute("SELECT count(*) FROM sessions").fetchone()[0]


class StorageTests(TemporaryDatabaseCase):
    def test_idempotency_does_not_confuse_json_booleans_with_numbers(self):
        session = {**valid_session(), "extraTag": True}
        store.save_session(session)
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_session({**session, "extraTag": 1})
        self.assertIs(store.get_session(session["sessionId"])["extraTag"], True)

    def test_valid_round_trip_and_duplicate_preserves_original(self):
        session = valid_session()
        store.save_session(session)
        self.assertEqual(store.get_session(session["sessionId"]), session)
        store.save_session(session)  # Identical retries are acknowledged without a second row.
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_session({**session, "startedAt": "2026-09-05T00:00:00Z"})
        self.assertEqual(self.row_count(), 1)

    def test_direct_storage_rejects_every_negative_case_before_connecting(self):
        for name, session in invalid_sessions():
            with self.subTest(case=name), patch.object(store, "_connect") as connect:
                with self.assertRaises(ValueError):
                    store.save_session(session)
                connect.assert_not_called()
        self.assertEqual(self.row_count(), 0)

    def test_corrupt_existing_row_cannot_enter_analysis(self):
        session = valid_session()
        store.save_session(session)
        session["attempts"][0]["clicks"][-1]["time"] = 9
        # Simulate pre-existing corruption ONLY inside the temporary database.
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE sessions SET raw_data=?", (json.dumps(session),))
        with self.assertRaises(ValueError):
            analyze_experiment(session["experimentId"])
        self.assertEqual(self.row_count(), 1)


async def asgi_request(app, method, path, payload=None):
    """Exercise the real ASGI routes without a server, network or HTTP client."""
    body = json.dumps(payload).encode() if payload is not None else b""
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"},
               "http_version": "1.1", "method": method, "path": path,
               "raw_path": path.encode(), "query_string": b"", "root_path": "",
               "scheme": "http", "server": ("test", 80), "client": ("test", 123),
               "headers": [(b"content-type", b"application/json")]}, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(response)


class ApiTests(TemporaryDatabaseCase):
    def setUp(self):
        super().setUp()
        # main imports the planner and initializes a DB at module import. Isolate
        # both existing side effects; no real planner/key/network is involved.
        stub = types.ModuleType("agent.experiment_planner")
        stub.run_experiment_planner = unittest.mock.Mock(side_effect=AssertionError("Agent must not run"))
        from agent.experiment_planner import ModelUnavailable, PlannerFailure
        stub.ModelUnavailable = ModelUnavailable
        stub.PlannerFailure = PlannerFailure
        stub.model_settings = lambda: ("test-model", "https://example.invalid/v1")
        previous = sys.modules.pop("backend.main", None)
        package = sys.modules["backend"]
        missing = object()
        previous_attribute = getattr(package, "main", missing)
        with patch.dict(sys.modules, {"agent.experiment_planner": stub}):
            self.main = importlib.import_module("backend.main")
        def restore_main():
            if previous is None:
                sys.modules.pop("backend.main", None)
            else:
                sys.modules["backend.main"] = previous
            if previous_attribute is missing:
                delattr(package, "main")
            else:
                package.main = previous_attribute
        self.addCleanup(restore_main)

    def request(self, method, path, payload=None):
        return asyncio.run(asgi_request(self.main.app, method, path, payload))

    def test_api_rejects_all_negative_cases_without_writes(self):
        for name, session in invalid_sessions():
            with self.subTest(case=name):
                status, body = self.request("POST", "/sessions", session)
                self.assertEqual(status, 422, body)
                self.assertIn("detail", body)
                self.assertEqual(self.row_count(), 0)

    def test_api_round_trip_duplicate_and_not_found(self):
        session = valid_session()
        self.assertEqual(self.request("POST", "/sessions", session)[0], 200)
        self.assertEqual(self.request("GET", "/sessions/fixture-session")[0], 200)
        self.assertEqual(self.request("GET", "/sessions/fixture-session")[1]["attempts"], session["attempts"])
        self.assertEqual(self.request("POST", "/sessions", session)[0], 200)
        self.assertEqual(self.request("POST", "/sessions", {**session, "startedAt": "2026-09-05T00:00:00Z"})[0], 409)
        self.assertEqual(self.request("GET", "/sessions/missing")[0], 404)
        self.assertEqual(self.row_count(), 1)

    def test_api_rejects_bad_root(self):
        for value in ([], "session", 42, True):
            with self.subTest(value=value):
                self.assertEqual(self.request("POST", "/sessions", value)[0], 422)
        self.assertEqual(self.row_count(), 0)


if __name__ == "__main__":
    unittest.main()
