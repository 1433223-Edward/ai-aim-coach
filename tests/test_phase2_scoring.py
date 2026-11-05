"""Deterministic scenario contracts, trajectories, scoring and SQLite isolation."""
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from analysis.scenarios import registry, snapshot, target_at, ScenarioConfig
from analysis.scoring import score
from backend import session_store as store
from backend.scenario_store import save


def evidence(config,experiment_id="fixture",order=1):
    result={"schemaVersion":"scenario-1","sessionId":str(uuid4()),"experimentId":experiment_id,"experimentOrder":order,
            "scenarioSnapshot":config,"trainerWidth":800,"trainerHeight":600,"status":"complete","startTime":0,
            "trainerSensitivity":1.0,"sensitivityRepeat":order,"startedAt":"2026-11-05T00:00:00Z","endedAt":"2026-11-05T00:02:01Z",
            "targetCount":config["target_count"] if config["category"]=="static" else 1,"attempts":[]}
    def attempt(index,appear,disappear):
        x,y=target_at(config,800,600,appear,index)
        return {"attemptId":str(uuid4()),"target":{"x":x,"y":y,"radius":config["radius"],"appearTime":appear,"disappearTime":disappear,"cursorStartX":400,"cursorStartY":300},"movements":[],"clicks":[],"tracking_samples":[]}
    if config["category"]=="static":
        for index in range(config["target_count"]):
            time=(index+1)*400;item=attempt(index,index*400,time)
            item["clicks"].append({"time":time,"x":item["target"]["x"],"y":item["target"]["y"],"hit":True})
            result["attempts"].append(item)
        result["elapsed_ms"]=config["target_count"]*400
    else:
        result["elapsed_ms"]=config["duration_seconds"]*1000
        item=attempt(0,0,result["elapsed_ms"])
        for time in range(0,result["elapsed_ms"],50):
            x,y=target_at(config,800,600,time);item["tracking_samples"].append({"time":time,"x":x,"y":y})
        result["attempts"].append(item)
    result["endTime"]=result["elapsed_ms"]
    return result


