import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev


# ============================================================
# AI Aim Coach
# Analysis-to-Agent Bridge v0.1
#
# 职责：
# 1. 读取一批 Training Session JSON
# 2. 验证数据集基本完整性
# 3. 计算 Attempt-level Metrics
# 4. 聚合 Session / Sensitivity Statistics
# 5. 生成稳定、机器可读取的 Structured Analysis Result
#
# 当前不做：
# - print 人类可读报告
# - 最佳 sensitivity 推荐
# - 显著性检验
# - Overshoot / Correction
# - Agent / LLM 判断
# ============================================================


DATA_DIR = Path("data/experiment_002")
ANALYSIS_OUTPUT = Path("analysis/output/analysis_result.json")

SCHEMA_VERSION = "0.1"
ANALYSIS_TYPE = "sensitivity_analysis"

METRIC_NAMES = (
    "final_hit_time_ms",
    "miss_count",
    "first_click_error_px",
    "path_efficiency",
)


# ============================================================
# 1. Load / Validation
# ============================================================

def load_sessions(data_dir: Path) -> list[dict]:
    """
    加载目录中的全部 Session JSON。

    不依赖文件名判断 sensitivity，
    真实实验条件始终读取 JSON 内的 trainerSensitivity。
    """
    session_files = sorted(data_dir.glob("*.json"))

    if not session_files:
        raise FileNotFoundError(
            f"No JSON files found in: {data_dir}"
        )

    sessions = []

    for path in session_files:
        with path.open("r", encoding="utf-8") as file:
            session = json.load(file)

        sessions.append(session)

    return sessions


def validate_dataset(sessions: list[dict]) -> None:
    """
    验证参与 sensitivity comparison 的 Session 是否具备基本可比性。

    这里负责阻止明显损坏或实验条件不一致的数据进入分析层。
    验证失败直接 raise,不通过 print 输出状态。
    """
    if not sessions:
        raise ValueError("Dataset is empty.")

    required_fields = {
        "sessionId",
        "trainerSensitivity",
        "targetCount",
        "attempts",
    }

    problems = []

    for index, session in enumerate(sessions, start=1):
        missing_fields = required_fields - session.keys()

        if missing_fields:
            problems.append(
                f"Session {index}: missing fields "
                f"{sorted(missing_fields)}"
            )
            continue

        expected_count = session["targetCount"]
        actual_count = len(session["attempts"])

        if actual_count != expected_count:
            problems.append(
                f"Session {index}: expected {expected_count} attempts, "
                f"got {actual_count}"
            )

    if problems:
        raise ValueError(
            "Dataset validation failed:\n- "
            + "\n- ".join(problems)
        )


# ============================================================
# 2. Attempt Metrics
#
# 原来的 metrics.py 职责合并到本文件。
# Raw Attempt -> Deterministic Metrics
# ============================================================

def calculate_path_length(attempt: dict) -> float:
    """
    计算准星在一个 Attempt 内实际走过的路径长度。

    路径：
    cursorStart
    -> movement[0]
    -> movement[1]
    -> ...
    -> final click
    """
    target = attempt["target"]
    movements = attempt["movements"]
    clicks = attempt["clicks"]

    prev_x = target["cursorStartX"]
    prev_y = target["cursorStartY"]

    path_length = 0.0

    for movement in movements:
        current_x = movement["x"]
        current_y = movement["y"]

        path_length += math.hypot(
            current_x - prev_x,
            current_y - prev_y,
        )

        prev_x = current_x
        prev_y = current_y

    if clicks:
        final_click = clicks[-1]

        path_length += math.hypot(
            final_click["x"] - prev_x,
            final_click["y"] - prev_y,
        )

    return path_length


def calculate_attempt_metrics(attempt: dict) -> dict:
    """
    从一个 Raw Attempt 中提取 Sensitivity Analysis v0.1
    当前已经验证过的四个确定性指标。

    不做推荐，不做解释性结论。
    """
    target = attempt["target"]
    clicks = attempt["clicks"]

    if not clicks:
        raise ValueError("Attempt contains no click data.")

    first_click = clicks[0]
    final_click = clicks[-1]

    if not final_click["hit"]:
        raise ValueError("Attempt does not end with a HIT.")

    final_hit_time_ms = (
        final_click["time"]
        - target["appearTime"]
    )

    miss_count = sum(
        1
        for click in clicks
        if not click["hit"]
    )

    first_click_error_px = math.hypot(
        first_click["x"] - target["x"],
        first_click["y"] - target["y"],
    )

    start_x = target["cursorStartX"]
    start_y = target["cursorStartY"]

    direct_distance = math.hypot(
        final_click["x"] - start_x,
        final_click["y"] - start_y,
    )

    path_length = calculate_path_length(attempt)

    if path_length > 0:
        path_efficiency = direct_distance / path_length
    else:
        path_efficiency = 1.0 if direct_distance == 0 else 0.0

    return {
        "final_hit_time_ms": final_hit_time_ms,
        "miss_count": miss_count,
        "first_click_error_px": first_click_error_px,
        "path_efficiency": path_efficiency,
    }


