import json
import sqlite3
import random
import os
import math
from uuid import uuid4
from contextlib import closing, nullcontext
from pathlib import Path

from analysis.session_validator import require_valid_session


# ============================================================
# AI Aim Coach
# SQLite Session Store v0.1
#
# 职责：
#
# Training Session
#      ↓
# SQLite Persistence
#
# 当前数据库只负责“保存事实”。
#
# 不负责：
# - 计算训练指标
# - Sensitivity Analysis
# - Agent Decision
#
# Raw Session 仍然整体保存，
# 避免现在过早拆分 attempts / movements / clicks。
# ============================================================


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Tests and demos can select a separate database without touching training history.
DB_PATH = Path(os.getenv("AIMCOACH_DB_PATH", str(PROJECT_ROOT / "data" / "aimcoach.db")))


def storage_ready() -> bool:
    # Read-only probing must not create a missing database or change training history.
    try:
        with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("SELECT session_id FROM sessions LIMIT 1").fetchall()
            connection.execute("SELECT experiment_id FROM experiments LIMIT 1").fetchall()
        return True
    except sqlite3.Error:
        return False


def _connect() -> sqlite3.Connection:
    """
    每次数据库操作创建一个短生命周期 Connection。

    当前是本地单用户应用，
    暂时不需要连接池。
    """

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(DB_PATH)

    connection.row_factory = sqlite3.Row

    return connection


