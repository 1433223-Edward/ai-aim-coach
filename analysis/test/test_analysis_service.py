from backend.session_store import (
    get_sessions_by_experiment,
)

from analysis.analysis_service import (
    analyze_sessions,
)


# ============================================================
# AI Aim Coach
# Analysis Service Runtime Verification
#
# 验证链路：
#
# SQLite
#   ↓
# Sessions
#   ↓
# Analysis Service
#   ↓
# Structured Analysis Result
#
# 当前不验证：
# - Agent
# - Tool Calling
# - 多 Experiment
# - 统计结论是否有意义
# ============================================================


# 改成你刚才测试 Session 使用的 experimentId。
TEST_EXPERIMENT_ID = "exp_001"


def main() -> None:

    # --------------------------------------------------------
    # 1. 从 SQLite 读取 Experiment Sessions
    # --------------------------------------------------------

    sessions = get_sessions_by_experiment(
        TEST_EXPERIMENT_ID
    )

    assert sessions, (
        "No sessions found for experiment."
    )

    print(
        f"[PASS] Loaded {len(sessions)} session(s) "
        f"from {TEST_EXPERIMENT_ID}"
    )


    # --------------------------------------------------------
    # 2. 交给纯 Analysis Service
    # --------------------------------------------------------

    result = analyze_sessions(
        sessions
    )

    print(
        "[PASS] analyze_sessions() completed"
    )


    # ========================================================
    # 3. 验证 Result 顶层结构
    # ========================================================

    assert (
        result["analysis_type"]
        == "sensitivity_analysis"
    )

    assert "schema_version" in result

    assert "experiment" in result

    assert "sensitivity_profiles" in result

    assert "findings" in result

    assert "limitations" in result

    print(
        "[PASS] Result top-level schema"
    )


    # ========================================================
    # 4. 验证 Experiment Metadata
    # ========================================================

    experiment = result["experiment"]

    assert (
        experiment["experiment_id"]
        == TEST_EXPERIMENT_ID
    )

    assert (
        experiment["session_count"]
        == len(sessions)
    )

    expected_attempt_count = sum(
        len(session["attempts"])
        for session in sessions
    )

    assert (
        experiment["attempt_count"]
        == expected_attempt_count
    )

    print(
        "[PASS] Experiment metadata"
    )


    # ========================================================
    # 5. 验证 Sensitivity Profile
    #
    # 当前测试数据只有一个 sensitivity，
    # 所以正常情况下应该只有一个 Profile。
    # ========================================================

    profiles = result[
        "sensitivity_profiles"
    ]

    assert len(profiles) == 1, (
        "Expected one sensitivity profile "
        "for current smoke-test experiment."
    )

    profile = profiles[0]

    expected_sensitivity = (
        sessions[0]["trainerSensitivity"]
    )

    assert (
        profile["sensitivity"]
        == expected_sensitivity
    )

    assert (
        profile["session_count"]
        == len(sessions)
    )

    assert (
        profile["attempt_count"]
        == expected_attempt_count
    )

    print(
        "[PASS] Sensitivity profile"
    )


    # ========================================================
    # 6. 验证四个已有指标
    # ========================================================

    metrics = profile["metrics"]

    expected_metric_names = {
        "final_hit_time_ms",
        "miss_count",
        "first_click_error_px",
        "path_efficiency",
    }

    assert (
        set(metrics.keys())
        == expected_metric_names
    )


    for metric_name in expected_metric_names:

        summary = metrics[
            metric_name
        ]

        assert (
            summary["sample_count"]
            == expected_attempt_count
        )

        assert "mean" in summary
        assert "median" in summary
        assert "std" in summary


    print(
        "[PASS] Metric summaries"
    )


    # ========================================================
    # 7. 基本数值合理性
    #
    # 这里只检查“不可能值”，
    # 不判断哪一个 sensitivity 更好。
    # ========================================================

    assert (
        metrics[
            "final_hit_time_ms"
        ]["mean"] >= 0
    )

    assert (
        metrics[
            "miss_count"
        ]["mean"] >= 0
    )

    assert (
        metrics[
            "first_click_error_px"
        ]["mean"] >= 0
    )

    path_efficiency = (
        metrics[
            "path_efficiency"
        ]["mean"]
    )

    assert (
        0 <= path_efficiency <= 1.000001
    )

    print(
        "[PASS] Metric value sanity"
    )


    # ========================================================
    # 8. Miss Behavior
    # ========================================================

    miss_behavior = profile[
        "miss_behavior"
    ]

    assert (
        miss_behavior["attempt_count"]
        == expected_attempt_count
    )

    assert (
        0
        <= miss_behavior[
            "miss_attempt_rate"
        ]
        <= 1
    )

    print(
        "[PASS] Miss behavior"
    )


    # ========================================================
    # 9. Session-level Consistency
    # ========================================================

    session_summaries = (
        profile[
            "session_consistency"
        ][
            "sessions"
        ]
    )

    assert (
        len(session_summaries)
        == len(sessions)
    )

    print(
        "[PASS] Session-level summaries"
    )


    # ========================================================
    # 10. Findings
    #
    # 当前只有一个 sensitivity，
    # 没有 Comparison Condition。
    #
    # 所以 findings=[] 是正确结果，
    # 不是分析失败。
    # ========================================================

    assert result["findings"] == []

    print(
        "[PASS] No false comparison findings"
    )


    print()
    print(
        "Database-backed Analysis Service: PASS"
    )


import unittest


class AnalysisFixtureTests(unittest.TestCase):
    def test_isolated_fixture(self):
        from analysis.legacy_adapter import load_legacy_fixture
        sessions, _ = load_legacy_fixture()
        result = analyze_sessions(sessions)
        self.assertEqual(result["experiment"]["session_count"], 9)
        self.assertEqual(result["experiment"]["attempt_count"], 180)
        self.assertEqual(len(result["sensitivity_profiles"]), 3)


if __name__ == "__main__":
    unittest.main()
