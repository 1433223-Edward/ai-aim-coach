"""Bounded historical evidence and deterministic training decisions, never model metrics."""
import hashlib
import json
import statistics
from contextlib import closing
from copy import deepcopy
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from analysis.scenarios import ScenarioConfig, snapshot as scenario_snapshot
from analysis.scoring import score
from backend import session_store as store


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


class HistoryFilter(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    limit: int = Field(default=20, ge=1, le=50)
    date_from: str | None = None
    date_to: str | None = None
    scenario_id: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        for value in (self.date_from, self.date_to):
            if value is not None:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("Dates must use YYYY-MM-DD.")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Date range is reversed.")
        if self.scenario_id not in (None, "flick_2d"):
            ScenarioConfig(scenario_id=self.scenario_id)
        return self


class TrainingBounds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    time_budget_seconds: int = Field(default=120, ge=10, le=3600)
    max_sessions: int = Field(default=3, ge=1, le=10)
    max_attempts: int = Field(default=20, ge=5, le=100)
    max_duration_seconds: int = Field(default=60, ge=10, le=120)
    max_difficulty: Literal["easy", "normal", "hard"] = "normal"


class SessionSelection(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    session_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.session_ids)) != len(self.session_ids) or any(not s.strip() for s in self.session_ids):
            raise ValueError("Session IDs must be unique and nonblank.")
        return self


def load_history(experiment_id=None, history_filter=None):
    """Read a bounded request snapshot across both evidence families in one transaction."""
    query = history_filter or HistoryFilter()
    with closing(store._connect()) as connection:
        connection.execute("BEGIN")
        rows = connection.execute("""SELECT *, CASE WHEN json_valid(raw_data) THEN coalesce(json_extract(raw_data,'$.startedAt'),recorded_at) ELSE recorded_at END AS history_time FROM (
            SELECT session_id,experiment_id,raw_data,NULL AS result_json,'complete' AS status,
                   started_at AS recorded_at,'legacy' AS family,'flick_2d' AS scene FROM sessions
            UNION ALL SELECT session_id,experiment_id,raw_data,NULL,'incomplete',started_at,'legacy','flick_2d' FROM incomplete_sessions
            UNION ALL SELECT session_id,experiment_id,raw_data,result_json,status,saved_at,'scenario',scenario_id FROM scenario_sessions)
            WHERE (? IS NULL OR experiment_id=?)
            AND (? IS NULL OR scene=?)
            AND (? IS NULL OR substr(CASE WHEN json_valid(raw_data) THEN coalesce(json_extract(raw_data,'$.startedAt'),recorded_at) ELSE recorded_at END,1,10)>=?)
            AND (? IS NULL OR substr(CASE WHEN json_valid(raw_data) THEN coalesce(json_extract(raw_data,'$.startedAt'),recorded_at) ELSE recorded_at END,1,10)<=?)
            ORDER BY history_time DESC,session_id DESC LIMIT 101""", (
                experiment_id, experiment_id, query.scenario_id, query.scenario_id,
                query.date_from, query.date_from, query.date_to, query.date_to)).fetchall()
        records = []
        for row in rows[:100]:
            item = {k: row[k] for k in ("session_id", "experiment_id", "status")}
            item.update(scenario_id="unknown", parameters=None, metrics=None, score=None,
                        condition_key=None, scoring_version=None, normalized_components=None,
                        started_at=row["recorded_at"], eligible=False)
            try:
                raw = json.loads(row["raw_data"])
                if raw["sessionId"] != row["session_id"] or raw.get("experimentId", row["experiment_id"]) != row["experiment_id"]:
                    raise ValueError("Identity mismatch")
                item["started_at"] = raw["startedAt"]
                date.fromisoformat(item["started_at"][:10])
                experiment_row = connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (row["experiment_id"],)).fetchone()
                if experiment_row:
                    state = store._experiment_state(connection, experiment_row)
                    item["experiment"] = {k: state[k] for k in ("experiment_id", "protocol", "completed_count", "status", "ended")}
                else:
                    item["experiment"] = {"experiment_id": row["experiment_id"], "managed": False}
                if row["family"] == "scenario":
                    config = raw["scenarioSnapshot"]
                    if canonical(scenario_snapshot({k: config[k] for k in ScenarioConfig.model_fields})) != canonical(config):
                        raise ValueError("Unregistered configuration")
                    protocol_row = connection.execute("SELECT protocol FROM experiments WHERE experiment_id=?", (row["experiment_id"],)).fetchone()
                    if not protocol_row or canonical(json.loads(protocol_row[0]).get("scenario")) != canonical(config):
                        raise ValueError("Protocol mismatch")
                    # Recompute current-version evidence; a stored label alone cannot prove integrity.
                    calculated = score(raw, config)
                    saved = json.loads(row["result_json"])
                    for key in ("metrics", "overall_score", "eligible", "normalized_components", "scoring_version"):
                        if canonical(calculated[key]) != canonical(saved[key]):
                            raise ValueError("Stored score mismatch")
                    if raw["status"] != row["status"]:
                        raise ValueError("Status mismatch")
                    condition = [config, raw["trainerWidth"], raw["trainerHeight"]]
                    item.update(scenario_id=config["scenario_id"], parameters=config,
                                viewport=[raw["trainerWidth"], raw["trainerHeight"]],
                                metrics=calculated["metrics"], score=calculated["overall_score"],
                                eligible=calculated["eligible"], scoring_version=calculated["scoring_version"],
                                normalized_components=calculated["normalized_components"],
                                condition_key=hashlib.sha256(canonical(condition).encode()).hexdigest())
                else:
                    item.update(scenario_id="flick_2d", parameters={k: raw.get(k) for k in
                                ("trainerSensitivity", "targetCount", "trainerWidth", "trainerHeight", "settingsSnapshot")})
                    if row["status"] == "complete":
                        from analysis.session_validator import require_valid_session
                        from analysis.analysis_service import calculate_session_summary
                        derived = deepcopy(raw)
                        derived.setdefault("experimentId", row["experiment_id"])
                        require_valid_session(derived)
                        item["metrics"] = calculate_session_summary(derived)["metrics"]
                    item["limitation"] = "Legacy metrics have no aim-score-1 score; excluded from scenario comparisons."
            except (ValueError, TypeError, KeyError, OverflowError):
                # Keep abnormal records visible but never use them as numerical evidence.
                item.update(status="abnormal", metrics=None, score=None, eligible=False)
                item["started_at"] = str(row["recorded_at"])
                item["error"] = "invalid_or_unsupported_evidence"
            records.append(item)
        return records, len(rows) > 100