def init_database() -> None:
    """
    初始化数据库。

    如果 sessions 表已经存在，
    CREATE TABLE IF NOT EXISTS 不会重复创建。
    """

    with closing(_connect()) as connection, connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,

                schema_version TEXT NOT NULL,

                started_at TEXT NOT NULL,
                ended_at TEXT,

                trainer_sensitivity REAL NOT NULL,
                target_count INTEGER NOT NULL,

                experiment_order INTEGER,
                sensitivity_repeat INTEGER,

                raw_data TEXT NOT NULL,

                saved_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute("""CREATE TABLE IF NOT EXISTS experiment_endings (
            experiment_id TEXT PRIMARY KEY, ended_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS scenario_sessions (
            session_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, experiment_order INTEGER NOT NULL,
            scenario_id TEXT NOT NULL, status TEXT NOT NULL, raw_data TEXT NOT NULL, result_json TEXT NOT NULL,
            saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        # Plans retain the pre-recommendation evidence without rewriting any training row.
        connection.execute("""CREATE TABLE IF NOT EXISTS training_plans (
            plan_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
            payload TEXT NOT NULL, experiment_id TEXT UNIQUE, evaluation_json TEXT)""")
        # Additive archive: interrupted reports never enter complete-session metrics or progress.
        connection.execute("""CREATE TABLE IF NOT EXISTS incomplete_sessions (
            session_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, started_at TEXT NOT NULL,
            raw_data TEXT NOT NULL, saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

        # Add managed experiments without rewriting or regrouping legacy sessions.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL,
                schedule TEXT NOT NULL,
                active_slot INTEGER UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


class ExperimentConflict(ValueError):
    """The submitted condition is stale or belongs to a different protocol."""


class ExperimentNotFound(LookupError):
    """The requested experiment has no stored identity or legacy sessions."""


def _experiment_state(connection, row) -> dict:
    try:
        protocol = json.loads(row["protocol"])
        schedule = json.loads(row["schedule"])
        # Stored state is also an input boundary: never restore unsupported or broken schedules.
        if (not isinstance(protocol, dict) or protocol.get("version") != "1"
                or protocol.get("session_schema_version") != "0.1"
                or not isinstance(schedule, list) or not schedule
                or any(type(protocol.get(key)) not in (int, float) or not math.isfinite(protocol[key]) or protocol[key] <= 0
                       for key in ("target_count", "target_radius", "trainer_width", "trainer_height"))
                or type(protocol["target_count"]) is not int
                or any(not isinstance(c, dict) or type(c.get("sensitivity")) not in (int, float)
                       or not math.isfinite(c["sensitivity"]) or c["sensitivity"] <= 0
                       or type(c.get("repeat")) is not int or c["repeat"] <= 0 for c in schedule)
                or len({(c["sensitivity"], c["repeat"]) for c in schedule}) != len(schedule)):
            raise ValueError()
        if "settings" in protocol:
            # Validate stored snapshots too; never reinterpret corrupt settings as UI defaults.
            from backend.settings import ExperimentSettings
            ExperimentSettings(**protocol["settings"])
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ExperimentConflict("Stored experiment protocol or schedule is invalid.") from None
    # Progress comes only from committed sessions, never from browser counters.
    saved_rows = connection.execute(
        "SELECT session_id, experiment_order FROM sessions WHERE experiment_id=? UNION ALL "
        "SELECT session_id,experiment_order FROM scenario_sessions WHERE experiment_id=? AND status='complete' ORDER BY experiment_order",
        (row["experiment_id"],row["experiment_id"]),
    ).fetchall()
    completed = len(saved_rows)
    # Do not disguise manually corrupted or noncontiguous progress as a valid resume.
    if [r["experiment_order"] for r in saved_rows] != list(range(1, completed + 1)) or completed > len(schedule):
        raise ExperimentConflict("Experiment progress is inconsistent with its schedule.")
    ended = connection.execute("SELECT 1 FROM experiment_endings WHERE experiment_id=?",(row["experiment_id"],)).fetchone() is not None
    return {
        "ended": ended,
        "experiment_id": row["experiment_id"],
        "protocol": protocol,
        "schedule": schedule,
        "completed_session_ids": [r["session_id"] for r in saved_rows],
        "completed_count": completed,
        "status": "complete" if completed == len(schedule) else "incomplete",
        "next_condition": schedule[completed] if completed < len(schedule) and not ended else None,
    }


def get_or_create_experiment(trainer_width: int, trainer_height: int, settings: dict | None = None, scenario: dict | None = None, *, _connection=None) -> dict:
    """Resume the active experiment, or create one after an explicit new start."""
    # Direct callers must follow the same viewport contract as the HTTP endpoint.
    if any(type(value) is not int or value <= 50 for value in (trainer_width, trainer_height)):
        raise ValueError("Trainer dimensions must be integers greater than 50.")
    from backend.settings import ExperimentSettings
    # Validate direct callers before opening storage; only new experiments consume preferences.
    requested = ExperimentSettings(**settings).model_dump() if settings is not None else None
    # Plan acceptance supplies its transaction so Experiment creation and association commit together.
    scope = closing(_connect()) if _connection is None else nullcontext(_connection)
    with scope as connection, (connection if _connection is None else nullcontext()):
        # Serialize creation across tabs so there can be only one active experiment.
        if _connection is None:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM experiments WHERE active_slot=1"
        ).fetchone()
        if row is None:
            # Freeze the collection protocol and randomized order once per experiment.
            protocol = {
                "version": "1", "session_schema_version": "0.1",
                "target_count": 3, "target_radius": 25,
                "sensitivities": [0.8, 1.0, 1.2], "sessions_per_sensitivity": 3,
                "trainer_width": trainer_width, "trainer_height": trainer_height,
                "trainer_size_policy": "fixed_per_experiment",
                "input_mode_policy": "prefer_unadjusted_with_fallback",
            }
            schedule = [
                {"sensitivity": sensitivity, "repeat": repeat}
                for sensitivity in protocol["sensitivities"]
                for repeat in range(1, protocol["sessions_per_sensitivity"] + 1)
            ]
            if requested is not None:
                # Freeze settings in the existing protocol JSON; never update an active experiment.
                protocol["settings_contract_version"] = 1
                protocol["settings"] = requested
                protocol["target_count"] = requested["attempts"]
                protocol["sensitivities"] = [requested["sensitivity"]]
                protocol["sessions_per_sensitivity"] = requested["sessions"]
                schedule = [{"sensitivity": requested["sensitivity"], "repeat": repeat}
                            for repeat in range(1, requested["sessions"] + 1)]
            if scenario is not None:
                from analysis.scenarios import snapshot
                protocol["scenario"] = snapshot(scenario)
            random.SystemRandom().shuffle(schedule)
            experiment_id = str(uuid4())
            connection.execute(
                "INSERT INTO experiments (experiment_id, protocol, schedule, active_slot) "
                "VALUES (?, ?, ?, 1)",
                (experiment_id, json.dumps(protocol), json.dumps(schedule)),
            )
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
        return _experiment_state(connection, row)


def get_experiment(experiment_id: str | None = None) -> dict | None:
    with closing(_connect()) as connection, connection:
        # Read protocol and progress in one transaction, including concurrent uploads.
        connection.execute("BEGIN")
        if experiment_id is None:
            row = connection.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
        if row is not None:
            return _experiment_state(connection, row)
        return None


def require_matching_experiment(session: dict, state: dict) -> None:
    # Reject stale tabs and protocol mismatches before inserting any raw data.
    condition = state["next_condition"]
    if condition is None or session.get("experimentOrder") != state["completed_count"] + 1:
        raise ExperimentConflict("Experiment condition has already changed. Reload progress.")
    protocol = state["protocol"]
    if protocol.get("scenario"):
        raise ExperimentConflict("Scenario evidence must use the scenario Session endpoint.")
    if protocol.get("settings") is not None:
        from backend.settings import ExperimentSettings
        snapshot = session.get("settingsSnapshot")
        # Earlier Phase 1 protocols had no snapshot requirement. Preserve their queued reports.
        if snapshot is not None or protocol.get("settings_contract_version") == 1:
            try:
                normalized = ExperimentSettings(**snapshot).model_dump()
                expected_settings = ExperimentSettings(**protocol["settings"]).model_dump()
            except (ValueError, TypeError):
                raise ExperimentConflict("Invalid settings snapshot.") from None
            if normalized != expected_settings:
                raise ExperimentConflict("Session settings snapshot does not match the frozen experiment.")
    if session.get("scene", "flick_2d") != "flick_2d":
        raise ExperimentConflict("Unsupported scene.")
    expected = {
        "protocolVersion": protocol["version"],
        "schemaVersion": protocol["session_schema_version"],
        "targetCount": protocol["target_count"],
        "trainerWidth": protocol["trainer_width"],
        "trainerHeight": protocol["trainer_height"],
        "trainerSensitivity": condition["sensitivity"],
        "sensitivityRepeat": condition["repeat"],
    }
    for key, value in expected.items():
        if session.get(key) != value:
            raise ExperimentConflict(f"Session {key} does not match the experiment protocol.")
    if session.get("inputMode") not in ("unadjusted_requested", "adjusted_fallback"):
        raise ExperimentConflict("Session inputMode is missing or unsupported.")
    if any(a["target"]["radius"] != protocol["target_radius"] for a in session["attempts"]):
        raise ExperimentConflict("Target radius does not match the experiment protocol.")


def load_experiment_evidence(experiment_id: str) -> tuple[dict, list[dict]]:
    """Read protocol, progress and raw sessions from one SQLite read transaction."""
    with closing(_connect()) as connection, connection:
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)
        ).fetchone()
        if row is None:
            # Legacy rows stay untouched, but cannot silently become protocol-verified evidence.
            if connection.execute(
                "SELECT 1 FROM sessions WHERE experiment_id=? LIMIT 1", (experiment_id,)
            ).fetchone():
                raise ExperimentConflict("Legacy experiment has no verified protocol. Start a new experiment.")
            raise ExperimentNotFound("Experiment not found.")
        state = _experiment_state(connection, row)
        rows = connection.execute(
            "SELECT session_id, raw_data FROM sessions WHERE experiment_id=? ORDER BY experiment_order",
            (experiment_id,),
        ).fetchall()
        try:
            sessions = [json.loads(r["raw_data"]) for r in rows]
        except (ValueError, TypeError):
            raise ExperimentConflict("Stored session JSON is invalid.") from None
        for row, session in zip(rows, sessions):
            if not isinstance(session, dict) or session.get("sessionId") != row["session_id"] or session.get("experimentId") != experiment_id:
                raise ExperimentConflict("Stored session identity does not match its experiment.")
        return state, sessions


