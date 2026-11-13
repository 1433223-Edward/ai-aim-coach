"""Read-only UI projections use isolated records and never real SQLite or a real model."""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import session_store as store, training_plans
from backend.scenario_store import save
from backend.main import app
from agent import experiment_planner as runtime
from test_phase2_scoring import evidence
from test_phase3_training import FakeTrainingClient


class HistoryViewTests(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(store,"DB_PATH",Path(directory)/"ui.db"))
        store.init_database()

    def test_scene_projection_returns_real_conditions_and_marks_bad_rows(self):
        state=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":5},{"scenario_id":"flick_target","target_count":5,"duration_seconds":10})
        ids=[]
        for _ in range(2):
            state=store.get_experiment(state["experiment_id"])
            report=evidence(state["protocol"]["scenario"],state["experiment_id"],state["completed_count"]+1)
            report.update(settingsSnapshot=state["protocol"]["settings"],sensitivityRepeat=state["next_condition"]["repeat"])
            save(report);ids.append(report["sessionId"])
        with TestClient(app) as client:
            rows=client.get("/scenarios/sessions?limit=10").json()
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]["viewport"],[800,600])
            self.assertEqual(rows[0]["parameters"],state["protocol"]["scenario"])
            self.assertEqual(rows[0]["result"]["overall_score"],100)
            with closing(store._connect()) as connection,connection:
                connection.execute("UPDATE scenario_sessions SET result_json='broken' WHERE session_id=?",(ids[0],))
            response=client.get("/scenarios/sessions")
            self.assertEqual(response.status_code,200)
            self.assertEqual(sum(row["status"]=="abnormal" for row in response.json()),1)
            self.assertEqual(sum(row.get("result",{}).get("overall_score")==100 for row in response.json() if row["result"]),1)

    def test_plan_history_is_bounded_and_omits_raw_baseline_payloads(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/training-plans").json(),[])
            self.assertEqual(client.get("/training-plans?limit=51").status_code,422)
            with patch.object(runtime,"get_model_client",return_value=FakeTrainingClient()):
                plan=training_plans.create_plan(training_plans.TrainingRequest(goal="速度",trainer_width=800,trainer_height=600))
            rows=client.get("/training-plans?limit=1").json()
            self.assertEqual(rows[0]["plan_id"],plan["plan_id"])
            self.assertEqual(rows[0]["plan_status"],"proposed")
            self.assertNotIn("baseline_records",rows[0])
            self.assertIsNone(rows[0]["experiment_id"])
            with closing(store._connect()) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM experiments").fetchone()[0],0)

    def test_free_scenario_freezes_appearance_without_changing_execution_contract(self):
        with TestClient(app) as client:
            body={"trainer_width":800,"trainer_height":600,"scenario":{"scenario_id":"flick_target","target_count":5,"duration_seconds":10},
                  "preferences":{"crosshair_color":"#00ff00","target_color":"#ffcc00","sessions":30,"attempts":99,"sensitivity":5.0}}
            response=client.post("/scenarios/experiments",json=body)
            self.assertEqual(response.status_code,200,response.text)
            state=response.json();settings=state["protocol"]["settings"]
            self.assertEqual(settings["crosshair_color"],"#00ff00")
            self.assertEqual(settings["target_color"],"#ffcc00")
            self.assertEqual(len(state["schedule"]),1)
            self.assertEqual(settings["attempts"],5)
            self.assertEqual(settings["sensitivity"],1)
            body["preferences"]["crosshair_color"]="#ff0000"
            restored=client.post("/scenarios/experiments",json=body).json()
            self.assertEqual(restored["protocol"]["settings"]["crosshair_color"],"#00ff00")
