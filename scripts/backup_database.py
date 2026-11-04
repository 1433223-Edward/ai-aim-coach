"""Create or restore a SQLite snapshot to a NEW explicit destination only."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def backup_database(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    # Existing destinations (including real training databases) are never overwritten.
    if not source.is_file() or source == destination:
        raise ValueError("Source must be an existing database distinct from destination.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, \
                closing(sqlite3.connect(destination)) as snapshot:
            # SQLite backup includes committed WAL contents; copying the .db file alone does not.
            original.backup(snapshot)
            if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Snapshot integrity check failed.")
            count = snapshot.execute("SELECT count(*) FROM sessions").fetchone()[0]
        return {"session_count": count, "destination": str(destination)}
    except Exception:
        # Remove only the new file created by this invocation, never the source or an old backup.
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(backup_database(args.source, args.destination))