def save_session(
    session: dict,
) -> None:
    """
    保存一次完整 Training Session。

    常用字段单独存列：
        方便未来 SQL 查询。

    完整 Session:
        仍然保存到 raw_data,
        保留原始训练证据。
    """

    # One identity cannot represent both an archived interruption and a completed report.
    # Full validation still happens before any connection for invalid requests.
    # Enforce the same contract even when callers bypass the HTTP endpoint.
    require_valid_session(session)

    raw_data = json.dumps(
        session,
        ensure_ascii=False,
        allow_nan=False,
    )

    with closing(_connect()) as connection, connection:
        # Validate and insert under one write lock; two tabs cannot commit the same slot.
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM incomplete_sessions WHERE session_id=? UNION ALL SELECT 1 FROM scenario_sessions WHERE session_id=?", (session["sessionId"],session["sessionId"])).fetchone():
            raise ExperimentConflict("Session ID belongs to an interrupted report.")
        existing = connection.execute(
            "SELECT raw_data FROM sessions WHERE session_id=?", (session["sessionId"],)
        ).fetchone()
        if existing:
            # A lost HTTP acknowledgement may retry the exact same immutable payload.
            canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if canonical(json.loads(existing["raw_data"])) == canonical(session):
                return
            raise sqlite3.IntegrityError("Session ID belongs to a different payload.")
        row = connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?", (session["experimentId"],)
        ).fetchone()
        if row is None:
            raise ExperimentConflict("Create a managed experiment before uploading sessions.")
        state = _experiment_state(connection, row)
        require_matching_experiment(session, state)
        connection.execute(
            """
            INSERT INTO sessions (
                session_id,
                experiment_id,
                schema_version,
                started_at,
                ended_at,
                trainer_sensitivity,
                target_count,
                experiment_order,
                sensitivity_repeat,
                raw_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["sessionId"],
                session["experimentId"],
                session["schemaVersion"],
                session["startedAt"],
                session.get("endedAt"),
                session["trainerSensitivity"],
                session["targetCount"],
                session.get("experimentOrder"),
                session.get("sensitivityRepeat"),
                raw_data,
            ),
        )
        # Release the active slot only after the last condition has been committed.
        if state["completed_count"] + 1 == len(state["schedule"]):
            connection.execute(
                "UPDATE experiments SET active_slot=NULL WHERE experiment_id=?",
                (session["experimentId"],),
            )


def get_session(
    session_id: str,
) -> dict | None:
    """
    根据 session_id 读取完整 Raw Session。

    Analyzer 将来可以复用类似的读取能力，
    而不是直接知道 SQLite 表结构。
    """

    with closing(_connect()) as connection, connection:

        row = connection.execute(
            """
            SELECT raw_data
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        return None

    session = json.loads(row["raw_data"])
    # Read existing data without rewriting it, but never return a mismatched identity.
    if not isinstance(session, dict) or session.get("sessionId") != session_id:
        raise ExperimentConflict("Stored session identity is invalid.")
    return session

def get_sessions_by_experiment(
    experiment_id: str,
) -> list[dict]:
    """
    返回属于指定 Experiment 的完整 Raw Sessions。

    Storage Layer 负责：
    - SQL 查询
    - JSON 反序列化

    Analyzer 不需要知道 SQLite 表结构。
    """

    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """
            SELECT raw_data
            FROM sessions
            WHERE experiment_id = ?
            ORDER BY experiment_order
            """,
            (experiment_id,),
        ).fetchall()

    return [
        json.loads(row["raw_data"])
        for row in rows
    ]

