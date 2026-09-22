from __future__ import annotations

import json
from pathlib import Path

from evalforge.models import CURRENT_SCHEMA_VERSION, EvalRun


def save_run(run: EvalRun, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(path: str | Path) -> EvalRun:
    path = Path(path)
    data = json.loads(path.read_text())
    version = data.get("schema_version")
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version={version!r}, but this build of EvalForge "
            f"reads schema_version={CURRENT_SCHEMA_VERSION}. Re-run the suite to "
            "regenerate the run artifact."
        )
    return EvalRun.model_validate(data)


def find_latest_on_branch(run_dir: str | Path, suite_name: str, git_branch: str) -> EvalRun | None:
    """File-backend equivalent of db.latest_run: the most recent run of this
    suite on this branch, found by scanning run_dir. O(n) in the number of
    run files, same cost list_runs() already pays in api.py without a DB."""
    run_dir = Path(run_dir)
    if not run_dir.exists():
        return None
    best: EvalRun | None = None
    for path in run_dir.glob("*.json"):
        try:
            candidate = load_run(path)
        except Exception:
            continue
        if candidate.suite_name != suite_name or candidate.git_branch != git_branch:
            continue
        if best is None or candidate.created_at > best.created_at:
            best = candidate
    return best
