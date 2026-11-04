from datetime import datetime
import math
import json


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


def _is_number(value: object) -> bool:
    # bool is an int subclass, but is not a valid coordinate/time/delta.
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_session_integrity(
    session: object,
    *,
    require_experiment_id: bool = True,
    allow_incomplete: bool = False,
) -> list[str]:
    """Validate reported fields, not the authenticity of client-side actions.

    Legacy JSON without experimentId may be inspected explicitly with
    require_experiment_id=False. Storage and analysis always use the default.
    No input is coerced, repaired, or mutated.
    allow_incomplete is reserved for the separate archive boundary. Analysis keeps
    the default and cannot consume partial observations as complete Sessions.
    """
    errors: list[str] = []
    if not isinstance(session, dict):
        return ["session must be an object."]
    if not allow_incomplete and session.get("status") not in (None, "complete"):
        errors.append("Only complete Sessions may enter the complete-session pipeline.")
    try:
        json.dumps(session, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return ["session must contain finite, JSON-compatible values."]

    def number(obj: dict, key: str, path: str, *, positive=False,
               nonnegative=False) -> bool:
        value = obj.get(key)
        if not _is_number(value):
            errors.append(f"{path}.{key} must be a finite number.")
            return False
        if (positive and value <= 0) or (nonnegative and value < 0):
            bound = "positive" if positive else "nonnegative"
            errors.append(f"{path}.{key} must be {bound}.")
            return False
        return True

    names = ["schemaVersion", "sessionId"]
    if require_experiment_id or "experimentId" in session:
        names.append("experimentId")
    for key in names:
        value = session.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"session.{key} must be a nonempty string.")

    count = session.get("targetCount")
    if type(count) is not int or count <= 0:
        errors.append("session.targetCount must be a positive integer.")
    for key in ("experimentOrder", "sensitivityRepeat"):
        value = session.get(key)
        if value is not None and (type(value) is not int or value <= 0):
            errors.append(f"session.{key} must be a positive integer or null.")
    number(session, "trainerSensitivity", "session", positive=True)
    for key in ("trainerWidth", "trainerHeight"):
        number(session, key, "session", positive=True)

    wall_times = []
    for key in ("startedAt", "endedAt"):
        value = session.get(key)
        try:
            if not isinstance(value, str) or "T" not in value:
                raise ValueError("Expected ISO datetime")
            wall_times.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except (ValueError, TypeError, OverflowError):
            errors.append(f"session.{key} must be an ISO datetime.")
    if len(wall_times) == 2:
        try:
            if wall_times[1] < wall_times[0]:
                errors.append("session.endedAt is earlier than startedAt.")
        except TypeError:
            errors.append("session.startedAt and endedAt must use comparable timezones.")

    valid_start = number(session, "startTime", "session", nonnegative=True)
    valid_end = number(session, "endTime", "session", nonnegative=True)
    start = session.get("startTime") if valid_start else None
    end = session.get("endTime") if valid_end else None
    if start is not None and end is not None and end < start:
        errors.append("session.endTime is earlier than startTime.")

    attempts = session.get("attempts")
    if not isinstance(attempts, list):
        return errors + ["session.attempts must be a list."]
    if not attempts and not allow_incomplete:
        errors.append("session.attempts must not be empty.")
    if not allow_incomplete and len(attempts) != count:
        errors.append("Attempt count does not match targetCount.")

    previous_end = start
    attempt_ids = set()
    for index, attempt in enumerate(attempts, start=1):
        path = f"Attempt {index}"
        if not isinstance(attempt, dict):
            errors.append(f"{path} must be an object.")
            continue
        # New clients identify attempts; legacy records without IDs remain readable.
        if "attemptId" in attempt:
            identity = attempt["attemptId"]
            if not isinstance(identity, str) or not identity.strip() or identity in attempt_ids:
                errors.append(f"{path}.attemptId must be a unique nonempty string.")
            else:
                attempt_ids.add(identity)
        target = attempt.get("target")
        if not isinstance(target, dict):
            errors.append(f"{path}.target must be an object.")
            continue
        target_valid = {
            key: number(target, key, f"{path}.target",
                        positive=(key == "radius"),
                        nonnegative=(key in ("appearTime", "disappearTime")))
            for key in ("x", "y", "radius", "appearTime", "disappearTime",
                        "cursorStartX", "cursorStartY")
        }
        appear = target["appearTime"] if target_valid["appearTime"] else None
        disappear = target["disappearTime"] if target_valid["disappearTime"] else None
        if appear is not None:
            if previous_end is not None and appear < previous_end:
                errors.append(f"{path}: appearTime precedes session/previous attempt end.")
            if end is not None and appear > end:
                errors.append(f"{path}: appearTime exceeds session.endTime.")
        if disappear is not None:
            if appear is not None and disappear < appear:
                errors.append(f"{path}: disappearTime is earlier than appearTime.")
            if end is not None and disappear > end:
                errors.append(f"{path}: disappearTime exceeds session.endTime.")
            previous_end = disappear

        valid_event_times = {"movements": [], "clicks": []}
        for kind, fields in (
            ("movements", ("time", "rawDX", "rawDY", "x", "y")),
            ("clicks", ("time", "x", "y")),
        ):
            events = attempt.get(kind)
            if not isinstance(events, list):
                errors.append(f"{path}.{kind} must be a list.")
                continue
            if kind == "clicks" and not events:
                errors.append(f"{path}: no click recorded.")
            previous_time = None
            for event_index, event in enumerate(events, start=1):
                event_path = f"{path}.{kind}[{event_index}]"
                if not isinstance(event, dict):
                    errors.append(f"{event_path} must be an object.")
                    continue
                valid = {key: number(event, key, event_path,
                                     nonnegative=(key == "time")) for key in fields}
                if valid["time"]:
                    time = event["time"]
                    valid_event_times[kind].append(time)
                    if previous_time is not None and time < previous_time:
                        errors.append(f"{event_path}: timestamps are not monotonic.")
                    if appear is not None and time < appear:
                        errors.append(f"{event_path}: time precedes appearTime.")
                    if disappear is not None and time > disappear:
                        errors.append(f"{event_path}: time exceeds disappearTime.")
                    previous_time = time
                if kind == "clicks":
                    hit = event.get("hit")
                    if type(hit) is not bool:
                        errors.append(f"{event_path}.hit must be a boolean.")
                    else:
                        if hit and event_index != len(events):
                            errors.append(f"{event_path}: HIT must end the attempt.")
                        if all(valid[k] for k in ("x", "y")) and all(
                            target_valid[k] for k in ("x", "y", "radius")
                        ):
                            distance = math.hypot(float(event["x"]) - float(target["x"]),
                                                  float(event["y"]) - float(target["y"]))
                            if not math.isfinite(distance):
                                errors.append(f"{event_path}: target distance must be finite.")
                            elif hit != (distance <= target["radius"]):
                                errors.append(f"{event_path}: hit disagrees with target geometry.")
            if kind == "clicks" and events:
                last = events[-1]
                if not isinstance(last, dict) or last.get("hit") is not True:
                    errors.append(f"{path}: final click is not HIT.")

        clicks = attempt.get("clicks")
        if isinstance(clicks, list) and clicks and isinstance(clicks[-1], dict):
            final_time = clicks[-1].get("time")
            if _is_number(final_time) and any(
                time > final_time for time in valid_event_times["movements"]
            ):
                errors.append(f"{path}: movement occurs after final click.")
    return errors


def require_valid_session(session: object) -> None:
    """Shared write/analysis guard; raises before any DB connection or metrics."""
    errors = validate_session_integrity(session)
    if errors:
        raise ValueError("Session integrity validation failed:\n- " + "\n- ".join(errors))
