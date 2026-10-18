"""One sandbox run from the host: run.sh, the wait on its lock, and its result. Marbas and Valac share it."""

import json
import subprocess
import time
from pathlib import Path

RUN_LOCKED = 3  # run.sh exit code when another run holds the lock
LOCK_TRIES = 60
LOCK_WAIT_SECONDS = 60


def sandbox_run(run_sh: str, args: list[str], runs: Path, pattern: str) -> tuple[dict, Path | None]:
    """Returns the run's result and its folder, the newest that matches `pattern`. Waits while a hand run holds the lock."""
    for _ in range(LOCK_TRIES):
        done = subprocess.run([run_sh, *args], capture_output=True, text=True)
        if done.returncode != RUN_LOCKED:
            break
        time.sleep(LOCK_WAIT_SECONDS)
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        result = {"outcome": "blocked", "reason": f"run.sh exited {done.returncode} with no result"}
    run_dirs = sorted((p for p in runs.glob(pattern) if p.is_dir()), key=lambda path: path.stat().st_mtime)
    return result, (run_dirs[-1] if run_dirs else None)
