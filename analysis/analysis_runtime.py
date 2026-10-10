from backend.session_store import (
    get_latest_experiment_id,
    get_sessions_by_experiment,
)

from analysis.analysis_service import (
    analyze_sessions,
)


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