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


import unittest


class ToolsFixtureTests(unittest.TestCase):
    def test_isolated_fixture(self):
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        from backend import session_store as store
        from scripts.seed_demo import seed_demo
        from analysis.analysis_runtime import create_analysis_snapshot
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tools.db"
            info = seed_demo(destination)
            with patch.object(store, "DB_PATH", destination):
                snapshot = create_analysis_snapshot(info["experiment_id"])
                self.assertEqual(get_analysis_summary(snapshot=snapshot)["experiment"]["session_count"], 9)
                self.assertEqual(get_sensitivity_profile(1.2, snapshot=snapshot)["sensitivity"], 1.2)
                result = compare_sensitivities([0.8, 1.0, 1.2], snapshot=snapshot)
                self.assertEqual(len(result["profiles"]), 3)
                self.assertEqual(result["evidence"], snapshot.metadata())


if __name__ == "__main__":
    unittest.main()