def get_training_history(records, **arguments):
    query = HistoryFilter(**arguments)
    selected = [r for r in records if
                (not query.scenario_id or r["scenario_id"] == query.scenario_id) and
                (not query.date_from or r["started_at"][:10] >= query.date_from) and
                (not query.date_to or r["started_at"][:10] <= query.date_to)]
    # Do not repeat an Experiment protocol or null metrics in every model-visible Session.
    public = []
    for row in selected[:query.limit]:
        item = {k: deepcopy(v) for k, v in row.items() if k not in ("experiment", "normalized_components")}
        if item["metrics"]:
            item["metrics"] = {k: v for k, v in item["metrics"].items() if v is not None}
        if row["scoring_version"]:
            item["parameters"] = {k: row["parameters"][k] for k in ScenarioConfig.model_fields}
        public.append(item)
    return {"sessions": public,
            "experiment_ids": sorted({r["experiment_id"] for r in selected[:query.limit]}),
            "experiments": [deepcopy(next(r["experiment"] for r in selected[:query.limit] if r["experiment_id"] == identity and "experiment" in r))
                            for identity in sorted({r["experiment_id"] for r in selected[:query.limit] if "experiment" in r})],
            "matched_in_snapshot": len(selected), "returned": min(query.limit, len(selected))}


