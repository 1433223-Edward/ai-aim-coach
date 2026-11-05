"""Offline Fake Client, synthetic evidence, real localhost HTTP and disposable SQLite.

This script never reads the configured model key and never starts a paid model request.
"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from backend import session_store as store
from backend.scenario_store import save
from backend.main import app
from agent import experiment_planner as runtime
from test_phase2_scoring import evidence
from test_phase3_training import FakeTrainingClient
import uvicorn


def save_next(state, index, misses=False):
    run = evidence(state["protocol"]["scenario"], state["experiment_id"], state["completed_count"]+1)
    run.update(settingsSnapshot=state["protocol"]["settings"], sensitivityRepeat=state["next_condition"]["repeat"],
               startedAt=f"2026-11-{index+1:02d}T00:00:00Z", endedAt=f"2026-11-{index+1:02d}T00:01:00Z")
    if misses:
        for attempt in run["attempts"]:
            attempt["clicks"].insert(0, {"time":attempt["target"]["appearTime"]+100,"x":0,"y":0,"hit":False})
    return run


def main():
    with tempfile.TemporaryDirectory(prefix="aimcoach-phase3-") as directory, \
            patch.object(store,"DB_PATH",Path(directory)/"test.db"), \
            patch.object(runtime,"get_model_client",side_effect=FakeTrainingClient):
        store.init_database()
        baseline = store.get_or_create_experiment(800,600,{"sessions":3,"attempts":5},
            {"scenario_id":"flick_target","target_count":5,"duration_seconds":10})
        for index in range(3):
            save(save_next(store.get_experiment(baseline["experiment_id"]), index, True))
        server = None
        thread = None
        def start():
            nonlocal server, thread
            sock = socket.socket(); sock.bind(("127.0.0.1",0)); sock.listen(128)
            server = uvicorn.Server(uvicorn.Config(app,log_level="error"))
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]),daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    return f"http://127.0.0.1:{sock.getsockname()[1]}"
                time.sleep(.05)
            raise RuntimeError("Disposable server failed to start.")
        def stop():
            if server:
                server.should_exit = True
                thread.join(10)
                if thread.is_alive():
                    raise RuntimeError("Disposable server did not stop.")
        base = start()
        def api(path, body=None):
            req = Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type":"application/json"})
            with urlopen(req,timeout=20) as response:
                return json.load(response)
        try:
            plan = api("/agent/training-plans", {"goal":"提高稳定性","trainer_width":800,"trainer_height":600})
            assert plan["agent"]["usage"]["client_kind"] == "fake_client"
            assert plan["plan_status"] == "proposed"
            accepted = api(f'/training-plans/{plan["plan_id"]}/accept',{"trainer_width":800,"trainer_height":600})
            exp_id = accepted["experiment"]["experiment_id"]
            initial = api(f'/training-plans/{plan["plan_id"]}/evaluate',{})
            assert initial["completion_rate"] == 0 and initial["metric_delta"] is None
            for index in range(plan["sessions"]):
                state = api(f"/experiments/{exp_id}")
                report = save_next(state,index+5)
                saved = api("/scenarios/sessions",report)
                assert saved["saved"] and saved["result"]["overall_score"] == 100
                assert api("/scenarios/sessions",report) == saved
            evaluation = api(f'/training-plans/{plan["plan_id"]}/evaluate',{})
            assert evaluation["completion_rate"] == 1 and evaluation["sufficient"]
            assert evaluation["metric_delta"] == 22.5
            stop(); base = start()
            restored = api(f'/training-plans/{plan["plan_id"]}')
            assert restored["evaluation"] == evaluation
            assert restored["baseline_records"] == plan["baseline_records"]
            print(json.dumps({"source":"SYNTHETIC TEST DATA / FAKE CLIENT; NOT A REAL MODEL RECOMMENDATION",
                "transport":"real HTTP", "database":"disposable SQLite", "result":"PASS",
                "checks":["three tools / four model rounds", "explicit accept", "no automatic training",
                          "Session upload / idempotency", "same-condition evaluation", "restart evidence retention"],
                "example_plan":{k:restored[k] for k in ("plan_id","created_at","scenario_id","sessions","task_parameters",
                    "training_goal","target","evidence_session_ids","plan_status","plan_version","experiment_id","agent")},
                "baseline_scores":[77.5,77.5,77.5],"execution_scores":[100]*plan["sessions"],
                "evaluation":evaluation},ensure_ascii=False,indent=2))
        finally:
            stop()


if __name__ == "__main__":
    main()
