from datetime import datetime


# ============================================================
# AI Aim Coach
# Session Integrity Validator v0.1
#
# 职责：
#
# Raw Training Session
#        ↓
# Deterministic Validation
#        ↓
# errors[]
#
# 只判断数据是否完整、一致。
#
# 不负责：
# - 计算训练指标
# - 判断 sensitivity 表现
# - Agent 决策
# ============================================================


def _parse_iso_time(value: str) -> datetime:
    """
    JavaScript 的 toISOString() 会产生：
    2026-09-15T10:20:30.123Z

    Python fromisoformat 使用 +00:00 表示 UTC。
    """
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def validate_session_integrity(
    session: dict,
) -> list[str]:
    """
    返回发现的所有完整性错误。

    [] 表示 PASS。

    我们不只返回第一个错误，
    因为 Debug Raw Data 时一次看到所有问题更有价值。
    """

    errors: list[str] = []


    # ========================================================
    # Session-level Checks
    # ========================================================

    attempts = session.get("attempts")

    if not isinstance(attempts, list):
        return [
            "session.attempts must be a list."
        ]


    target_count = session.get("targetCount")

    if len(attempts) != target_count:
        errors.append(
            "Attempt count does not match targetCount: "
            f"{len(attempts)} != {target_count}"
        )


    if session.get("endedAt") is None:
        errors.append(
            "Completed session is missing endedAt."
        )


    # startedAt / endedAt 的现实时间顺序检查。
    try:
        started_at = _parse_iso_time(
            session["startedAt"]
        )

        ended_at = _parse_iso_time(
            session["endedAt"]
        )

        if ended_at < started_at:
            errors.append(
                "endedAt is earlier than startedAt."
            )

    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        errors.append(
            "Invalid startedAt or endedAt."
        )


    # performance.now() 的 Session 内部时间顺序。
    start_time = session.get("startTime")
    end_time = session.get("endTime")

    if (
        isinstance(start_time, (int, float))
        and isinstance(end_time, (int, float))
        and end_time < start_time
    ):
        errors.append(
            "endTime is earlier than startTime."
        )


    # ========================================================
    # Attempt-level Checks
    # ========================================================

    required_target_fields = {
        "x",
        "y",
        "radius",
        "appearTime",
        "disappearTime",
        "cursorStartX",
        "cursorStartY",
    }

    required_movement_fields = {
        "time",
        "rawDX",
        "rawDY",
        "x",
        "y",
    }

    required_click_fields = {
        "time",
        "x",
        "y",
        "hit",
    }


    for index, attempt in enumerate(
        attempts,
        start=1,
    ):
        prefix = f"Attempt {index}"


        # ----------------------------------------------------
        # Target
        # ----------------------------------------------------

        target = attempt.get("target")

        if not isinstance(target, dict):
            errors.append(
                f"{prefix}: target is missing."
            )
            continue


        missing_target_fields = (
            required_target_fields
            - target.keys()
        )

        if missing_target_fields:
            errors.append(
                f"{prefix}: target missing fields "
                f"{sorted(missing_target_fields)}."
            )


        appear_time = target.get(
            "appearTime"
        )

        disappear_time = target.get(
            "disappearTime"
        )

        if (
            isinstance(appear_time, (int, float))
            and isinstance(
                disappear_time,
                (int, float),
            )
            and disappear_time < appear_time
        ):
            errors.append(
                f"{prefix}: disappearTime "
                "is earlier than appearTime."
            )


        # ----------------------------------------------------
        # Movements
        #
        # movements=[] 本身不一定非法。
        # 极端情况下 Target 可能直接出现在准星附近，
        # 用户无需移动就 HIT。
        # ----------------------------------------------------

        movements = attempt.get(
            "movements"
        )

        if not isinstance(movements, list):
            errors.append(
                f"{prefix}: movements must be a list."
            )
        else:
            previous_time = None

            for movement_index, movement in enumerate(
                movements,
                start=1,
            ):
                missing = (
                    required_movement_fields
                    - movement.keys()
                )

                if missing:
                    errors.append(
                        f"{prefix} movement "
                        f"{movement_index}: "
                        f"missing fields "
                        f"{sorted(missing)}."
                    )


                movement_time = movement.get(
                    "time"
                )

                if (
                    previous_time is not None
                    and movement_time is not None
                    and movement_time
                    < previous_time
                ):
                    errors.append(
                        f"{prefix}: movement "
                        "timestamps are not monotonic."
                    )

                if movement_time is not None:
                    previous_time = (
                        movement_time
                    )


        # ----------------------------------------------------
        # Clicks
        # ----------------------------------------------------

        clicks = attempt.get("clicks")

        if not isinstance(clicks, list):
            errors.append(
                f"{prefix}: clicks must be a list."
            )

            continue


        if len(clicks) == 0:
            errors.append(
                f"{prefix}: no click recorded."
            )

            continue


        previous_click_time = None

        for click_index, click in enumerate(
            clicks,
            start=1,
        ):
            missing = (
                required_click_fields
                - click.keys()
            )

            if missing:
                errors.append(
                    f"{prefix} click "
                    f"{click_index}: "
                    f"missing fields "
                    f"{sorted(missing)}."
                )


            click_time = click.get("time")

            if (
                previous_click_time
                is not None
                and click_time is not None
                and click_time
                < previous_click_time
            ):
                errors.append(
                    f"{prefix}: click timestamps "
                    "are not monotonic."
                )

            if click_time is not None:
                previous_click_time = (
                    click_time
                )


        # Attempt 的结束条件就是第一次 HIT，
        # 所以最后一次 Click 必须是 HIT。
        if clicks[-1].get("hit") is not True:
            errors.append(
                f"{prefix}: final click is not HIT."
            )


    return errors