def analyze_performance_trend(records, session_ids=None):
    selection = SessionSelection(session_ids=session_ids if session_ids is not None else [])
    known = {r["session_id"]: r for r in records}
    if set(selection.session_ids) - known.keys():
        raise ValueError("Session ID is missing from this bounded snapshot.")
    selected = [known[i] for i in selection.session_ids] if selection.session_ids else records
    groups = {}
    for record in selected:
        if record["status"] == "complete" and record["eligible"] and record["condition_key"]:
            groups.setdefault(record["condition_key"], []).append(record)
    result = []
    for key, rows in groups.items():
        rows = sorted(rows, key=lambda r: (r["started_at"], r["session_id"]))
        # Operational sample sufficiency is descriptive, not a statistical significance claim.
        sufficient = len(rows) >= 3
        components = {name: statistics.mean(r["normalized_components"][name] for r in rows)
                      for name in rows[0]["normalized_components"]}
        metrics = {"overall_score": [r["score"] for r in rows]}
        for name in rows[0]["metrics"]:
            values = [r["metrics"][name] for r in rows]
            if all(type(v) in (int, float) for v in values):
                metrics[name] = values
        split = max(1, len(rows)//2)
        changes = {name: {"earlier_mean": statistics.mean(values[:split]),
                          "later_mean": statistics.mean(values[split:]),
                          "delta": statistics.mean(values[split:])-statistics.mean(values[:split])}
                   for name, values in metrics.items() if len(rows) >= 2}
        result.append({"condition_key": key, "scenario_id": rows[0]["scenario_id"],
                       "parameters": rows[0]["parameters"], "viewport": rows[0]["viewport"],
                       "sample_count": len(rows), "sufficient": sufficient,
                       "evidence_session_ids": [r["session_id"] for r in rows],
                       "weak_component": min(components, key=components.get) if max(components.values())-min(components.values()) > 1e-9 else None,
                       "components": components, "changes": changes})
    return {"groups": result, "evidence_session_ids": [r["session_id"] for r in selected if r["eligible"]],
            "limitation": "Only identical full configurations and viewport are compared. Changes are descriptive, not causal."}


def recommend_training_plan(records, goal, bounds, viewport):
    trends = analyze_performance_trend(records)
    # Preserve an observed condition when it fits every user constraint, enabling a valid later baseline.
    allowed = ["easy", "normal", "hard"][:["easy", "normal", "hard"].index(bounds.max_difficulty)+1]
    candidates = [g for g in trends["groups"] if g["viewport"] == viewport and
                  g["parameters"]["difficulty"] in allowed and
                  g["parameters"]["target_count"] <= bounds.max_attempts and
                  g["parameters"]["duration_seconds"] <= min(bounds.max_duration_seconds, bounds.time_budget_seconds)]
    # Explicit task words narrow the registry choice; unknown goals use a conservative baseline task.
    task_words = (("reactive_tracking", ("reactive", "反应", "变向")),
                  ("vertical_tracking", ("vertical", "垂直")),
                  ("horizontal_tracking", ("horizontal", "水平")),
                  ("four_target_switching", ("switch", "切换")),
                  ("six_target_precision", ("precision", "精准", "微调")),
                  ("flick_target", ("flick", "定位")))
    requested_scene = next((scene for scene, words in task_words if any(word in goal.lower() for word in words)), None)
    tracking_goal = requested_scene.endswith("tracking") if requested_scene else any(word in goal.lower() for word in ("track", "跟随", "跟踪"))
    relevant = [g for g in candidates if (g["parameters"]["category"] == "tracking") == tracking_goal and
                (requested_scene is None or g["scenario_id"] == requested_scene)]
    chosen = min(relevant, key=lambda g: min(g["components"].values()), default=None)
    parameters = {k: chosen["parameters"][k] for k in ScenarioConfig.model_fields} if chosen else ScenarioConfig(
        scenario_id=requested_scene or ("horizontal_tracking" if tracking_goal else "flick_target"),
        difficulty=bounds.max_difficulty, target_count=min(20, bounds.max_attempts),
        duration_seconds=min(20, bounds.max_duration_seconds, bounds.time_budget_seconds)).model_dump()
    sessions = min(bounds.max_sessions, bounds.time_budget_seconds//parameters["duration_seconds"])
    metric, direction = "overall_score", "increase"
    if tracking_goal:
        metric = "tracking_coverage"
    elif any(word in goal.lower() for word in ("accuracy", "准确", "精准")):
        metric = "accuracy"
    elif any(word in goal.lower() for word in ("speed", "速度", "快速")):
        metric, direction = "average_hit_time_ms", "decrease"
    # Goal-specific metrics must exist for the selected category.
    if parameters["scenario_id"].endswith("tracking") and metric in ("accuracy", "average_hit_time_ms"):
        metric, direction = "overall_score", "increase"
    return {"scenario_id": parameters["scenario_id"], "sessions": sessions,
            "task_parameters": parameters, "training_goal": goal,
            "evidence_session_ids": chosen["evidence_session_ids"] if chosen else [],
            "target": {"metric": metric, "direction": direction, "minimum_delta": 0},
            "rationale": {"source": "deterministic_rules", "weak_component": chosen["weak_component"] if chosen else None,
                          "sufficient": bool(chosen and chosen["sufficient"]),
                          "reason": "observed_relative_weakness" if chosen and chosen["weak_component"] else "balanced_condition_practice" if chosen else "first_or_unmatched_condition_baseline"}}


class TrainingContext:
    prompt = """You are the personalized training planner. Call get_training_history, then
analyze_performance_trend, then recommend_training_plan. Tools own every statistic and parameter.
Never invent history or causal improvement. Never start training. Return ONLY JSON:
{"plan": <exact recommend_training_plan result>, "explanation": "brief explanation; observations versus proposal and unknowns"}.
Do not modify the plan object. A user must accept it before execution."""

    def __init__(self, goal, bounds, viewport, history_filter=None):
        self.goal, self.bounds, self.viewport = goal, bounds, viewport
        # Apply request scope before LIMIT so older date ranges remain queryable without loading all history.
        self.history_filter = history_filter or HistoryFilter()
        self._records, self.truncated = load_history(history_filter=self.history_filter)
        self.history_seen = self.trend_seen = False
        self.candidate = None
        self._snapshot_id = hashlib.sha256(canonical(self._records).encode()).hexdigest()
        models = {"get_training_history": HistoryFilter, "analyze_performance_trend": SessionSelection}
        self.tools = [{"type": "function", "function": {"name": name,
                       "description": "Read fixed bounded evidence; never write training data.",
                       "parameters": model.model_json_schema() if model else {"type": "object", "properties": {}, "additionalProperties": False}}}
                      for name, model in [*models.items(), ("recommend_training_plan", None)]]

    def result(self):
        # Training has its own evidence gate; no synthetic sensitivity metrics enter the legacy gate.
        return {}

    def metadata(self):
        return {"snapshot_id": self._snapshot_id, "session_ids": [r["session_id"] for r in self._records],
                "scope": "latest_100_across_all_families", "truncated": self.truncated,
                "bounds": self.bounds.model_dump(), "viewport": self.viewport,
                "history_filter": self.history_filter.model_dump()}

    def validate(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("Expected an argument object.")
        if name == "get_training_history":
            HistoryFilter(**arguments)
        elif name == "analyze_performance_trend":
            SessionSelection(**arguments)
        elif name != "recommend_training_plan" or arguments:
            raise ValueError("Unknown tool or extra arguments.")

    def execute(self, name, arguments):
        self.validate(name, arguments)
        if name == "get_training_history":
            result = get_training_history(self._records, **{"limit": self.history_filter.limit, **arguments})
            self.history_seen = True
        elif name == "analyze_performance_trend":
            if not self.history_seen:
                raise ValueError("Read history before interpreting it.")
            result = analyze_performance_trend(self._records, **arguments)
            self.trend_seen = True
        else:
            if not self.trend_seen:
                raise ValueError("Analyze evidence before recommending.")
            self.candidate = recommend_training_plan(self._records, self.goal, self.bounds, self.viewport)
            result = self.candidate
        return deepcopy({**result, "evidence": {"snapshot_id": self._snapshot_id}}) if name != "recommend_training_plan" else deepcopy(result)

    def accepts(self, content):
        try:
            output = json.loads(content)
            return (self.candidate is not None and set(output) == {"plan", "explanation"} and
                    isinstance(output["explanation"], str) and 0 < len(output["explanation"]) <= 4000 and
                    canonical(output["plan"]) == canonical(self.candidate))
        except (ValueError, TypeError):
            return False

    def records(self):
        return deepcopy(self._records)
