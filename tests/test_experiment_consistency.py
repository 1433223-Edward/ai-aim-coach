"""P0-2 regression tests use temporary SQLite and a fake model, never real data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import test_session_contract as contract
from backend import session_store as store
from analysis import analysis_runtime as runtime
from analysis import tools


def make_session(state):
    """Build collector-shaped data for the next persisted condition."""
    session = contract.valid_session()
    protocol = state["protocol"]
    condition = state["next_condition"]
    session.update(
        sessionId=str(uuid4()), experimentId=state["experiment_id"],
        protocolVersion=protocol["version"], experimentOrder=state["completed_count"] + 1,
        sensitivityRepeat=condition["repeat"], trainerSensitivity=condition["sensitivity"],
        trainerWidth=protocol["trainer_width"], trainerHeight=protocol["trainer_height"],
        targetCount=protocol["target_count"],
    )
    attempt = session["attempts"][0]
    attempt["target"]["radius"] = protocol["target_radius"]
    attempt["clicks"][0]["x"] = 0  # Keep the MISS outside the larger production target.
    attempt["movements"][0].update(x=0, rawDX=0)
    session["attempts"] = []
    for index in range(protocol["target_count"]):
        item = deepcopy(attempt)
        for key in ("appearTime", "disappearTime"):
            item["target"][key] += index * 50
        for event in item["clicks"] + item["movements"]:
            event["time"] += index * 50
        session["attempts"].append(item)
    session["endTime"] = 40 + (protocol["target_count"] - 1) * 50
    return session


def response(content=None, calls=()):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason="tool_calls" if calls else "stop", message=types.SimpleNamespace(
        content=content, tool_calls=list(calls) or None,
    ))])


def tool_call(name, arguments=None):
    return types.SimpleNamespace(id=str(uuid4()), function=types.SimpleNamespace(
        name=name, arguments=json.dumps(arguments or {}),
    ))


class FakeClient:
    def __init__(self, handler=None):
        self.chat = types.SimpleNamespace(completions=self)
        self.handler = handler or self.with_evidence

    def with_evidence(self, **kwargs):
        results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
        if not results:
            return response(calls=[tool_call("get_analysis_summary")])
        if not any("metrics" in r for r in results):
            return response(calls=[tool_call("get_sensitivity_profile", {"sensitivity": s})
                                   for s in results[0]["experiment"]["sensitivities"]])
        return response("Test recommendation")

    def create(self, **kwargs):
        return self.handler(**kwargs)


def load_planner(client):
    # Execute the real runtime with an isolated fake SDK, without importing a real provider.
    spec = importlib.util.spec_from_file_location("p02_planner", contract.ROOT / "agent/experiment_planner.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=lambda **kwargs: client)}), \
            patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-only-unused"}):
        spec.loader.exec_module(module)
    module.get_model_client = lambda: client
    return module


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db_path = Path(directory) / "experiments.db"
        self.enterContext(patch.object(store, "DB_PATH", self.db_path))
        store.init_database()

    def new_experiment(self):
        return store.get_or_create_experiment(800, 600)

    def save_next(self, state):
        store.save_session(make_session(state))
        return store.get_experiment(state["experiment_id"])

    def finish(self, state):
        while state["status"] != "complete":
            state = self.save_next(state)
        return state

    def test_identity_schedule_and_confirmed_progress_survive_reload(self):
        state = self.new_experiment()
        UUID(state["experiment_id"])
        expected_conditions = {(s, r) for s in (0.8, 1.0, 1.2) for r in (1, 2, 3)}
        self.assertEqual({(c["sensitivity"], c["repeat"]) for c in state["schedule"]}, expected_conditions)
        saved = self.save_next(state)
        resumed = store.get_or_create_experiment(900, 700)
        self.assertEqual(resumed, saved)
        self.assertEqual(resumed["schedule"], state["schedule"])
        self.assertEqual(resumed["completed_count"], 1)
        self.assertEqual(resumed["protocol"]["trainer_width"], 800)
        # Idempotent initialization does not reset or replace persisted state.
        store.init_database()
        self.assertEqual(store.get_experiment(), resumed)

    def test_concurrent_tabs_create_only_one_active_experiment(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(lambda _: self.new_experiment(), range(2)))
        self.assertEqual(states[0], states[1])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM experiments").fetchone()[0], 1)

    def test_concurrent_tabs_cannot_commit_same_condition_twice(self):
        state = self.new_experiment()
        barrier = threading.Barrier(2)

        def upload(_):
            session = make_session(state)
            barrier.wait(timeout=5)
            try:
                store.save_session(session)
                return "saved"
            except store.ExperimentConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(upload, range(2))), ["saved", "conflict"])
        self.assertEqual(store.get_experiment()["completed_count"], 1)

    def test_protocol_mismatch_and_unregistered_identity_are_rejected(self):
        state = self.new_experiment()
        changes = {"trainerWidth": 801, "trainerHeight": 601, "protocolVersion": "other",
                   "trainerSensitivity": 2.0, "sensitivityRepeat": 10, "experimentOrder": 2,
                   "schemaVersion": "other", "inputMode": "unknown", "experimentId": "exp_001"}
        for key, value in changes.items():
            with self.subTest(key=key):
                session = make_session(state)
                session[key] = value
                with self.assertRaises(store.ExperimentConflict):
                    store.save_session(session)
        session = make_session(state)
        session["attempts"][0]["target"]["radius"] = 24
        with self.assertRaises(store.ExperimentConflict):
            store.save_session(session)
        session = make_session(state)
        session["attempts"].pop()
        session["targetCount"] -= 1
        with self.assertRaises(store.ExperimentConflict):
            store.save_session(session)
        self.assertEqual(store.get_experiment()["completed_count"], 0)

    def test_completion_is_restored_until_explicit_new_experiment(self):
        old = self.finish(self.new_experiment())
        self.assertEqual(store.get_experiment(), old)
        new = self.new_experiment()
        self.assertNotEqual(new["experiment_id"], old["experiment_id"])
        self.assertEqual(new["completed_count"], 0)
        self.assertEqual(store.get_experiment(old["experiment_id"]), old)
        with self.assertRaises(store.ExperimentConflict):
            store.save_session(make_session({**old, "next_condition": old["schedule"][0]}))

    def test_legacy_rows_are_not_migrated_or_used_as_verified_evidence(self):
        raw = json.dumps(contract.valid_session())
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("""INSERT INTO sessions
                (session_id, experiment_id, schema_version, started_at, trainer_sensitivity, target_count, raw_data)
                VALUES ('legacy', 'exp_001', '0.1', 'old', 1, 1, ?)""", (raw,))
        store.init_database()
        self.assertIsNone(store.get_experiment())
        with self.assertRaisesRegex(store.ExperimentConflict, "Legacy"):
            runtime.create_analysis_snapshot("exp_001")
        self.new_experiment()
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT raw_data FROM sessions WHERE session_id='legacy'").fetchone()[0], raw)

    def test_snapshot_is_immutable_and_new_requests_see_new_data(self):
        state = self.save_next(self.new_experiment())
        old = runtime.create_analysis_snapshot(state["experiment_id"])
        same = runtime.create_analysis_snapshot(state["experiment_id"])
        self.assertEqual(old.metadata(), same.metadata())
        old.result()["experiment"]["session_count"] = 999
        old.metadata()["session_ids"].clear()
        old_meta = old.metadata()
        state = self.save_next(state)
        with patch.object(runtime, "analyze_latest_experiment", side_effect=AssertionError("latest is forbidden")):
            self.assertEqual(tools.get_analysis_summary(snapshot=old)["evidence"], old_meta)
            sensitivity = old.result()["sensitivity_profiles"][0]["sensitivity"]
            self.assertEqual(tools.get_sensitivity_profile(sensitivity, snapshot=old)["evidence"], old_meta)
        new = runtime.create_analysis_snapshot(state["experiment_id"])
        self.assertNotEqual(new.metadata()["snapshot_id"], old_meta["snapshot_id"])
        self.assertEqual(new.metadata()["session_count"], old_meta["session_count"] + 1)
        self.assertEqual(old.metadata(), old_meta)

    def test_read_transaction_keeps_progress_and_session_rows_together(self):
        state = self.save_next(self.new_experiment())
        reader_ready = threading.Event()
        writer_started = threading.Event()
        original_state = store._experiment_state

        def paused_state(connection, row):
            result = original_state(connection, row)
            # Pause only the reader, after its first read has fixed the SQLite snapshot.
            if threading.current_thread().name.startswith("snapshot-reader"):
                reader_ready.set()
                self.assertTrue(writer_started.wait(timeout=5))
            return result

        def writer():
            self.assertTrue(reader_ready.wait(timeout=5))
            writer_started.set()
            self.save_next(state)

        with patch.object(store, "_experiment_state", side_effect=paused_state), \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-reader") as readers, \
                ThreadPoolExecutor(max_workers=1) as writers:
            read = readers.submit(store.load_experiment_evidence, state["experiment_id"])
            write = writers.submit(writer)
            frozen_state, frozen_sessions = read.result(timeout=10)
            write.result(timeout=10)
        self.assertEqual(frozen_state["completed_count"], 1)
        self.assertEqual(len(frozen_sessions), 1)
        self.assertEqual(store.get_experiment()["completed_count"], 2)

    def test_corrupt_identity_or_protocol_is_not_relabelled_as_valid_evidence(self):
        state = self.save_next(self.new_experiment())
        session = store.get_sessions_by_experiment(state["experiment_id"])[0]
        for key, value in (("experimentId", "different"), ("protocolVersion", "different")):
            with self.subTest(key=key):
                corrupted = {**session, key: value}
                with closing(sqlite3.connect(self.db_path)) as connection, connection:
                    connection.execute("UPDATE sessions SET raw_data=?", (json.dumps(corrupted),))
                with self.assertRaises(store.ExperimentConflict):
                    runtime.create_analysis_snapshot(state["experiment_id"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE sessions SET raw_data=?", (json.dumps(session),))

    def test_single_recommendation_keeps_snapshot_during_upload(self):
        state = self.save_next(self.new_experiment())
        # Collect two tested conditions so comparison is valid regardless of shuffle order.
        while len({s["trainerSensitivity"] for s in store.get_sessions_by_experiment(state["experiment_id"])}) < 2:
            state = self.save_next(state)
        sensitivities = sorted({s["trainerSensitivity"] for s in store.get_sessions_by_experiment(state["experiment_id"])})
        tool_results = []
        model_round = 0

        def model(**kwargs):
            nonlocal model_round
            model_round += 1
            if model_round == 1:
                return response(calls=[tool_call("get_analysis_summary")])
            if model_round == 2:
                # This commit happens after the summary but before subsequent tool calls.
                self.save_next(state)
                return response(calls=[tool_call("compare_sensitivities", {"sensitivities": sensitivities}),
                                       tool_call("get_sensitivity_profile", {"sensitivity": sensitivities[0]})])
            tool_results.extend(json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool")
            return response("Evidence-bound recommendation")

        planner = load_planner(FakeClient(model))
        with patch.object(planner, "create_analysis_snapshot", wraps=runtime.create_analysis_snapshot) as create:
            result = planner.run_experiment_planner("test", state["experiment_id"])
            create.assert_called_once_with(state["experiment_id"])
        self.assertEqual(len(tool_results), 3)
        for item in tool_results:
            self.assertEqual(item["evidence"], result["evidence"])
        self.assertEqual(result["evidence"]["session_count"], state["completed_count"])
        planner.get_model_client = lambda: FakeClient()
        fresh = planner.run_experiment_planner("again", state["experiment_id"])
        self.assertEqual(fresh["evidence"]["session_count"], state["completed_count"] + 1)
        self.assertNotEqual(fresh["evidence"]["snapshot_id"], result["evidence"]["snapshot_id"])

    def test_parallel_recommendations_do_not_share_evidence(self):
        old = self.finish(self.new_experiment())
        new = self.save_next(self.new_experiment())
        barrier = threading.Barrier(2)

        def model(**kwargs):
            if not any(m["role"] == "tool" for m in kwargs["messages"]):
                barrier.wait(timeout=5)
                return response(calls=[tool_call("get_analysis_summary")])
            results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
            if not any("metrics" in r for r in results):
                return response(calls=[tool_call("get_sensitivity_profile", {"sensitivity": s})
                                       for s in results[0]["experiment"]["sensitivities"]])
            return response("Scoped answer")

        planner = load_planner(FakeClient(model))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda state: planner.run_experiment_planner("test", state["experiment_id"]), [old, new]))
        self.assertEqual([r["evidence"]["experiment_id"] for r in results], [old["experiment_id"], new["experiment_id"]])
        self.assertEqual([r["evidence"]["session_count"] for r in results], [9, 1])
        self.assertFalse(results[0]["evidence"]["incomplete"])
        self.assertTrue(results[1]["evidence"]["incomplete"])

    def test_api_explicit_scope_status_and_resume(self):
        planner = load_planner(FakeClient())
        spec = importlib.util.spec_from_file_location("p02_main", contract.ROOT / "backend/main.py")
        main = importlib.util.module_from_spec(spec)
        stub = types.SimpleNamespace(run_experiment_planner=planner.run_experiment_planner,
                                     ModelUnavailable=planner.ModelUnavailable, PlannerFailure=planner.PlannerFailure,
                                     model_settings=planner.model_settings)
        with patch.dict(sys.modules, {"agent.experiment_planner": stub}):
            spec.loader.exec_module(main)

        def request(method, path, payload=None):
            import asyncio
            return asyncio.run(contract.asgi_request(main.app, method, path, payload))

        self.assertEqual(request("GET", "/experiments/current")[0], 404)
        status, state = request("POST", "/experiments", {"trainer_width": 800, "trainer_height": 600})
        self.assertEqual(status, 200)
        self.assertEqual(request("GET", "/experiments/current")[1], state)
        self.assertEqual(request("POST", "/agent/plan", {"goal": "test"})[0], 422)
        self.assertEqual(request("POST", "/agent/plan", {"goal": "test", "experiment_id": "missing"})[0], 404)
        payload = {"goal": "test", "experiment_id": state["experiment_id"]}
        self.assertEqual(request("POST", "/agent/plan", payload)[0], 409)
        self.assertEqual(request("POST", "/sessions", make_session(state))[0], 200)
        restored = request("GET", "/experiments/" + state["experiment_id"])[1]
        self.assertEqual(restored["completed_count"], 1)
        self.assertEqual(restored["schedule"], state["schedule"])
        status, result = request("POST", "/agent/plan", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["evidence"]["experiment_id"], state["experiment_id"])
        self.assertEqual(result["evidence"]["session_count"], 1)
        self.assertTrue(result["evidence"]["incomplete"])
        self.assertEqual(request("POST", "/sessions", make_session(state))[0], 409)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("""INSERT INTO sessions
                (session_id, experiment_id, schema_version, started_at, trainer_sensitivity, target_count, raw_data)
                VALUES ('legacy', 'exp_001', '0.1', 'old', 1, 1, ?)""", (json.dumps(contract.valid_session()),))
        self.assertEqual(request("GET", "/experiments/exp_001")[0], 409)
        self.assertEqual(request("POST", "/agent/plan", {"goal": "test", "experiment_id": "exp_001"})[0], 409)


if __name__ == "__main__":
    unittest.main()