# ============================================================
# 3. Statistics
#
# Attempt Metrics -> Fixed Statistical Summary
# ============================================================

def calculate_statistics(values: list[float]) -> dict:
    """
    所有普通 Metric 统一使用同一套统计输出格式。

    固定字段：
    - sample_count
    - mean
    - median
    - std
    """
    if not values:
        raise ValueError("Cannot calculate statistics for empty values.")

    return {
        "sample_count": len(values),
        "mean": mean(values),
        "median": median(values),
        "std": stdev(values) if len(values) >= 2 else 0.0,
    }


def calculate_miss_attempt_rate(
    metrics_list: list[dict],
) -> dict:
    """
    统计发生过至少一次 MISS 的 Attempt 比例。

    这个指标不是连续数值的 Mean/Median/Std，
    因此保留独立、固定的 behavior 输出结构。
    """
    total_attempts = len(metrics_list)

    miss_attempts = sum(
        1
        for metrics in metrics_list
        if metrics["miss_count"] > 0
    )

    rate = (
        miss_attempts / total_attempts
        if total_attempts > 0
        else 0.0
    )

    return {
        "miss_attempt_count": miss_attempts,
        "attempt_count": total_attempts,
        "miss_attempt_rate": rate,
    }


def summarize_metrics(
    metrics_list: list[dict],
) -> dict:
    """
    将一批 Attempt Metrics 统一聚合成固定统计结构。
    """
    if not metrics_list:
        raise ValueError("metrics_list is empty.")

    summary = {}

    for metric_name in METRIC_NAMES:
        values = [
            metrics[metric_name]
            for metrics in metrics_list
        ]

        summary[metric_name] = calculate_statistics(values)

    return summary


# ============================================================
# 4. Session / Sensitivity Aggregation
# ============================================================

def group_sessions_by_sensitivity(
    sessions: list[dict],
) -> dict[float, list[dict]]:
    """
    保留 Session 边界，按 trainerSensitivity 分组。
    """
    grouped = defaultdict(list)

    for session in sessions:
        grouped[session["trainerSensitivity"]].append(session)

    return dict(grouped)


def calculate_session_summary(session: dict) -> dict:
    """
    将一个 Session 转换为稳定的 Session-level Summary。

    这一层保留 experimentOrder / sensitivityRepeat,
    便于后续 Agent / Tool 判断热身或疲劳等实验顺序因素。
    """
    attempt_metrics = [
        calculate_attempt_metrics(attempt)
        for attempt in session["attempts"]
    ]

    return {
        "session_id": session["sessionId"],
        "sensitivity": session["trainerSensitivity"],
        "experiment_order": session.get("experimentOrder"),
        "sensitivity_repeat": session.get("sensitivityRepeat"),
        "attempt_count": len(attempt_metrics),
        "metrics": summarize_metrics(attempt_metrics),
        "miss_behavior": calculate_miss_attempt_rate(
            attempt_metrics
        ),
    }


def build_sensitivity_profile(
    sensitivity: float,
    sessions: list[dict],
) -> dict:
    """
    把同一 sensitivity 下的多个 Session 聚合为一个 Profile。

    Agent 以后应主要读取这一层，而不是 Raw Mouse Events。
    """
    session_summaries = [
        calculate_session_summary(session)
        for session in sessions
    ]

    all_attempt_metrics = []

    for session in sessions:
        all_attempt_metrics.extend(
            calculate_attempt_metrics(attempt)
            for attempt in session["attempts"]
        )

    return {
        "sensitivity": sensitivity,
        "session_count": len(session_summaries),
        "attempt_count": len(all_attempt_metrics),
        "metrics": summarize_metrics(all_attempt_metrics),
        "miss_behavior": calculate_miss_attempt_rate(
            all_attempt_metrics
        ),
        "session_consistency": {
            "sessions": session_summaries
        },
    }


