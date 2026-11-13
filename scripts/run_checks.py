"""One offline verification command shared by local development and CI."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
commands = [
    [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
    ["node", "--experimental-vm-modules", "--test", "tests/test_experiment_frontend.cjs", "tests/test_pending_queue.cjs", "tests/test_frontend_shell.cjs", "tests/test_phase1_frontend.cjs", "tests/test_phase2_ui.cjs", "tests/test_phase3_ui.cjs", "tests/test_phase4_ui.cjs"],
    ["node", "--check", "frontend/trainer.js"],
    ["node", "--check", "frontend/pending_sessions.js"],
]
# Check every shipped module, including modules not imported by a particular test scenario.
commands.extend(["node", "--check", str(path.relative_to(ROOT))] for path in sorted((ROOT / "frontend").glob("*.js"))
                if path.name not in ("trainer.js", "pending_sessions.js"))
if __name__ == "__main__":
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
