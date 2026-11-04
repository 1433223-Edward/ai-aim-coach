"""Exercise the real tool loop with isolated data and deterministic model responses."""
from contextlib import closing
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

import test_experiment_consistency as fixtures
import test_session_contract as contract
from backend import session_store as store


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db_path = Path(self.directory) / "agent.db"
        self.enterContext(patch.object(store, "DB_PATH", self.db_path))
        store.init_database()
        self.state = store.get_or_create_experiment(800, 600)
        # At least two actual tested profiles are needed for comparison tests, not a new business threshold.
        while len({s["trainerSensitivity"] for s in store.get_sessions_by_experiment(self.state["experiment_id"])}) < 2:
            store.save_session(fixtures.make_session(self.state))
            self.state = store.get_experiment(self.state["experiment_id"])

    def planner(self, handler=None):
        return fixtures.load_planner(fixtures.FakeClient(handler))

    def run_plan(self, planner, **kwargs):
        return planner.run_experiment_planner("test goal", self.state["experiment_id"], **kwargs)

    def fails(self, planner, code, **kwargs):
        with self.assertRaises(planner.PlannerFailure) as failure:
            self.run_plan(planner, **kwargs)
        self.assertEqual(failure.exception.detail["code"], code)
        self.assertTrue(failure.exception.detail["request_id"])
        self.assertNotIn("answer", failure.exception.detail)
        return failure.exception.detail

    def test_success_requires_summary_and_all_tested_metric_profiles(self):
        result = self.run_plan(self.planner())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["evidence"]["incomplete"])
        self.assertIn("get_analysis_summary", result["tools_used"])
        self.assertTrue(all(t["success"] for t in result["trace"]))
        self.assertEqual({t["snapshot_id"] for t in result["trace"]}, {result["evidence"]["snapshot_id"]})

    def test_zero_tools_is_never_accepted_as_a_recommendation(self):
        calls = []
        planner = self.planner(lambda **kw: calls.append(kw) or fixtures.response("Unverified plan"))
        self.fails(planner, "insufficient_evidence")
        self.assertEqual(len(calls), 2)

    def test_summary_alone_cannot_substitute_for_metrics(self):
        def model(**kw):
            if not any(m["role"] == "tool" for m in kw["messages"]):
                return fixtures.response(calls=[fixtures.tool_call("get_analysis_summary")])
            return fixtures.response("Only summary")
        self.fails(self.planner(model), "insufficient_evidence")

    def test_invalid_arguments_are_errors_not_evidence(self):
        cases = [("unknown", {}), ("get_sensitivity_profile", {}),
                 ("get_analysis_summary", {"extra": 1}),
                 ("get_sensitivity_profile", {"sensitivity": True}),
                 ("get_sensitivity_profile", {"sensitivity": "1"}),
                 ("compare_sensitivities", {"sensitivities": [1, 1]}),
                 ("compare_sensitivities", {"sensitivities": [1, float("inf")]}),
                 ("get_analysis_summary", []), ("get_sensitivity_profile", {"sensitivity": 999})]
        for name, args in cases:
            with self.subTest(name=name, arguments_type=type(args).__name__):
                planner = self.planner(lambda **kw: fixtures.response(calls=[fixtures.tool_call(name, args)]))
                # tool_call's default is {}, so explicitly preserve the [] negative case.
                if args == []:
                    def bad_array(**kw):
                        call = fixtures.tool_call(name)
                        call.function.arguments = "[]"
                        return fixtures.response(calls=[call])
                    planner = self.planner(bad_array)
                detail = self.fails(planner, "tool_error")
                self.assertTrue(all(not t["success"] for t in detail["trace"]))

    def test_bad_json_has_one_bounded_recovery(self):
        def bad_json(**kw):
            call = fixtures.tool_call("get_analysis_summary")
            call.function.arguments = "{invalid"
            return fixtures.response(calls=[call])
        detail = self.fails(self.planner(bad_json), "tool_error")
        self.assertEqual(len(detail["trace"]), 2)

    def test_single_tool_error_can_recover_using_valid_evidence(self):
        valid = fixtures.FakeClient()
        seen = 0
        def model(**kw):
            nonlocal seen
            seen += 1
            if seen == 1:
                return fixtures.response(calls=[fixtures.tool_call("get_sensitivity_profile", {"sensitivity": 999})])
            filtered = {**kw, "messages": [m for m in kw["messages"]
                        if not (m["role"] == "tool" and "error" in json.loads(m["content"]))]}
            return valid.with_evidence(**filtered)
        result = self.run_plan(self.planner(model))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["trace"][0]["success"])
        self.assertEqual(result["trace"][0]["arguments"], {"sensitivity": 999})

    def test_corrupt_tool_payload_or_scope_is_rejected(self):
        for scope_error in (False, True):
            with self.subTest(scope_error=scope_error):
                planner = self.planner(lambda **kw: fixtures.response(calls=[fixtures.tool_call("get_analysis_summary")]))
                original = planner.execute_tool
                def corrupt(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if scope_error:
                        result["evidence"]["experiment_id"] = "different"
                    else:
                        result["findings"] = [{"fabricated": True}]
                    return result
                with patch.object(planner, "execute_tool", side_effect=corrupt):
                    self.fails(planner, "tool_error")

    def test_empty_truncated_refused_and_malformed_messages_fail(self):
        for kind, code in (("empty", "empty_answer"), ("length", "invalid_model_response"),
                           ("refusal", "invalid_model_response"), ("malformed", "invalid_model_response")):
            with self.subTest(kind=kind):
                value = fixtures.response("text" if kind != "empty" else " ")
                if kind == "length": value.choices[0].finish_reason = "length"
                if kind == "refusal": value.choices[0].message.refusal = "refused"
                if kind == "malformed": value.choices = []
                self.fails(self.planner(lambda **kw: value), code)

    def test_malformed_tool_call_and_duplicate_ids_fail(self):
        for calls in ([types.SimpleNamespace(id="missing-function")],
                      [fixtures.tool_call("get_analysis_summary")] * 2):
            self.fails(self.planner(lambda **kw: fixtures.response(calls=calls)), "invalid_tool_call")

    def test_last_model_round_does_not_execute_more_tools(self):
        requests = []
        def model(**kw):
            requests.append(kw["tool_choice"])
            return fixtures.response(calls=[fixtures.tool_call("get_analysis_summary")])
        planner = self.planner(model)
        detail = self.fails(planner, "round_budget_exceeded")
        self.assertEqual(len(requests), 6)
        self.assertEqual(requests[-1], "none")
        self.assertEqual(len(detail["trace"]), 5)

    def test_tool_token_and_deadline_budgets(self):
        planner = self.planner(lambda **kw: fixtures.response(calls=[fixtures.tool_call("get_analysis_summary")] * 1 +
                                              [fixtures.tool_call("get_analysis_summary")]))
        self.fails(planner, "tool_budget_exceeded", limits=replace(planner.RuntimeLimits(), tool_calls=1))
        self.fails(planner, "token_budget_exceeded", limits=replace(planner.RuntimeLimits(), total_tokens=1))
        times = iter([0, 61])
        self.fails(planner, "deadline_exceeded", clock=lambda: next(times))

    def test_provider_retry_is_bounded_and_errors_are_redacted(self):
        valid = fixtures.FakeClient()
        attempts = 0
        def transient(**kw):
            nonlocal attempts
            attempts += 1
            if attempts == 1: raise TimeoutError("must-not-be-exposed")
            return valid.with_evidence(**kw)
        self.assertEqual(self.run_plan(self.planner(transient))["status"], "ok")
        def permanent(**kw): raise ValueError("must-not-be-exposed")
        detail = self.fails(self.planner(permanent), "provider_error")
        self.assertNotIn("must-not-be-exposed", json.dumps(detail))
        def timeout(**kw): raise TimeoutError("private")
        detail = self.fails(self.planner(timeout), "provider_timeout")
        self.assertEqual(len(detail["trace"]), 2)

    def test_missing_and_invalid_stored_evidence_has_request_identity(self):
        planner = self.planner()
        with self.assertRaises(planner.PlannerFailure) as failure:
            planner.run_experiment_planner("test", "unknown")
        self.assertEqual(failure.exception.detail["code"], "experiment_not_found")
        self.assertTrue(failure.exception.detail["request_id"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE sessions SET raw_data='{invalid'")
        self.fails(planner, "invalid_evidence")

    def test_missing_finish_state_and_empty_object_tool_calls_are_invalid(self):
        for malformed in ("missing_finish", "object_calls"):
            with self.subTest(malformed=malformed):
                value = fixtures.response("not evidence")
                if malformed == "missing_finish": del value.choices[0].finish_reason
                else: value.choices[0].message.tool_calls = {}
                self.fails(self.planner(lambda **kw: value), "invalid_model_response")

    def test_reused_tool_id_in_later_round_is_rejected(self):
        call = fixtures.tool_call("get_analysis_summary")
        self.fails(self.planner(lambda **kw: fixtures.response(calls=[call])), "invalid_tool_call")


class StartupTests(unittest.TestCase):
    def test_missing_optional_sdk_fails_only_when_model_is_requested(self):
        import sys
        from agent.experiment_planner import get_model_client, ModelUnavailable
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-only-not-a-real-key"}), patch.dict(sys.modules, {"openai": None}):
            with self.assertRaises(ModelUnavailable):
                get_model_client()

    def test_without_key_import_startup_training_and_analysis_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(store, "DB_PATH", Path(directory) / "startup.db"), \
                patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            spec = importlib.util.spec_from_file_location("startup_main", contract.ROOT / "backend/main.py")
            main = importlib.util.module_from_spec(spec)
            with patch.object(store, "_connect", side_effect=AssertionError("Import must not initialize DB")):
                spec.loader.exec_module(main)
            async def exercise():
                async with main.app.router.lifespan_context(main.app):
                    state = store.get_or_create_experiment(800, 600)
                    session = fixtures.make_session(state)
                    self.assertEqual((await contract.asgi_request(main.app, "POST", "/sessions", session))[0], 200)
                    self.assertEqual((await contract.asgi_request(main.app, "GET", "/sessions/" + session["sessionId"]))[0], 200)
                    status, result = await contract.asgi_request(main.app, "GET", "/experiments/" + state["experiment_id"] + "/analysis")
                    self.assertEqual(status, 200)
                    self.assertEqual(result["evidence"]["session_count"], 1)
                    status, result = await contract.asgi_request(main.app, "POST", "/agent/plan", {"goal": "test", "experiment_id": state["experiment_id"]})
                    self.assertEqual(status, 503)
                    self.assertEqual(result["detail"]["code"], "model_unavailable")
                    self.assertTrue(result["detail"]["request_id"])
            import asyncio
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
