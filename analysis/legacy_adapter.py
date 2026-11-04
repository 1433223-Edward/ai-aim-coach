"""Read-only adapter for versioned engineering fixtures, never a live-data migration."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_legacy_fixture(dataset="experiment_002") -> tuple[list[dict], list[dict]]:
    manifest = json.loads((ROOT / "tests/fixtures/legacy_manifest.json").read_text(encoding="utf-8"))
    sessions, sources = [], []
    for name, expected_hash in manifest.items():
        if not name.startswith(f"data/{dataset}/"):
            continue
        raw = (ROOT / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(f"Historical fixture changed: {name}")
        session = json.loads(raw)
        # The derived identity labels a fixture group; it does not recover lost experiment metadata.
        missing = "experimentId" not in session
        session.setdefault("experimentId", f"fixture:{dataset}")
        sessions.append(session)
        sources.append({"path": name, "sha256": expected_hash,
                        "derived_identity": session["experimentId"] if missing else None,
                        "order_unknown": "experimentOrder" not in session})
    if not sessions:
        raise ValueError("Unknown or empty historical fixture group.")
    return sessions, sources