class ScenarioTests(unittest.TestCase):
    def test_all_six_perfect_runs_are_reproducible_and_bounded(self):
        self.assertEqual(len(registry()),6)
        for definition in registry():
            with self.subTest(scenario=definition["scenario_id"]):
                config=snapshot({"scenario_id":definition["scenario_id"],"target_count":5,"duration_seconds":10})
                run=evidence(config);result=score(run,config)
                self.assertEqual(result,score(deepcopy(run),deepcopy(config)))
                self.assertEqual(result["overall_score"],100)
                self.assertTrue(result["eligible"])
                self.assertEqual(result["metrics"]["tracking_error_px"],0 if config["category"]=="tracking" else None)

    def test_early_exit_no_shots_and_slow_play_do_not_receive_high_scores(self):
        config=snapshot({"scenario_id":"flick_target","target_count":5,"duration_seconds":120})
        run=evidence(config);run["status"]="incomplete"
        self.assertEqual(score(run,config)["overall_score"],0)
        run.update(status="complete",attempts=[])
        self.assertEqual(score(run,config)["overall_score"],0)
        run=evidence(config)
        for index,event in enumerate(run["attempts"]):
            event["clicks"][0]["time"]=(index+1)*20000
            event["target"]["appearTime"]=index*20000;event["target"]["disappearTime"]=(index+1)*20000
        run["elapsed_ms"]=100000;run["endTime"]=100000
        self.assertLess(score(run,config)["overall_score"],20)

    def test_bad_geometry_types_density_versions_and_sampling_fail(self):
        config=snapshot({"scenario_id":"four_target_switching","target_count":5,"duration_seconds":10})
        run=evidence(config);run["attempts"][0]["clicks"][0]["hit"]=False
        with self.assertRaises(ValueError):score(run,config)
        run=evidence(config);run["attempts"][0]["clicks"][0]["x"]=True
        with self.assertRaises(ValueError):score(run,config)
        run=evidence(config);run["attempts"]*=401
        with self.assertRaises(ValueError):score(run,config)
        tracking=snapshot({"scenario_id":"horizontal_tracking","duration_seconds":10})
        run=evidence(tracking);run["attempts"][0]["tracking_samples"][1]["time"]=run["attempts"][0]["tracking_samples"][0]["time"]
        with self.assertRaises(ValueError):score(run,tracking)
        tracking["scoring_version"]="unknown"
        with self.assertRaises(ValueError):score(run,tracking)

    def test_boundaries_and_seeded_motion_match_browser(self):
        cases=[]
        for definition in registry():
            for direction in ("positive","negative"):
                config=snapshot({"scenario_id":definition["scenario_id"],"seed":2147483647,"duration_seconds":120,"speed":400,"direction":direction})
                for time in (0,50,1999,2000,2001,5000,119999):
                    x,y=target_at(config,800,600,time,7)
                    self.assertTrue(config["radius"]<=x<=800-config["radius"])
                    self.assertTrue(config["radius"]<=y<=600-config["radius"])
                    cases.append([config,time,[x,y]])
        program="import {targetAt} from './frontend/scenario_motion.js'; let data='';for await(const chunk of process.stdin)data+=chunk; console.log(JSON.stringify(JSON.parse(data).map(([c,t])=>targetAt(c,800,600,t,7))));"
        actual=json.loads(subprocess.check_output(["node","--input-type=module","-e",program],input=json.dumps(cases),text=True))
        for case,position in zip(cases,actual):
            self.assertAlmostEqual(case[2][0],position[0],places=7);self.assertAlmostEqual(case[2][1],position[1],places=7)

    def test_configuration_rejects_executable_or_unbounded_parameters(self):
        for value in ({"scenario_id":"eval"},{"scenario_id":"flick_target","function":"x"},{"scenario_id":"flick_target","duration_seconds":121},
                      {"scenario_id":"flick_target","seed":True},{"scenario_id":"vertical_tracking","speed":0}):
            with self.subTest(value=value),self.assertRaises(ValueError):ScenarioConfig(**value)

    def test_sqlite_replay_snapshot_isolation_and_same_condition_comparison(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(store,"DB_PATH",Path(directory)/"scenario.db"):
            store.init_database()
            config_input={"scenario_id":"flick_target","target_count":5,"duration_seconds":10}
            state=store.get_or_create_experiment(800,600,{"sessions":2,"attempts":5},config_input)
            config=state["protocol"]["scenario"]
            run=evidence(config,state["experiment_id"]);run["sensitivityRepeat"]=state["next_condition"]["repeat"];run["settingsSnapshot"]=state["protocol"]["settings"]
            run["overall_score"]=999
            first=save(run);self.assertEqual(first["overall_score"],100);self.assertEqual(first,save(run))
            with self.assertRaises(store.ExperimentConflict):save({**run,"overall_score":1000})
            self.assertEqual(store.get_experiment(state["experiment_id"])["completed_count"],1)
            self.assertEqual(store.get_sessions_by_experiment(state["experiment_id"]),[])
            next_state=store.get_experiment(state["experiment_id"]);next_run=evidence(config,state["experiment_id"],2);next_run["sensitivityRepeat"]=next_state["next_condition"]["repeat"];next_run["settingsSnapshot"]=state["protocol"]["settings"]
            second=save(next_run)
            self.assertEqual(second["previous_same_condition"],{"count":1,"best_score":100})
            other=deepcopy(run);other["sessionId"]=str(uuid4());other["scenarioSnapshot"]["difficulty"]="hard"
            with self.assertRaises(store.ExperimentConflict):save(other)
            store.init_database()
            self.assertEqual(store.get_experiment(state["experiment_id"])["completed_count"],2)
            self.assertEqual(store.history_experiments(scene="flick_target")[0]["saved_sessions"],2)
            detail=store.history_session_detail(run["sessionId"])
            self.assertEqual(detail["score"]["overall_score"],100)
            self.assertEqual(len(store.history_sessions(scene="flick_target")),2)
            self.assertEqual(len(store.history_sessions(scene="flick_target",date="2026-11-05")),2)
            self.assertEqual(store.history_sessions(scene="flick_target",date="2000-01-01"),[])

    def test_misses_reduce_normalized_score_and_sparse_tracking_is_ineligible(self):
        config=snapshot({"scenario_id":"four_target_switching","target_count":5,"duration_seconds":10})
        run=evidence(config)
        for attempt in run["attempts"]:
            attempt["clicks"].insert(0,{"time":attempt["target"]["appearTime"]+100,"x":0,"y":0,"hit":False})
        result=score(run,config)
        self.assertEqual(result["metrics"]["accuracy"],.5)
        self.assertEqual(result["overall_score"],77.5)
        tracking=snapshot({"scenario_id":"horizontal_tracking","duration_seconds":10})
        run=evidence(tracking);run["attempts"][0]["tracking_samples"]=run["attempts"][0]["tracking_samples"][::2]
        self.assertFalse(score(run,tracking)["eligible"])
        self.assertEqual(score(run,tracking)["overall_score"],0)