def build_sensitivity_profiles(
    sessions: list[dict],
) -> list[dict]:
    """
    生成所有 sensitivity 的 Profile。
    """
    grouped_sessions = group_sessions_by_sensitivity(sessions)

    return [
        build_sensitivity_profile(
            sensitivity=sensitivity,
            sessions=grouped_sessions[sensitivity],
        )
        for sensitivity in sorted(grouped_sessions)
    ]


# ============================================================
# 5. Deterministic Findings
#
# 这里只输出明确规则支持的事实。
# 不负责推荐“最佳 sensitivity”。
# ============================================================

def build_path_efficiency_findings(
    profiles: list[dict],
) -> list[dict]:
    """
    当前只实现一个严格、可解释的 Finding:

    某个 sensitivity 的每个 Session Path Efficiency Median
    都低于其他 sensitivity 的所有 Session Median。

    满足才输出，不满足就不制造结论。
    """
    findings = []

    for candidate in profiles:
        candidate_sensitivity = candidate["sensitivity"]

        candidate_session_medians = [
            session["metrics"]["path_efficiency"]["median"]
            for session
            in candidate["session_consistency"]["sessions"]
        ]

        comparison_profiles = [
            profile
            for profile in profiles
            if profile["sensitivity"] != candidate_sensitivity
        ]

        comparison_session_medians = [
            session["metrics"]["path_efficiency"]["median"]
            for profile in comparison_profiles
            for session
            in profile["session_consistency"]["sessions"]
        ]

        if (
            not candidate_session_medians
            or not comparison_session_medians
        ):
            continue

        comparison_min = min(comparison_session_medians)

        supporting_sessions = sum(
            1
            for value in candidate_session_medians
            if value < comparison_min
        )

        if supporting_sessions == len(candidate_session_medians):
            findings.append({
                "finding_type": "cross_session_comparison",
                "metric": "path_efficiency",
                "condition": {
                    "sensitivity": candidate_sensitivity
                },
                "comparison": {
                    "sensitivities": [
                        profile["sensitivity"]
                        for profile in comparison_profiles
                    ]
                },
                "result": "consistently_lower",
                "rule": (
                    "each_condition_session_median_below_"
                    "all_comparison_session_medians"
                ),
                "session_consistency": {
                    "supporting_sessions": supporting_sessions,
                    "total_sessions": len(
                        candidate_session_medians
                    ),
                },
                "evidence": {
                    "condition_session_medians":
                        candidate_session_medians,
                    "comparison_session_medians":
                        comparison_session_medians,
                },
            })

    return findings


# ============================================================
# 6. Fixed Structured Output
# ============================================================

def build_analysis_result(
    sessions: list[dict],
) -> dict:
    """
    生成 Analysis-to-Agent Bridge v0.1 的固定输出格式。

    Raw Data
    -> Deterministic Analysis
    -> Structured Evidence
    """
    profiles = build_sensitivity_profiles(sessions)

    sensitivities = [
        profile["sensitivity"]
        for profile in profiles
    ]

    total_attempts = sum(
        len(session["attempts"])
        for session in sessions
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": ANALYSIS_TYPE,

        "experiment": {
            "sensitivities": sensitivities,
            "session_count": len(sessions),
            "attempt_count": total_attempts,
        },

        "sensitivity_profiles": profiles,

        "findings": build_path_efficiency_findings(
            profiles
        ),

        "limitations": [
            {
                "code": "limited_sessions_per_condition",
                "message": (
                    "Only a small number of sessions were "
                    "collected per sensitivity."
                ),
            },
            {
                "code": "single_player_fixed_environment",
                "message": (
                    "Results describe one player under a "
                    "fixed device and browser environment."
                ),
            },
            {
                "code": "descriptive_analysis_only",
                "message": (
                    "The current analysis is descriptive and "
                    "does not include statistical significance "
                    "testing."
                ),
            },
        ],
    }


def save_analysis_result(
    result: dict,
    output_path: Path,
) -> None:
    """
    唯一正式输出：
    固定 schema 的 analysis_result.json。
    """
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# 7. Entry Point
# ============================================================

def main() -> None:
    sessions = load_sessions(DATA_DIR)

    validate_dataset(sessions)

    analysis_result = build_analysis_result(
        sessions
    )

    save_analysis_result(
        analysis_result,
        ANALYSIS_OUTPUT,
    )


if __name__ == "__main__":
    main()
