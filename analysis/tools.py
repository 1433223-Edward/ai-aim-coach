from analysis.analysis_runtime import AnalysisSnapshot
# ============================================================
# AI Aim Coach
# Analysis Tools v0.1
#
# 职责边界：
#
# Request-scoped analysis snapshot
#        ↓
#     Tools
#        ↓
# Future Agent
#
# Tools 只负责：
# - 查询
# - 筛选
# - 返回稳定结构
#
# Tools 不负责：
# - 读取 Raw Mouse Events
# - 重新计算统计指标
# - 推荐最佳 sensitivity
# - 做 Agent 决策
# ============================================================

def _get_current_analysis_result(snapshot: AnalysisSnapshot) -> dict:
    # Tools only read the snapshot supplied by their caller; never select latest.
    return snapshot.result()

# ============================================================
# Tool 1
# get_analysis_summary()
# ============================================================

def get_analysis_summary(*, snapshot: AnalysisSnapshot) -> dict:
    """Return scope, findings and limitations from the caller's fixed evidence."""
    result = _get_current_analysis_result(snapshot)

    return {
        "evidence": snapshot.metadata(),
        "analysis_type": result["analysis_type"],
        "schema_version": result["schema_version"],

        "experiment": result["experiment"],

        "findings": result["findings"],

        "limitations": result["limitations"],
    }


# ============================================================
# Tool 2
# get_sensitivity_profile()
# ============================================================

def get_sensitivity_profile(
    sensitivity: float,
    *, snapshot: AnalysisSnapshot,
) -> dict:
    """Return a tested condition's profile without making a recommendation."""
    result = _get_current_analysis_result(snapshot)

    profiles = result["sensitivity_profiles"]

    for profile in profiles:
        if profile["sensitivity"] == sensitivity:
            return {**profile, "evidence": snapshot.metadata()}

    available = [
        profile["sensitivity"]
        for profile in profiles
    ]

    raise ValueError(
        f"Sensitivity {sensitivity} was not found. "
        f"Available sensitivities: {available}"
    )


# ============================================================
# Tool 3
# compare_sensitivities()
# ============================================================

def compare_sensitivities(
    sensitivities: list[float],
    *, snapshot: AnalysisSnapshot,
) -> dict:
    """Select profiles and related findings from one snapshot; do not recompute."""

    if len(sensitivities) < 2:
        raise ValueError(
            "compare_sensitivities requires "
            "at least two sensitivities."
        )

    result = _get_current_analysis_result(snapshot)

    profiles_by_sensitivity = {
        profile["sensitivity"]: profile
        for profile in result["sensitivity_profiles"]
    }

    missing = [
        sensitivity
        for sensitivity in sensitivities
        if sensitivity not in profiles_by_sensitivity
    ]

    if missing:
        raise ValueError(
            f"Sensitivities not found: {missing}. "
            f"Available: "
            f"{list(profiles_by_sensitivity.keys())}"
        )

    # 保持调用者给出的顺序。
    selected_profiles = [
        profiles_by_sensitivity[sensitivity]
        for sensitivity in sensitivities
    ]

    requested = set(sensitivities)

    relevant_findings = []

    for finding in result["findings"]:

        condition_sensitivity = (
            finding
            .get("condition", {})
            .get("sensitivity")
        )

        comparison_sensitivities = set(
            finding
            .get("comparison", {})
            .get("sensitivities", [])
        )

        # Finding 的主要 condition 在当前比较范围内，
        # 并且至少涉及一个当前比较对象，
        # 才认为与本次 Tool Call 有关。
        if (
            condition_sensitivity in requested
            and comparison_sensitivities.intersection(
                requested
            )
        ):
            relevant_findings.append(finding)

    return {
        "evidence": snapshot.metadata(),
        "sensitivities": sensitivities,

        "profiles": selected_profiles,

        "relevant_findings": relevant_findings,

        # Agent 做判断时仍然需要知道证据边界。
        "limitations": result["limitations"],
    }
