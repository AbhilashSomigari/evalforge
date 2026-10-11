from __future__ import annotations

import json
import logging
from pathlib import Path

from evalforge_agents.logging_utils import get_logger, log
from evalforge_agents.models import CURRENT_SCHEMA_VERSION, EvalRun

logger = get_logger(__name__)


def save_run(run: EvalRun, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2))
    return path


def parse_run(data: object, source: str) -> EvalRun:
    """Validate a decoded run artifact, from a file or a Postgres row alike.

    Both backends must apply the same compatibility rule: a run written under
    a different schema_version is rejected with a clear message rather than
    half-parsed (or failing deep inside pydantic validation).
    """
    if not isinstance(data, dict):
        raise ValueError(f"{source} does not contain a run object")
    version = data.get("schema_version")
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"{source} has schema_version={version!r}, but this build of EvalForge "
            f"reads schema_version={CURRENT_SCHEMA_VERSION}. Re-run the suite to "
            "regenerate the run artifact."
        )
    return EvalRun.model_validate(data)


def load_run(path: str | Path) -> EvalRun:
    path = Path(path)
    return parse_run(json.loads(path.read_text()), str(path))


def try_load_run(path: str | Path) -> EvalRun | None:
    """load_run for directory scans: a file that can't be read is skipped, but
    logged - silently skipping it (e.g. an old schema_version) would make a
    baseline lookup quietly fall back to an older run, or to none at all."""
    try:
        return load_run(path)
    except (OSError, ValueError) as exc:  # ValueError covers JSON + pydantic errors
        log(logger, logging.WARNING, "skipping unreadable run file", path=str(path), error=str(exc))
        return None


def find_latest_on_branch(run_dir: str | Path, suite_name: str, git_branch: str) -> EvalRun | None:
    """File-backend equivalent of db.latest_run: the most recent run of this
    suite on this branch, found by scanning run_dir. O(n) in the number of
    run files, same cost list_runs() already pays in api.py without a DB."""
    run_dir = Path(run_dir)
    if not run_dir.exists():
        return None
    best: EvalRun | None = None
    for path in run_dir.glob("*.json"):
        candidate = try_load_run(path)
        if candidate is None:
            continue
        if candidate.suite_name != suite_name or candidate.git_branch != git_branch:
            continue
        if best is None or candidate.created_at > best.created_at:
            best = candidate
    return best
