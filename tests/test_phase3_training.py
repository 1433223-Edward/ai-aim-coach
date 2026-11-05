"""All model replies are explicit Fake Client responses; every database is temporary."""
import json
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent import experiment_planner as runtime
from analysis import training_tools as tools
from backend import session_store as store, training_plans as plans
from backend.scenario_store import save
from test_phase2_scoring import evidence
from test_experiment_consistency import response, tool_call


class FakeTrainingClient:
    is_fake = True

    def __init__(self, behavior="normal"):
        self.chat = type("Chat", (), {"completions": self})()
        self.behavior, self.closed, self.calls = behavior, False, 0

    def create(self, **kwargs):
        self.calls += 1
        if self.behavior == "timeout":
            raise TimeoutError("test-only timeout")
        if self.behavior == "loop":
            return response(calls=[tool_call("get_training_history")])
        if self.behavior == "unknown":
            return response(calls=[tool_call("arbitrary_python", {"function": "os.system"})])
        if self.behavior == "recover" and self.calls == 1:
            return response(calls=[tool_call("analyze_performance_trend", {"session_ids":["missing"]})])
        results = [m for m in kwargs["messages"] if m["role"] == "tool" and "error" not in json.loads(m["content"])]
        if len(results) < 3:
            name = ["get_training_history", "analyze_performance_trend", "recommend_training_plan"][len(results)]
            return response(calls=[tool_call(name)])
        proposal = json.loads(results[-1]["content"])
        if self.behavior == "forged":
            proposal["scenario_id"] = "unregistered_game"
        return response(json.dumps({"plan": proposal, "explanation": "FAKE CLIENT / SYNTHETIC TEST DATA: deterministic proposal; no causal claim."}))

    def close(self):
        self.closed = True


class TrainingTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(store, "DB_PATH", Path(directory)/"phase3.db"))
        store.init_database()

    def request(self, **kwargs):
        return plans.TrainingRequest(goal="提高稳定性", trainer_width=800, trainer_height=600, **kwargs)

    def proposal(self, behavior="normal", **kwargs):
        client = FakeTrainingClient(behavior)
        with patch.object(runtime, "get_model_client", return_value=client):
            plan = plans.create_plan(self.request(**kwargs))
        self.assertTrue(client.closed)
        self.assertEqual(plan["agent"]["usage"]["client_kind"], "fake_client")
        return plan

    def seed(self, scenario="flick_target", sessions=3, misses=True, **config):
        state = store.get_or_create_experiment(800, 600, {"sessions": sessions, "attempts": 5},
            {"scenario_id": scenario, "target_count": 5, "duration_seconds": 10, **config})
        for index in range(sessions):
            self.save_next(state["experiment_id"], misses, index)
        return state["experiment_id"]

    def save_next(self, experiment_id, misses=False, index=0):
        state = store.get_experiment(experiment_id)
        run = evidence(state["protocol"]["scenario"], experiment_id, state["completed_count"]+1)
        run["settingsSnapshot"] = state["protocol"]["settings"]
        run["sensitivityRepeat"] = state["next_condition"]["repeat"]
        run["startedAt"] = f"2026-11-{index+1:02d}T00:00:00Z"
        run["endedAt"] = f"2026-11-{index+1:02d}T00:03:00Z"
        if misses and run["scenarioSnapshot"]["category"] == "static":
            for attempt in run["attempts"]:
                attempt["clicks"].insert(0, {"time": attempt["target"]["appearTime"]+100, "x": 0, "y": 0, "hit": False})
        save(run)
        return run

    def test_three_tools_and_fixed_snapshot(self):
        self.seed()
        context = tools.TrainingContext("提高准确率", tools.TrainingBounds(), [800,600])
        history = context.execute("get_training_history", {"limit": 2, "date_from":"2026-11-02", "scenario_id":"flick_target"})
        self.assertEqual(history["returned"], 2)
        self.assertTrue(all(r["score"] == 77.5 for r in history["sessions"]))
        trend = context.execute("analyze_performance_trend", {})
        self.assertEqual(len(trend["groups"]), 1)
        self.assertTrue(trend["groups"][0]["sufficient"])
        self.assertEqual(trend["groups"][0]["weak_component"], "accuracy")
        candidate = context.execute("recommend_training_plan", {})
        self.assertEqual(candidate["target"]["metric"], "accuracy")
        self.seed(seed=9)
        self.assertEqual(len(context.records()), 3)
        self.assertEqual(len(tools.load_history()[0]), 6)

    def test_no_history_first_plan_and_explicit_acceptance(self):
        plan = self.proposal()
        self.assertEqual(plan["evidence_session_ids"], [])
        self.assertFalse(plan["rationale"]["sufficient"])
        self.assertIsNone(store.get_experiment())
        reply = plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800, trainer_height=600))
        experiment = reply["experiment"]
        self.assertEqual(experiment["protocol"]["training_plan_id"], plan["plan_id"])
        self.assertEqual(experiment["completed_count"], 0)
        retry = plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800, trainer_height=600))
        self.assertEqual(experiment["experiment_id"], retry["experiment"]["experiment_id"])

    def test_condition_isolation_and_bounds(self):
        self.seed(difficulty="easy")
        self.seed(difficulty="normal")
        self.seed(seed=2)
        self.seed(scenario="horizontal_tracking")
        rows, _ = tools.load_history()
        self.assertEqual(len(tools.analyze_performance_trend(rows)["groups"]), 4)
        bounds = tools.TrainingBounds(time_budget_seconds=10, max_sessions=1, max_attempts=5,
                                     max_duration_seconds=10, max_difficulty="easy")
        candidate = tools.recommend_training_plan(rows, "跟随能力", bounds, [800,600])
        self.assertEqual(candidate["sessions"], 1)
        self.assertEqual(candidate["task_parameters"]["difficulty"], "easy")
        self.assertLessEqual(candidate["task_parameters"]["duration_seconds"]*candidate["sessions"], 10)
        # No easy tracking history: this creates a baseline rather than attaching unlike evidence.
        self.assertEqual(candidate["evidence_session_ids"], [])

    def test_invalid_arguments_missing_ids_and_unknown_scenario(self):
        context = tools.TrainingContext("test", tools.TrainingBounds(), [800,600])
        for name, args in [("get_training_history", {"limit":51}), ("get_training_history", {"date_from":"2026-12-10", "date_to":"2026-01-01"}),
                           ("get_training_history", {"scenario_id":"eval"}), ("get_training_history", {"limit":True}),
                           ("recommend_training_plan", {"scenario_id":"eval"}), ("get_training_history", {"extra":1})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                context.execute(name, args)
        with self.assertRaises(ValueError):
            tools.analyze_performance_trend([], ["missing-session"])
        with self.assertRaises(ValueError):
            tools.TrainingBounds(max_attempts=101)

    def test_explicit_goals_select_only_existing_registry_tasks(self):
        for goal, expected in [("四目标切换","four_target_switching"),("精准微调","six_target_precision"),
                               ("水平跟随","horizontal_tracking"),("垂直跟随","vertical_tracking"),
                               ("flick定位","flick_target"),("动态反应跟随","reactive_tracking")]:
            with self.subTest(goal=goal):
                plan = tools.recommend_training_plan([],goal,tools.TrainingBounds(),[800,600])
                self.assertEqual(plan["scenario_id"],expected)
                self.assertEqual(plan["evidence_session_ids"],[])

    def test_full_plan_execution_and_effect_evaluation(self):
        self.seed()
        plan = self.proposal()
        before = deepcopy(plan["baseline_records"])
        accepted = plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800, trainer_height=600,
            preferences={"crosshair_color":"#ff0000", "sessions":1, "attempts":99}))
        exp = accepted["experiment"]
        self.assertEqual(exp["protocol"]["settings"]["crosshair_color"], "#ff0000")
        self.assertEqual(len(exp["schedule"]), plan["sessions"])
        self.assertEqual(exp["protocol"]["target_count"], 5)
        initial = plans.evaluate_plan(plan["plan_id"])
        self.assertFalse(initial["sufficient"])
        self.assertIsNone(initial["target_reached"])
        for index in range(plan["sessions"]):
            self.save_next(exp["experiment_id"], index=index+5)
        outcome = plans.evaluate_plan(plan["plan_id"])
        self.assertEqual(outcome["completion_rate"], 1)
        self.assertTrue(outcome["sufficient"])
        self.assertEqual(outcome["metric_delta"], 22.5)
        self.assertTrue(outcome["target_reached"])
        self.assertIn("no causal", outcome["limitation"])
        self.assertEqual(plans.get_plan(plan["plan_id"])["baseline_records"], before)
        self.assertEqual(len(outcome["comparison_session_ids"]), plan["sessions"])
        store.init_database()
        self.assertEqual(plans.get_plan(plan["plan_id"])["evaluation"], outcome)

    def test_refusal_active_experiment_and_atomic_rollback(self):
        plan = self.proposal()
        store.get_or_create_experiment(800,600)
        with self.assertRaises(store.ExperimentConflict):
            plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800, trainer_height=600))
        self.assertEqual(plans.get_plan(plan["plan_id"])["plan_status"], "proposed")
        plans.reject_plan(plan["plan_id"])
        with self.assertRaises(store.ExperimentConflict):
            plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800, trainer_height=600))

    def test_model_failures_cannot_create_plans_and_close_client(self):
        for behavior, code in [("unknown","tool_error"), ("forged","insufficient_evidence"),
                               ("timeout","provider_timeout"), ("loop","round_budget_exceeded")]:
            client = FakeTrainingClient(behavior)
            with self.subTest(behavior=behavior), patch.object(runtime,"get_model_client",return_value=client):
                with self.assertRaises(runtime.PlannerFailure) as failure:
                    plans.create_plan(self.request())
                self.assertEqual(failure.exception.detail["code"], code)
                self.assertLessEqual(client.calls, 6)
                self.assertTrue(client.closed)
        with closing(store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM training_plans").fetchone()[0], 0)

    def test_single_tool_failure_recovers_and_missing_model_is_explicit(self):
        plan = self.proposal("recover")
        self.assertEqual(len([entry for entry in plan["agent"]["trace"] if not entry["success"]]),1)
        self.assertEqual(plan["agent"]["usage"]["model_calls"],5)
        with patch.object(runtime,"get_model_client",side_effect=runtime.ModelUnavailable("test-only missing key")):
            with self.assertRaises(runtime.PlannerFailure) as error:
                plans.create_plan(self.request())
        self.assertEqual(error.exception.detail["code"],"model_unavailable")
        self.assertEqual(error.exception.http_status,503)

    def test_corrupt_and_incomplete_records_are_not_trend_evidence(self):
        exp = self.seed()
        state = store.get_or_create_experiment(800,600,{"sessions":1,"attempts":5},
            {"scenario_id":"flick_target","target_count":5,"duration_seconds":10})
        partial = evidence(state["protocol"]["scenario"], state["experiment_id"])
        partial.update(status="incomplete", attempts=partial["attempts"][:4], elapsed_ms=1600, endTime=1600,
                       settingsSnapshot=state["protocol"]["settings"], sensitivityRepeat=state["next_condition"]["repeat"])
        save(partial)
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE scenario_sessions SET result_json='{}' WHERE session_id=(SELECT session_id FROM scenario_sessions WHERE experiment_id=? LIMIT 1)", (exp,))
        rows, _ = tools.load_history()
        self.assertEqual(sum(r["status"] == "abnormal" for r in rows), 1)
        groups = tools.analyze_performance_trend(rows)["groups"]
        self.assertEqual(groups[0]["sample_count"], 2)
        self.assertFalse(groups[0]["sufficient"])
        self.assertEqual(sum(r["status"] == "incomplete" for r in rows), 1)
        self.assertEqual(len(tools.get_training_history(rows)["sessions"]), 4)

    def test_api_rejects_bad_bounds_and_preserves_old_endpoint(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        with TestClient(app) as client:
            bad = self.request().model_dump(); bad["bounds"]["max_sessions"] = 99
            self.assertEqual(client.post("/agent/training-plans", json=bad).status_code, 422)
            self.assertEqual(client.post("/agent/training-plans", content='{"goal":"test","trainer_width":NaN,"trainer_height":600}',
                                        headers={"Content-Type":"application/json"}).status_code,422)
            self.assertEqual(client.get("/training-plans/not-found").status_code, 404)
            with patch.object(runtime,"get_model_client",return_value=FakeTrainingClient()):
                reply = client.post("/agent/training-plans", json=self.request().model_dump())
            self.assertEqual(reply.status_code, 200, reply.text)
            plan_id = reply.json()["plan_id"]
            accepted = client.post(f"/training-plans/{plan_id}/accept",json={"trainer_width":800,"trainer_height":600})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(client.post(f"/training-plans/{plan_id}/evaluate").status_code, 200)
            self.assertEqual(client.post("/agent/plan", json={"goal":"test","experiment_id":"missing"}).status_code,404)

    def test_bounded_history_snapshot_and_plan_version_rejection(self):
        self.seed(sessions=30)
        self.seed(sessions=30, seed=2)
        self.seed(sessions=30, seed=3)
        self.seed(sessions=15, seed=4)
        rows, truncated = tools.load_history()
        self.assertEqual(len(rows), 100)
        self.assertTrue(truncated)
        self.assertEqual(len(tools.get_training_history(rows,limit=50)["sessions"]),50)
        scoped, clipped = tools.load_history(history_filter=tools.HistoryFilter(date_from="2026-11-30", date_to="2026-11-30"))
        self.assertEqual(len(scoped),3)
        self.assertFalse(clipped)
        plan = self.proposal()
        with closing(store._connect()) as connection, connection:
            payload = deepcopy(plan)
            payload["frozen_scenario"]["scoring_version"] = "obsolete"
            connection.execute("UPDATE training_plans SET payload=? WHERE plan_id=?", (tools.canonical(payload),plan["plan_id"]))
        with self.assertRaises(store.ExperimentConflict):
            plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800,trainer_height=600))
        self.assertEqual(store.get_experiment()["status"], "complete")

    def test_acceptance_rollback_and_evidence_insufficient_after_one_session(self):
        self.seed()
        plan = self.proposal(bounds=tools.TrainingBounds(max_sessions=1))
        original = store.get_or_create_experiment
        def fail_after_create(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic failure before plan association")
        with patch.object(store,"get_or_create_experiment",side_effect=fail_after_create), self.assertRaises(RuntimeError):
            plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800,trainer_height=600))
        with closing(store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM experiments").fetchone()[0],1)
            self.assertEqual(connection.execute("SELECT count(*) FROM experiments WHERE active_slot=1").fetchone()[0],0)
        self.assertEqual(plans.get_plan(plan["plan_id"])["plan_status"],"proposed")
        accepted = plans.accept_plan(plan["plan_id"], plans.AcceptRequest(trainer_width=800,trainer_height=600))
        self.save_next(accepted["experiment"]["experiment_id"])
        outcome = plans.evaluate_plan(plan["plan_id"])
        self.assertEqual(outcome["completion_rate"],1)
        self.assertFalse(outcome["sufficient"])
        self.assertIsNone(outcome["metric_delta"])
