"""Store alternate Session evidence separately so legacy Agent metrics cannot consume tracking samples."""
import json
from contextlib import closing
from backend import session_store as store
from analysis.scoring import score

def save(session):
    if not isinstance(session,dict):raise ValueError("Session must be an object.")
    for key in ("sessionId","experimentId"):
        if not isinstance(session.get(key),str) or not 1<=len(session[key])<=128 or not session[key].strip():raise ValueError("Invalid identity.")
    raw=json.dumps(session,sort_keys=True,allow_nan=False)
    with closing(store._connect()) as connection,connection:
        connection.execute("BEGIN IMMEDIATE")
        existing=connection.execute("SELECT raw_data,result_json FROM scenario_sessions WHERE session_id=?",(session["sessionId"],)).fetchone()
        if existing:
            if existing["raw_data"]!=raw:raise store.ExperimentConflict("Session ID has another payload.")
            return json.loads(existing["result_json"])
        if connection.execute("SELECT 1 FROM sessions WHERE session_id=? UNION ALL SELECT 1 FROM incomplete_sessions WHERE session_id=?",(session["sessionId"],session["sessionId"])).fetchone():raise store.ExperimentConflict("Session identity already exists.")
        row=connection.execute("SELECT * FROM experiments WHERE experiment_id=?",(session["experimentId"],)).fetchone()
        if row is None:raise store.ExperimentNotFound("Unknown experiment.")
        # The same transaction binds progress, the frozen protocol, evidence and its stored score.
        state=store._experiment_state(connection,row)
        config=state["protocol"].get("scenario")
        if not config or json.dumps(session.get("scenarioSnapshot"),sort_keys=True,allow_nan=False)!=json.dumps(config,sort_keys=True,allow_nan=False):raise store.ExperimentConflict("Scenario snapshot mismatch.")
        order=session.get("experimentOrder")
        if type(order) is not int or not 1<=order<=len(state["schedule"]):raise store.ExperimentConflict("Invalid experiment condition.")
        if session.get("status")=="complete" and (state["ended"] or order!=state["completed_count"]+1):raise store.ExperimentConflict("Stale experiment condition.")
        if (session.get("trainerWidth"),session.get("trainerHeight"))!=(state["protocol"]["trainer_width"],state["protocol"]["trainer_height"]):raise store.ExperimentConflict("Viewport mismatch.")
        if type(session.get("targetCount")) is not int or session["targetCount"]!=(config["target_count"] if config["category"]=="static" else 1):raise ValueError("Target count mismatch.")
        if session.get("trainerSensitivity")!=state["schedule"][order-1]["sensitivity"] or session.get("sensitivityRepeat")!=state["schedule"][order-1]["repeat"]:raise store.ExperimentConflict("Session condition mismatch.")
        from backend.settings import ExperimentSettings
        if ExperimentSettings(**session.get("settingsSnapshot",{})).model_dump()!=state["protocol"]["settings"]:raise store.ExperimentConflict("Settings snapshot mismatch.")
        from datetime import datetime
        if not all(isinstance(session.get(key),str) for key in ("startedAt","endedAt")):
            raise ValueError("Session wall-clock timestamps are required.")
        started=datetime.fromisoformat(session["startedAt"].replace("Z","+00:00"));ended=datetime.fromisoformat(session["endedAt"].replace("Z","+00:00"))
        if ended<started:raise ValueError("Invalid wall-clock order.")
        result=score(session,config)
        result.update(scenario_id=config["scenario_id"],difficulty=config["difficulty"])
        import hashlib
        result["condition_key"]=hashlib.sha256(json.dumps([config,session["trainerWidth"],session["trainerHeight"]],sort_keys=True).encode()).hexdigest()
        if session["status"]=="complete" and not result["eligible"]:raise ValueError("Complete run has insufficient evidence.")
        # Compare only identical full configurations, viewport and scoring version. No global leaderboard.
        prior=[]
        for record in connection.execute("SELECT raw_data,result_json FROM scenario_sessions WHERE scenario_id=? AND status='complete'",(config["scenario_id"],)):
            evidence=json.loads(record["raw_data"])
            if evidence.get("scenarioSnapshot")==config and evidence.get("trainerWidth")==session["trainerWidth"] and evidence.get("trainerHeight")==session["trainerHeight"]:
                prior.append(json.loads(record["result_json"])["overall_score"])
        result["previous_same_condition"]={"count":len(prior),"best_score":max(prior) if prior else None}
        connection.execute("INSERT INTO scenario_sessions(session_id,experiment_id,experiment_order,scenario_id,status,raw_data,result_json) VALUES(?,?,?,?,?,?,?)",
            (session["sessionId"],session["experimentId"],session["experimentOrder"],config["scenario_id"],session["status"],raw,json.dumps(result,allow_nan=False)))
        if session["status"]=="complete" and state["completed_count"]+1==len(state["schedule"]):
            connection.execute("UPDATE experiments SET active_slot=NULL WHERE experiment_id=?",(session["experimentId"],))
        return result
