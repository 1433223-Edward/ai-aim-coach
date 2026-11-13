"""Validated recommendations, explicit acceptance and descriptive outcome evaluation."""
import json
import statistics
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from agent.experiment_planner import run_experiment_planner
from analysis.scenarios import ScenarioConfig, snapshot, target_at
from analysis.training_tools import TrainingBounds, TrainingContext, HistoryFilter, canonical, load_history
from backend import session_store as store
from backend.settings import ExperimentSettings


class TrainingRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    goal: str = Field(min_length=1, max_length=1000)
    bounds: TrainingBounds = Field(default_factory=TrainingBounds)
    history_filter: HistoryFilter = Field(default_factory=HistoryFilter)
    trainer_width: int = Field(ge=100, le=10000)
    trainer_height: int = Field(ge=100, le=10000)


class AcceptRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    trainer_width: int = Field(ge=100, le=10000)
    trainer_height: int = Field(ge=100, le=10000)
    preferences: ExperimentSettings = Field(default_factory=ExperimentSettings)


def _read(connection, plan_id):
    row = connection.execute("SELECT * FROM training_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if row is None:
        raise store.ExperimentNotFound("Training plan not found.")
    return row


def _view(row):
    plan = json.loads(row["payload"])
    return {**plan, "plan_status": row["status"], "experiment_id": row["experiment_id"],
            "evaluation": json.loads(row["evaluation_json"]) if row["evaluation_json"] else None}


def get_plan(plan_id):
    with closing(store._connect()) as connection:
        return _view(_read(connection, plan_id))


def list_plans(limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid plan history limit.")
    with closing(store._connect()) as connection:
        # History needs plan identity and outcomes, not repeated baseline payloads.
        plans = [_view(row) for row in connection.execute("SELECT * FROM training_plans ORDER BY created_at DESC,rowid DESC LIMIT ?",(limit,))]
        return [{key:plan[key] for key in ("plan_id","created_at","plan_status","scenario_id","sessions",
                "task_parameters","experiment_id","evaluation")} for plan in plans]


def create_plan(request):
    if not request.goal.strip():
        raise ValueError("Goal must not be blank.")
    context = TrainingContext(request.goal, request.bounds, [request.trainer_width, request.trainer_height], request.history_filter)
    result = run_experiment_planner(request.goal, "training_history", training_context=context)
    # The runtime accepts only an exact copy of the deterministic tool proposal.
    output = json.loads(result["answer"])
    proposal = output["plan"]
    config = ScenarioConfig(**proposal["task_parameters"])
    target_at(snapshot(config.model_dump()), request.trainer_width, request.trainer_height, 0)
    plan = {**proposal, "plan_id": str(uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
            "plan_version": "training-plan-1", "viewport": context.viewport,
            "bounds": request.bounds.model_dump(), "frozen_scenario": snapshot(config.model_dump()),
            "evidence": context.metadata(), "baseline_records": context.records(),
            "model_explanation_unverified": output["explanation"],
            "agent": {k: result[k] for k in ("tools_used", "trace", "usage", "request_id")}}
    with closing(store._connect()) as connection, connection:
        connection.execute("INSERT INTO training_plans(plan_id,created_at,status,payload) VALUES(?,?,?,?)",
                           (plan["plan_id"], plan["created_at"], "proposed", canonical(plan)))
    return get_plan(plan["plan_id"])


def reject_plan(plan_id):
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _read(connection, plan_id)
        if row["status"] not in ("proposed", "rejected"):
            raise store.ExperimentConflict("An accepted plan cannot be rejected retroactively.")
        connection.execute("UPDATE training_plans SET status='rejected' WHERE plan_id=?", (plan_id,))
    return get_plan(plan_id)


def accept_plan(plan_id, request):
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _read(connection, plan_id)
        plan = json.loads(row["payload"])
        if plan["viewport"] != [request.trainer_width, request.trainer_height]:
            raise store.ExperimentConflict("Restore the plan viewport or request a new recommendation.")
        if row["status"] == "accepted":
            # Retrying a lost acceptance response must not create another Experiment.
            experiment = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (row["experiment_id"],)).fetchone()
            return {"plan": _view(row), "experiment": store._experiment_state(connection, experiment)}
        if row["status"] != "proposed":
            raise store.ExperimentConflict("Only a proposed plan can be accepted.")
        if connection.execute("SELECT 1 FROM experiments WHERE active_slot=1").fetchone():
            raise store.ExperimentConflict("Finish or explicitly end the active Experiment before accepting a plan.")
        parameters = ScenarioConfig(**plan["task_parameters"]).model_dump()
        frozen = snapshot(parameters)
        if canonical(frozen) != canonical(plan["frozen_scenario"]):
            raise store.ExperimentConflict("Plan scoring version changed; request a new plan.")
        target_at(frozen, request.trainer_width, request.trainer_height, 0)
        # Visual preferences are user-owned. Execution counts and difficulty come only from the plan.
        preferences = request.preferences.model_dump()
        preferences.update(sessions=plan["sessions"], attempts=parameters["target_count"] if frozen["category"] == "static" else 1,
                           sensitivity=1.0, time_limit_seconds=0, countdown_seconds=0)
        experiment = store.get_or_create_experiment(request.trainer_width, request.trainer_height,
                                                    preferences, parameters, _connection=connection)
        protocol = {**experiment["protocol"], "training_plan_id": plan_id}
        connection.execute("UPDATE experiments SET protocol=? WHERE experiment_id=?", (canonical(protocol), experiment["experiment_id"]))
        connection.execute("UPDATE training_plans SET status='accepted',experiment_id=? WHERE plan_id=?",
                           (experiment["experiment_id"], plan_id))
        experiment["protocol"] = protocol
    return {"plan": get_plan(plan_id), "experiment": experiment}


def evaluate_plan(plan_id):
    plan = get_plan(plan_id)
    if plan["plan_status"] != "accepted":
        raise store.ExperimentConflict("Plan has not been accepted.")
    records, _ = load_history(plan["experiment_id"])
    completed = [r for r in records if r["status"] == "complete" and r["eligible"]]
    # Keep before/after identities; unlike conditions never enter the same mean.
    baseline = [r for r in plan["baseline_records"] if r["session_id"] in plan["evidence_session_ids"] and r["eligible"]]
    matching = [r for r in completed if baseline and r["condition_key"] == baseline[0]["condition_key"]]
    sufficient = len(baseline) >= 3 and len(matching) >= 2
    metric = plan["target"]["metric"]
    def value(record):
        return record["score"] if metric == "overall_score" else record["metrics"][metric]
    delta = statistics.mean(map(value, matching))-statistics.mean(map(value, baseline)) if sufficient else None
    reached = (delta > 0 if plan["target"]["direction"] == "increase" else delta < 0) if sufficient else None
    components = {name: statistics.mean(r["normalized_components"][name] for r in matching)
                  for name in matching[0]["normalized_components"]} if sufficient else {}
    weak_before = plan["rationale"]["weak_component"]
    weak_after = min(components, key=components.get) if components and max(components.values())-min(components.values()) > 1e-9 else None
    experiment = store.get_experiment(plan["experiment_id"])
    result = {"plan_id": plan_id, "experiment_id": plan["experiment_id"],
              "evaluated_at": datetime.now(timezone.utc).isoformat(),
              "completion_rate": len(completed)/plan["sessions"], "experiment_ended": experiment["ended"],
              "baseline_session_ids": [r["session_id"] for r in baseline],
              "execution_session_ids": [r["session_id"] for r in records],
              "comparison_session_ids": [r["session_id"] for r in matching],
              "sufficient": sufficient, "target_metric": metric, "metric_delta": delta, "target_reached": reached,
              "weak_component_before": weak_before, "weak_component_after": weak_after,
              "recommendation_basis": "insufficient_evidence" if not sufficient else "no_distinct_relative_weakness" if weak_before is None and weak_after is None else "still_observed" if weak_before == weak_after else "relative_weakness_changed",
              "follow_up": "collect_more_same_condition_data" if not sufficient else "continue" if reached else "adjust",
              "limitation": "Descriptive before/after comparison; no causal attribution to the AI recommendation."}
    with closing(store._connect()) as connection, connection:
        connection.execute("UPDATE training_plans SET evaluation_json=? WHERE plan_id=?", (canonical(result), plan_id))
    return result
