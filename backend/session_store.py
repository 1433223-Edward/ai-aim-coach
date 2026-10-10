import json
import sqlite3
from pathlib import Path


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

DB_PATH = PROJECT_ROOT / "data" / "aimcoach.db"


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

    with _connect() as connection:

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

    raw_data = json.dumps(
        session,
        ensure_ascii=False,
    )

    with _connect() as connection:

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


def get_session(
    session_id: str,
) -> dict | None:
    """
    根据 session_id 读取完整 Raw Session。

    Analyzer 将来可以复用类似的读取能力，
    而不是直接知道 SQLite 表结构。
    """

    with _connect() as connection:

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

    return json.loads(
        row["raw_data"]
    )

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

    with _connect() as connection:
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

    with _connect() as connection:
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