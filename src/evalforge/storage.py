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
