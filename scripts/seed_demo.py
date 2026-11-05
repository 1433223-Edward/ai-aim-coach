"""Seed explicitly synthetic evidence into an explicitly selected, separate database."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend import session_store as store


def demo_session(state, index):
    protocol = state["protocol"]
    condition = state["schedule"][index]
    attempts = []
    for target_index in range(protocol["target_count"]):
        offset = target_index * 50
        attempts.append({
            "target": {"x": 100, "y": 100, "radius": 25, "cursorStartX": 0, "cursorStartY": 100,
                       "appearTime": offset + 10, "disappearTime": offset + 31},
            "movements": [{"time": offset + 30, "rawDX": 100 / condition["sensitivity"], "rawDY": 0,
                           "x": 100, "y": 100}],
            "clicks": [{"time": offset + 30, "x": 100, "y": 100, "hit": True}],
        })
    return {
        "schemaVersion": "0.1", "protocolVersion": "1", "syntheticDemo": True,
        "sessionId": str(uuid5(NAMESPACE_URL, f"aimcoach-synthetic-v1/session/{index}")),
        "experimentId": state["experiment_id"], "experimentOrder": index + 1,
        "sensitivityRepeat": condition["repeat"], "trainerSensitivity": condition["sensitivity"],
        "trainerWidth": 800, "trainerHeight": 600, "targetCount": 3,
        "inputMode": "unadjusted_requested", "startTime": 0, "endTime": 140,
        "startedAt": "2026-09-01T00:00:00Z", "endedAt": "2026-09-01T00:00:01Z", "attempts": attempts,
    }


def seed_demo(destination: Path) -> dict:
    destination = destination.resolve()
    if destination == (ROOT / "data/aimcoach.db").resolve():
        raise ValueError("Refusing to seed the historical training database.")
    configured = destination == store.DB_PATH.resolve()
    # Inspect existing content read-only before running any CREATE or INSERT.
    if destination.exists():
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {"sessions", "experiments", "incomplete_sessions", "experiment_endings", "scenario_sessions"}:
                raise ValueError("Destination contains unrelated data.")
            if "sessions" in tables and any(json.loads(r[0]).get("syntheticDemo") is not True
                                            for r in connection.execute("SELECT raw_data FROM sessions")):
                raise ValueError("Destination contains non-demo sessions.")
            if "scenario_sessions" in tables and connection.execute("SELECT 1 FROM scenario_sessions LIMIT 1").fetchone():
                raise ValueError("Destination contains scenario training data.")
            if "experiment_endings" in tables and connection.execute("SELECT 1 FROM experiment_endings LIMIT 1").fetchone():
                raise ValueError("Destination contains ended experiments.")
            if "incomplete_sessions" in tables and connection.execute("SELECT 1 FROM incomplete_sessions LIMIT 1").fetchone():
                raise ValueError("Destination contains interrupted training data.")
            if "experiments" in tables and any(json.loads(r[0]).get("demo_fixture") != "synthetic-v1"
                                               for r in connection.execute("SELECT protocol FROM experiments")):
                raise ValueError("Destination contains non-demo experiments.")
            # A running demo may point at this path; only its explicit marker permits reseeding.
            if configured and ("experiments" not in tables or not connection.execute("SELECT 1 FROM experiments LIMIT 1").fetchone()):
                raise ValueError("Configured database is not a verified synthetic demo.")
    elif configured:
        raise ValueError("Refusing to create a demo at an unverified configured database path.")
    previous_path = store.DB_PATH
    store.DB_PATH = destination
    try:
        store.init_database()
        state = store.get_experiment()
        if state is None:
            state = store.get_or_create_experiment(800, 600)
            # Mark all generated data as synthetic; no historical IDs or order are invented.
            state["protocol"]["demo_fixture"] = "synthetic-v1"
            state["schedule"] = [{"sensitivity": s, "repeat": r} for r in (1, 2, 3) for s in (0.8, 1.0, 1.2)]
            with closing(store._connect()) as connection, connection:
                connection.execute("UPDATE experiments SET protocol=?, schedule=? WHERE experiment_id=?",
                                   (json.dumps(state["protocol"]), json.dumps(state["schedule"]), state["experiment_id"]))
        for index in range(len(state["schedule"])):
            store.save_session(demo_session(state, index))
        state = store.get_experiment(state["experiment_id"])
        return {"data_kind": "SYNTHETIC DEMO ONLY", "database": str(destination),
                "experiment_id": state["experiment_id"], "session_count": state["completed_count"]}
    finally:
        store.DB_PATH = previous_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True, help="Separate demo DB; training DB is refused.")
    args = parser.parse_args()
    print(json.dumps(seed_demo(args.db), ensure_ascii=False))
