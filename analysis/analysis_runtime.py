from backend.session_store import (
    get_latest_experiment_id,
    get_sessions_by_experiment,
    load_experiment_evidence,
    require_matching_experiment,
    ExperimentConflict,
)
from dataclasses import dataclass
import hashlib
import json

from analysis.analysis_service import (
    analyze_sessions,
)


@dataclass(frozen=True)
class AnalysisSnapshot:
    """Immutable JSON owns request evidence; every reader receives a fresh copy."""
    result_json: str
    metadata_json: str

    def result(self) -> dict:
        return json.loads(self.result_json)

    def metadata(self) -> dict:
        return json.loads(self.metadata_json)


def create_analysis_snapshot(experiment_id: str) -> AnalysisSnapshot:
    # Load a fixed session set once; later uploads only affect later requests.
    state, sessions = load_experiment_evidence(experiment_id)
    if not sessions:
        raise ExperimentConflict("Experiment has no saved sessions yet (incomplete).")
    try:
        analysis_result = analyze_sessions(sessions)
    except (ValueError, TypeError, OverflowError) as exc:
        # Invalid stored evidence is a data conflict, not a model-provider failure.
        raise ExperimentConflict(f"Stored experiment data is invalid: {exc}") from exc
    for index, session in enumerate(sessions):
        require_matching_experiment(session, {
            **state, "completed_count": index, "next_condition": state["schedule"][index],
        })
    # Hash data and analysis, not a timestamp, so identical evidence has a stable identity.
    evidence = {"protocol": state["protocol"], "schedule": state["schedule"],
                "sessions": sessions, "analysis": analysis_result}
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    metadata = {
        "experiment_id": experiment_id,
        "snapshot_id": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "session_ids": [s["sessionId"] for s in sessions],
        "session_count": len(sessions),
        "attempt_count": sum(len(s["attempts"]) for s in sessions),
        "expected_session_count": len(state["schedule"]),
        "incomplete": state["status"] != "complete",
        "protocol_version": state["protocol"]["version"],
        "protocol": state["protocol"],
        "input_modes": sorted({s["inputMode"] for s in sessions}),
    }
    return AnalysisSnapshot(json.dumps(analysis_result, allow_nan=False), json.dumps(metadata))


# ============================================================
# AI Aim Coach
# Analysis Runtime v0.1
#
# 职责：
#
# experiment_id
#      ↓
# Session Store
#      ↓
# Raw Sessions
#      ↓
# Analysis Service
#      ↓
# Structured Analysis Result
#
# Runtime 负责连接组件。
#
# 它不负责：
# - SQL
# - 指标计算
# - Agent Decision
# ============================================================


def analyze_experiment(
    experiment_id: str,
) -> dict:
    """
    对指定 Experiment 执行完整确定性分析。

    Storage 和 Analysis 的边界：

    Session Store
        experiment_id → sessions

    Analysis Service
        sessions → result
    """

    sessions = (
        get_sessions_by_experiment(
            experiment_id
        )
    )

    if not sessions:
        raise ValueError(
            f"No sessions found for "
            f"experiment {experiment_id}."
        )

    return analyze_sessions(
        sessions
    )


def analyze_latest_experiment() -> dict:
    """
    当前单用户 Application 的默认分析入口。

    自动找到数据库中最新 Experiment,
    然后生成最新 Structured Analysis Result。
    """

    experiment_id = (
        get_latest_experiment_id()
    )

    if experiment_id is None:
        raise ValueError(
            "No training experiment found."
        )

    return analyze_experiment(
        experiment_id
    )
