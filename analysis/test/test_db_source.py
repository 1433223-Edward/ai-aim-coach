from backend.session_store import (
    get_session,
    get_sessions_by_experiment,
)


# ============================================================
# AI Aim Coach
# Database Session Source Verification
#
# 当前只验证：
#
# SQLite
#   ↓
# Raw Session Retrieval
#   ↓
# Future Analysis Pipeline
#
# 不计算任何 sensitivity metric。
# ============================================================


# 改成你刚才真实保存的 Session ID。
TEST_SESSION_ID = "86b1aa6f-d3c9-4ccf-babf-ab8e50dea354"

# 改成刚才测试数据真实使用的 Experiment ID。
TEST_EXPERIMENT_ID = "exp_001"


def verify_single_session() -> None:
    session = get_session(
        TEST_SESSION_ID
    )

    assert session is not None, (
        "Session was not found in SQLite."
    )

    assert (
        session["sessionId"]
        == TEST_SESSION_ID
    )

    assert (
        session["experimentId"]
        == TEST_EXPERIMENT_ID
    )

    assert (
        len(session["attempts"])
        == session["targetCount"]
    )

    print(
        "[PASS] Single session retrieval"
    )


def verify_experiment_query() -> None:
    sessions = (
        get_sessions_by_experiment(
            TEST_EXPERIMENT_ID
        )
    )

    assert len(sessions) > 0, (
        "No sessions found for experiment."
    )

    for session in sessions:
        assert (
            session["experimentId"]
            == TEST_EXPERIMENT_ID
        )

    print(
        "[PASS] Experiment isolation"
    )


def verify_raw_structure() -> None:
    session = get_session(
        TEST_SESSION_ID
    )

    attempts = session["attempts"]

    assert len(attempts) > 0

    for attempt in attempts:
        assert "target" in attempt
        assert "movements" in attempt
        assert "clicks" in attempt

        assert isinstance(
            attempt["movements"],
            list,
        )

        assert isinstance(
            attempt["clicks"],
            list,
        )

        assert (
            len(attempt["clicks"]) > 0
        )

        assert (
            attempt["clicks"][-1]["hit"]
            is True
        )

    print(
        "[PASS] Raw session structure"
    )


def main() -> None:
    verify_single_session()
    verify_experiment_query()
    verify_raw_structure()

    print()
    print(
        "Database Session Source: PASS"
    )


if __name__ == "__main__":
    main()