from analysis.analysis_runtime import (
    analyze_latest_experiment,
)
# ============================================================
# AI Aim Coach
# Analysis Tools v0.1
#
# 职责边界：
#
# analysis_result.json
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

def _get_current_analysis_result() -> dict:
    return analyze_latest_experiment()

# ============================================================
# Tool 1
# get_analysis_summary()
# ============================================================

def get_analysis_summary() -> dict:
    """
    返回当前 sensitivity experiment 的总体状态。

    适合 Agent 第一次了解：
    - 测试过哪些 sensitivity
    - 有多少实验数据
    - 当前有哪些可靠 finding
    - 当前证据有哪些限制

    不返回完整 profile,
    避免一次塞入过多统计细节。
    """
    result = _get_current_analysis_result()

    return {
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
) -> dict:
    """
    返回某一个 sensitivity 的完整确定性分析结果。

    这里只回答：
    “这个 sensitivity 的实验表现是什么？”

    不回答：
    “这个 sensitivity 是否适合用户？”
    """
    result = _get_current_analysis_result()

    profiles = result["sensitivity_profiles"]

    for profile in profiles:
        if profile["sensitivity"] == sensitivity:
            return profile

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
) -> dict:
    """
    返回指定 sensitivity 的并列证据。

    注意：
    这里不重新做任何统计计算。

    只是从 analysis_result.json 中：
    1. 找到指定 profiles
    2. 找到和这些 sensitivity 有关的 findings
    3. 返回给未来 Agent
    """

    if len(sensitivities) < 2:
        raise ValueError(
            "compare_sensitivities requires "
            "at least two sensitivities."
        )

    result = _get_current_analysis_result()

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
        "sensitivities": sensitivities,

        "profiles": selected_profiles,

        "relevant_findings": relevant_findings,

        # Agent 做判断时仍然需要知道证据边界。
        "limitations": result["limitations"],
    }