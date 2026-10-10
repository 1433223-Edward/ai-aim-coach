import math
from collections import defaultdict
from statistics import mean, median, stdev


# ============================================================
# AI Aim Coach
# Analysis Service v0.1
#
# 职责：
#
# sessions: list[dict]
#        ↓
# Deterministic Analysis
#        ↓
# Structured Analysis Result
#
# 本模块不知道 Session 来自：
# - SQLite
# - JSON
# - Test Fixture
#
# 也不负责：
# - 数据持久化
# - Agent Decision
# - 推荐最佳 sensitivity
# ============================================================


SCHEMA_VERSION = "0.2"
ANALYSIS_TYPE = "sensitivity_analysis"


METRIC_NAMES = (
    "final_hit_time_ms",
    "miss_count",
    "first_click_error_px",
    "path_efficiency",
)


# ============================================================
# 1. Analysis Dataset Validation
# ============================================================

def validate_analysis_dataset(
    sessions: list[dict],
) -> dict:
    """
    验证这一批 Sessions 是否可以作为同一个
    Experiment Dataset 进入分析。

    注意：
    Session 自身 Raw Data 是否完整，
    主要由 Session Validator 负责。

    这里关注的是：
    “这些 Session 能不能放在一起分析？”
    """

    if not sessions:
        raise ValueError(
            "Analysis dataset is empty."
        )


    required_fields = {
        "experimentId",
        "sessionId",
        "trainerSensitivity",
        "targetCount",
        "attempts",
    }

    problems = []

    for index, session in enumerate(
        sessions,
        start=1,
    ):
        missing_fields = (
            required_fields
            - session.keys()
        )

        if missing_fields:
            problems.append(
                f"Session {index}: "
                f"missing fields "
                f"{sorted(missing_fields)}"
            )

            continue


        expected_count = (
            session["targetCount"]
        )

        actual_count = len(
            session["attempts"]
        )

        if actual_count != expected_count:
            problems.append(
                f"Session {index}: "
                f"expected {expected_count} attempts, "
                f"got {actual_count}"
            )


    if problems:
        raise ValueError(
            "Analysis dataset validation failed:\n- "
            + "\n- ".join(problems)
        )


    # --------------------------------------------------------
    # 当前 v0.1 只允许分析同一个 Experiment。
    # --------------------------------------------------------

    experiment_ids = {
        session["experimentId"]
        for session in sessions
    }

    if len(experiment_ids) != 1:
        raise ValueError(
            "Analysis dataset must contain "
            "exactly one experimentId. "
            f"Found: {sorted(experiment_ids)}"
        )


    experiment_id = next(
        iter(experiment_ids)
    )


    sensitivities = sorted({
        session["trainerSensitivity"]
        for session in sessions
    })


    total_attempts = sum(
        len(session["attempts"])
        for session in sessions
    )


    return {
        "experiment_id": experiment_id,
        "sensitivities": sensitivities,
        "session_count": len(sessions),
        "attempt_count": total_attempts,
    }


# ============================================================
# 2. Attempt Metrics
#
# Raw Attempt
#      ↓
# Deterministic Metrics
# ============================================================

