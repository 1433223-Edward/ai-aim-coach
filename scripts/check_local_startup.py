"""Real local HTTP smoke using synthetic data, no key, and a disposable database."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def check_local_startup():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "smoke.db"
        # Subprocesses receive only the temporary database and an explicitly empty provider key.
        env = {**os.environ, "AIMCOACH_DB_PATH": str(database), "DASHSCOPE_API_KEY": "",
               "PYTHONDONTWRITEBYTECODE": "1"}
        # Seed refuses an unverified configured training path; select the DB only after creation.
        seed_env = {key: value for key, value in env.items() if key != "AIMCOACH_DB_PATH"}
        seeded = subprocess.check_output([sys.executable, "-B", "-m", "scripts.seed_demo", "--db", str(database)],
                                         cwd=ROOT, env=seed_env, text=True)
        experiment_id = json.loads(seeded)["experiment_id"]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        def request(path, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            query = Request(f"http://127.0.0.1:{port}{path}", data=body,
                            headers={"Content-Type": "application/json"})
            try:
                response = urlopen(query, timeout=3)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, response.read()

        # Start twice against the same isolated DB to verify persistence across process restart.
        for attempt in range(2):
            with tempfile.TemporaryFile() as log:
                server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "backend.main:app",
                                           "--host", "127.0.0.1", "--port", str(port)],
                                          cwd=ROOT, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            status, body = request("/health")
                            assert status == 200, body
                            assert json.loads(body) == {"storage": "ready", "agent": "unavailable"}
                            break
                        except (URLError, TimeoutError):
                            if server.poll() is not None or time.monotonic() >= deadline:
                                log.seek(0)
                                raise RuntimeError(log.read().decode(errors="replace"))
                            time.sleep(0.1)
                    assert request("/")[0] == 200
                    status, body = request(f"/experiments/{experiment_id}/analysis")
                    assert status == 200, body
                    evidence = json.loads(body)["evidence"]
                    assert evidence["session_count"] == 9 and evidence["attempt_count"] == 27
                    if attempt == 0:
                        original_snapshot = evidence["snapshot_id"]
                        status, raw = request("/sessions/" + evidence["session_ids"][0])
                        assert status == 200
                        sample = json.loads(raw)
                        assert request("/sessions", sample)[0] == 200
                        assert request("/sessions", {**sample, "syntheticDemo": False})[0] == 409
                        status, body = request("/experiments", {"trainer_width": 800, "trainer_height": 600})
                        assert status == 200
                        active = json.loads(body)
                        # Reuse only synthetic geometry; identity and condition come from the new server schedule.
                        sample.update(sessionId="smoke-upload", experimentId=active["experiment_id"],
                                      experimentOrder=1, trainerSensitivity=active["next_condition"]["sensitivity"],
                                      sensitivityRepeat=active["next_condition"]["repeat"])
                        assert request("/sessions", sample)[0] == 200
                        assert json.loads(request("/sessions/smoke-upload")[1]) == sample
                        assert request("/agent/plan", {"goal": " ", "experiment_id": experiment_id})[0] == 422
                        assert request("/sessions/unknown")[0] == 404
                    else:
                        assert evidence["snapshot_id"] == original_snapshot
                    status, body = request("/agent/plan", {"goal": "smoke", "experiment_id": experiment_id})
                    assert status == 503 and json.loads(body)["detail"]["code"] == "model_unavailable", body
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
        print("PASS: isolated real HTTP, static page, health, upload/read/idempotency/conflict/422/404, 9/27 analysis, no-key 503, restart snapshot stability.")


if __name__ == "__main__":
    check_local_startup()