def get_latest_experiment_id() -> str | None:
    """
    当前单用户 MVP:

    根据最近保存的 Session,
    判断当前最新的 Experiment。

    未来加入 user_id 后，可以自然升级为：
    get_latest_experiment_id(user_id)
    """

    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT experiment_id
            FROM sessions
            ORDER BY saved_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    return row["experiment_id"]


def save_incomplete_session(session: dict) -> None:
    """Archive client interruption reports, validating all completed attempts without padding data."""
    from analysis.session_validator import validate_session_integrity
    if session.get("status") != "incomplete" or session.get("interruptionReason") not in ("user_ended", "time_limit", "escape", "page_exit", "condition_changed"):
        raise ValueError("Invalid interruption status or reason.")
    if type(session.get("targetCount")) is not int or not isinstance(session.get("attempts"), list) or len(session["attempts"]) >= session["targetCount"]:
        raise ValueError("Incomplete reports require fewer completed attempts than targetCount.")
    # Validate the same metadata/events without inventing missing Attempts or treating this as complete.
    errors = validate_session_integrity(session, allow_incomplete=True)
    if errors:raise ValueError("Invalid interrupted report: " + "; ".join(errors))
    encoded = json.dumps(session, sort_keys=True, allow_nan=False)
    with closing(_connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT raw_data FROM incomplete_sessions WHERE session_id=?",(session["sessionId"],)).fetchone()
        if existing:
            if existing["raw_data"] == encoded:return
            raise ExperimentConflict("Interrupted Session ID has a different payload.")
        if connection.execute("SELECT 1 FROM sessions WHERE session_id=? UNION ALL SELECT 1 FROM scenario_sessions WHERE session_id=?",(session["sessionId"],session["sessionId"])).fetchone():
            raise ExperimentConflict("Session ID belongs to a completed report.")
        row=connection.execute("SELECT * FROM experiments WHERE experiment_id=?",(session["experimentId"],)).fetchone()
        if row is None:raise ExperimentConflict("Unknown experiment.")
        state=_experiment_state(connection,row)
        order=session.get("experimentOrder")
        if type(order) is not int or not 1 <= order <= len(state["schedule"]):raise ValueError("Invalid experimentOrder.")
        # A delayed archive may arrive after another tab saved this slot; preserve it without advancing.
        require_matching_experiment(session,{**state,"completed_count":order-1,"next_condition":state["schedule"][order-1]})
        connection.execute("INSERT INTO incomplete_sessions(session_id,experiment_id,started_at,raw_data) VALUES(?,?,?,?)",
                           (session["sessionId"],session["experimentId"],session["startedAt"],encoded))


def history_sessions(experiment_id=None, date=None, scene=None, limit=100, offset=0):
    with closing(_connect()) as connection:
        rows=connection.execute("""SELECT * FROM (
            SELECT session_id,experiment_id,started_at,target_count,'complete' AS status,saved_at,'flick_2d' AS scene FROM sessions
            UNION ALL
            SELECT session_id,experiment_id,started_at,NULL AS target_count,'incomplete' AS status,saved_at,'flick_2d' AS scene FROM incomplete_sessions
            UNION ALL
            SELECT session_id,experiment_id,json_extract(raw_data,'$.startedAt') AS started_at,json_extract(raw_data,'$.targetCount') AS target_count,status,saved_at,scenario_id AS scene FROM scenario_sessions)
            WHERE (? IS NULL OR experiment_id=?) AND (? IS NULL OR substr(started_at,1,10)=?) AND (? IS NULL OR scene=?)
            ORDER BY started_at DESC,session_id LIMIT ? OFFSET ?""",(experiment_id,experiment_id,date,date,scene,scene,limit,offset)).fetchall()
        return [dict(row) for row in rows]


def history_experiments(date=None, scene=None, limit=100, offset=0):
    # Include both evidence families without passing tracking data to legacy analysis.
    with closing(_connect()) as connection:
        rows=connection.execute("""SELECT ids.experiment_id,e.created_at,e.protocol,
            EXISTS(SELECT 1 FROM experiment_endings x WHERE x.experiment_id=ids.experiment_id) AS ended,
            (SELECT count(*) FROM sessions s WHERE s.experiment_id=ids.experiment_id) +
            (SELECT count(*) FROM scenario_sessions s WHERE s.experiment_id=ids.experiment_id AND s.status='complete') AS saved_sessions,
            (SELECT count(*) FROM incomplete_sessions i WHERE i.experiment_id=ids.experiment_id) +
            (SELECT count(*) FROM scenario_sessions s WHERE s.experiment_id=ids.experiment_id AND s.status='incomplete') AS interrupted_sessions
            FROM (SELECT experiment_id FROM experiments UNION SELECT experiment_id FROM sessions UNION SELECT experiment_id FROM incomplete_sessions UNION SELECT experiment_id FROM scenario_sessions) ids
            LEFT JOIN experiments e ON e.experiment_id=ids.experiment_id
            WHERE (? IS NULL OR substr(e.created_at,1,10)=? OR EXISTS
                (SELECT 1 FROM sessions s WHERE s.experiment_id=ids.experiment_id AND substr(s.started_at,1,10)=?) OR EXISTS
                (SELECT 1 FROM incomplete_sessions i WHERE i.experiment_id=ids.experiment_id AND substr(i.started_at,1,10)=?) OR EXISTS
                (SELECT 1 FROM scenario_sessions s WHERE s.experiment_id=ids.experiment_id AND substr(json_extract(s.raw_data,'$.startedAt'),1,10)=?))
            AND (? IS NULL OR coalesce(json_extract(e.protocol,'$.scenario.scenario_id'),'flick_2d')=?)
            ORDER BY e.created_at DESC,ids.experiment_id LIMIT ? OFFSET ?""",(date,date,date,date,date,scene,scene,limit,offset)).fetchall()
        return [{**{k:v for k,v in dict(row).items() if k!="protocol"},"managed":row["protocol"] is not None,
                 "scene":json.loads(row["protocol"]).get("scenario",{}).get("scenario_id","flick_2d") if row["protocol"] else "flick_2d"} for row in rows]



def history_session_detail(session_id):
    from analysis.analysis_service import calculate_attempt_metrics, calculate_session_summary
    session=get_session(session_id)
    if session is not None:
        from copy import deepcopy
        with closing(_connect()) as connection:
            identity=connection.execute("SELECT experiment_id FROM sessions WHERE session_id=?",(session_id,)).fetchone()[0]
        analysis_session=deepcopy(session)
        derived={}
        if "experimentId" not in analysis_session:
            # SQL retains the original association; derive only for analysis, never rewrite raw history.
            analysis_session["experimentId"]=identity
            derived["experimentId"]="sessions.experiment_id"
        elif analysis_session["experimentId"] != identity:
            raise ExperimentConflict("Stored Session association is inconsistent.")
        require_valid_session(analysis_session)
        return {"session":session,"status":"complete","derived_fields":derived,"attempt_metrics":[calculate_attempt_metrics(a) for a in analysis_session["attempts"]],"statistics":calculate_session_summary(analysis_session)}
    with closing(_connect()) as connection:
        row=connection.execute("SELECT raw_data,experiment_id FROM incomplete_sessions WHERE session_id=?",(session_id,)).fetchone()
        if row is None:
            scored=connection.execute("SELECT raw_data,result_json,status FROM scenario_sessions WHERE session_id=?",(session_id,)).fetchone()
            if scored is None:raise ExperimentNotFound("Session not found.")
            result=json.loads(scored["result_json"])
            return {"session":json.loads(scored["raw_data"]),"status":scored["status"],"statistics":result["metrics"],"score":result,"scoring_version":result["scoring_version"]}
        archived=json.loads(row["raw_data"])
        from analysis.session_validator import validate_session_integrity
        if not isinstance(archived,dict) or archived.get("sessionId")!=session_id or archived.get("experimentId")!=row["experiment_id"]:
            raise ExperimentConflict("Interrupted report identity is inconsistent.")
        if archived.get("status")!="incomplete" or validate_session_integrity(archived,allow_incomplete=True):
            raise ExperimentConflict("Interrupted report is invalid.")
        return {"session":archived,"status":"incomplete","statistics":None,"attempt_metrics":[],"limitation":"Interrupted reports are excluded from deterministic experiment analysis."}


def end_experiment(experiment_id):
    # Termination metadata is separate from immutable settings/protocol and raw sessions.
    with closing(_connect()) as connection,connection:
        connection.execute("BEGIN IMMEDIATE")
        row=connection.execute("SELECT * FROM experiments WHERE experiment_id=?",(experiment_id,)).fetchone()
        if row is None:raise ExperimentNotFound("Experiment not found.")
        connection.execute("INSERT OR IGNORE INTO experiment_endings(experiment_id) VALUES(?)",(experiment_id,))
        connection.execute("UPDATE experiments SET active_slot=NULL WHERE experiment_id=?",(experiment_id,))
        return _experiment_state(connection,row)
