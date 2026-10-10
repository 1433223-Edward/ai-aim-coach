from analysis.tools import (
    compare_sensitivities,
    get_analysis_summary,
    get_sensitivity_profile,
)


def main():
    # ----------------------------------------
    # Tool 1
    # ----------------------------------------

    summary = get_analysis_summary()

    assert (
        summary["analysis_type"]
        == "sensitivity_analysis"
    )

    assert (
        summary["experiment"]["session_count"]
        == 9
    )


    # ----------------------------------------
    # Tool 2
    # ----------------------------------------

    profile = get_sensitivity_profile(1.2)

    assert profile["sensitivity"] == 1.2

    assert "metrics" in profile
    assert "session_consistency" in profile


    # ----------------------------------------
    # Tool 3
    # ----------------------------------------

    comparison = compare_sensitivities(
        [0.8, 1.0, 1.2]
    )

    assert len(comparison["profiles"]) == 3

    assert (
        comparison["sensitivities"]
        == [0.8, 1.0, 1.2]
    )


if __name__ == "__main__":
    main()