def calculate_path_length(
    attempt: dict,
) -> float:
    """
    实际路径：

    cursorStart
        ↓
    movement[0]
        ↓
    movement[1]
        ↓
    ...
        ↓
    final click
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


def calculate_attempt_metrics(
    attempt: dict,
) -> dict:
    """
    一个 Raw Attempt
    → Sensitivity Analysis v0.1 的四个指标。

    这里不产生解释性判断。
    """

    target = attempt["target"]
    clicks = attempt["clicks"]


    if not clicks:
        raise ValueError(
            "Attempt contains no click data."
        )


    first_click = clicks[0]
    final_click = clicks[-1]


    if not final_click["hit"]:
        raise ValueError(
            "Attempt does not end with a HIT."
        )


    # --------------------------------------------------------
    # Final Hit Time
    # --------------------------------------------------------

    final_hit_time_ms = (
        final_click["time"]
        - target["appearTime"]
    )


    # --------------------------------------------------------
    # Miss Count
    # --------------------------------------------------------

    miss_count = sum(
        1
        for click in clicks
        if not click["hit"]
    )


    # --------------------------------------------------------
    # First-click Error
    # --------------------------------------------------------

    first_click_error_px = math.hypot(
        first_click["x"] - target["x"],
        first_click["y"] - target["y"],
    )


    # --------------------------------------------------------
    # Path Efficiency
    #
    # direct distance:
    # cursor start → final HIT position
    #
    # 而不是 cursor start → target center。
    # --------------------------------------------------------

    start_x = target["cursorStartX"]
    start_y = target["cursorStartY"]


    direct_distance = math.hypot(
        final_click["x"] - start_x,
        final_click["y"] - start_y,
    )


    path_length = calculate_path_length(
        attempt
    )


    if path_length > 0:
        path_efficiency = (
            direct_distance
            / path_length
        )

    else:
        path_efficiency = (
            1.0
            if direct_distance == 0
            else 0.0
        )


    return {
        "final_hit_time_ms":
            final_hit_time_ms,

        "miss_count":
            miss_count,

        "first_click_error_px":
            first_click_error_px,

        "path_efficiency":
            path_efficiency,
    }


# ============================================================
# 3. Statistical Aggregation
# ============================================================

def calculate_statistics(
    values: list[float],
) -> dict:
    """
    普通 Metric 固定统计结构：

    sample_count
    mean
    median
    std
    """

    if not values:
        raise ValueError(
            "Cannot calculate statistics "
            "for empty values."
        )


    return {
        "sample_count":
            len(values),

        "mean":
            mean(values),

        "median":
            median(values),

        "std":
            (
                stdev(values)
                if len(values) >= 2
                else 0.0
            ),
    }


def calculate_miss_attempt_rate(
    metrics_list: list[dict],
) -> dict:
    """
    Miss Attempt Rate：

    至少出现一次 MISS 的 Attempt 数
    /
    总 Attempt 数
    """

    total_attempts = len(
        metrics_list
    )


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
        "miss_attempt_count":
            miss_attempts,

        "attempt_count":
            total_attempts,

        "miss_attempt_rate":
            rate,
    }


def summarize_metrics(
    metrics_list: list[dict],
) -> dict:
    """
    一批 Attempt Metrics
    → 固定统计 Summary。
    """

    if not metrics_list:
        raise ValueError(
            "metrics_list is empty."
        )


    summary = {}


    for metric_name in METRIC_NAMES:
        values = [
            metrics[metric_name]
            for metrics in metrics_list
        ]

        summary[metric_name] = (
            calculate_statistics(values)
        )


    return summary


# ============================================================
# 4. Session / Sensitivity Aggregation
# ============================================================

def group_sessions_by_sensitivity(
    sessions: list[dict],
) -> dict[float, list[dict]]:
    """
    保留 Session 边界，
    按 trainerSensitivity 分组。
    """

    grouped = defaultdict(list)


    for session in sessions:
        grouped[
            session["trainerSensitivity"]
        ].append(session)


    return dict(grouped)


def calculate_session_summary(
    session: dict,
) -> dict:
    """
    Raw Session
    → Session-level Summary。

    保留：
    experimentOrder
    sensitivityRepeat

    方便后续检查 Session-level consistency。
    """

    attempt_metrics = [
        calculate_attempt_metrics(
            attempt
        )
        for attempt in session["attempts"]
    ]


    return {
        "session_id":
            session["sessionId"],

        "sensitivity":
            session["trainerSensitivity"],

        "experiment_order":
            session.get(
                "experimentOrder"
            ),

        "sensitivity_repeat":
            session.get(
                "sensitivityRepeat"
            ),

        "attempt_count":
            len(attempt_metrics),

        "metrics":
            summarize_metrics(
                attempt_metrics
            ),

        "miss_behavior":
            calculate_miss_attempt_rate(
                attempt_metrics
            ),
    }


def build_sensitivity_profile(
    sensitivity: float,
    sessions: list[dict],
) -> dict:
    """
    同一 sensitivity 下的多个 Session
    → Sensitivity Profile。
    """

    session_summaries = [
        calculate_session_summary(
            session
        )
        for session in sessions
    ]


    all_attempt_metrics = []


    for session in sessions:
        all_attempt_metrics.extend(
            calculate_attempt_metrics(
                attempt
            )
            for attempt
            in session["attempts"]
        )


    return {
        "sensitivity":
            sensitivity,

        "session_count":
            len(session_summaries),

        "attempt_count":
            len(all_attempt_metrics),

        "metrics":
            summarize_metrics(
                all_attempt_metrics
            ),

        "miss_behavior":
            calculate_miss_attempt_rate(
                all_attempt_metrics
            ),

        "session_consistency": {
            "sessions":
                session_summaries
        },
    }


def build_sensitivity_profiles(
    sessions: list[dict],
) -> list[dict]:
    """
    生成 Experiment 中所有 Sensitivity Profiles。
    """

    grouped_sessions = (
        group_sessions_by_sensitivity(
            sessions
        )
    )


    return [
        build_sensitivity_profile(
            sensitivity=sensitivity,

            sessions=(
                grouped_sessions[
                    sensitivity
                ]
            ),
        )

        for sensitivity
        in sorted(grouped_sessions)
    ]


# ============================================================
# 5. Deterministic Findings
# ============================================================

def build_path_efficiency_findings(
    profiles: list[dict],
) -> list[dict]:
    """
    当前继续保留旧版唯一 Finding：

    如果某一个 sensitivity 的每一个 Session
    Path Efficiency Median

    都低于其他 sensitivity 的所有
    Session Median，

    才输出 consistently_lower。
    """

    findings = []


    for candidate in profiles:

        candidate_sensitivity = (
            candidate["sensitivity"]
        )


        candidate_session_medians = [
            session[
                "metrics"
            ][
                "path_efficiency"
            ][
                "median"
            ]

            for session
            in candidate[
                "session_consistency"
            ][
                "sessions"
            ]
        ]


        comparison_profiles = [
            profile
            for profile in profiles
            if (
                profile["sensitivity"]
                != candidate_sensitivity
            )
        ]


        comparison_session_medians = [
            session[
                "metrics"
            ][
                "path_efficiency"
            ][
                "median"
            ]

            for profile
            in comparison_profiles

            for session
            in profile[
                "session_consistency"
            ][
                "sessions"
            ]
        ]


        # 只有一个 sensitivity 时，
        # 没有 Comparison，自然不能产生 Finding。
        if (
            not candidate_session_medians
            or not comparison_session_medians
        ):
            continue


        comparison_min = min(
            comparison_session_medians
        )


        supporting_sessions = sum(
            1
            for value
            in candidate_session_medians
            if value < comparison_min
        )


        if (
            supporting_sessions
            == len(candidate_session_medians)
        ):
            findings.append({
                "finding_type":
                    "cross_session_comparison",

                "metric":
                    "path_efficiency",

                "condition": {
                    "sensitivity":
                        candidate_sensitivity
                },

                "comparison": {
                    "sensitivities": [
                        profile["sensitivity"]

                        for profile
                        in comparison_profiles
                    ]
                },

                "result":
                    "consistently_lower",

                "rule": (
                    "each_condition_session_median_"
                    "below_all_comparison_"
                    "session_medians"
                ),

                "session_consistency": {
                    "supporting_sessions":
                        supporting_sessions,

                    "total_sessions":
                        len(
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
# 6. Structured Result
# ============================================================

def build_limitations(
    profiles: list[dict],
) -> list[dict]:
    """
    当前先继承旧版 Analysis v0.1 的限制描述。

    后续如果 Experiment Protocol 真正发生变化，
    再让 limitation 生成逻辑更加动态。
    """

    return [
        {
            "code":
                "limited_sessions_per_condition",

            "message": (
                "Only a small number of sessions "
                "were collected per sensitivity."
            ),
        },

        {
            "code":
                "single_player_fixed_environment",

            "message": (
                "Results describe one player "
                "under a fixed device and "
                "browser environment."
            ),
        },

        {
            "code":
                "descriptive_analysis_only",

            "message": (
                "The current analysis is "
                "descriptive and does not "
                "include statistical significance "
                "testing."
            ),
        },
    ]


def analyze_sessions(
    sessions: list[dict],
) -> dict:
    """
    Analysis Service 唯一公共入口。

    sessions
        ↓
    Validation
        ↓
    Metrics
        ↓
    Profiles
        ↓
    Findings
        ↓
    Structured Analysis Result
    """

    dataset = (
        validate_analysis_dataset(
            sessions
        )
    )


    profiles = (
        build_sensitivity_profiles(
            sessions
        )
    )


    findings = (
        build_path_efficiency_findings(
            profiles
        )
    )


    return {
        "schema_version":
            SCHEMA_VERSION,

        "analysis_type":
            ANALYSIS_TYPE,

        "experiment": {
            "experiment_id":
                dataset["experiment_id"],

            "sensitivities":
                dataset["sensitivities"],

            "session_count":
                dataset["session_count"],

            "attempt_count":
                dataset["attempt_count"],
        },

        "sensitivity_profiles":
            profiles,

        "findings":
            findings,

        "limitations":
            build_limitations(
                profiles
            ),
    }