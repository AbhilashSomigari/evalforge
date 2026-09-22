from __future__ import annotations

from pathlib import Path

from evalforge.models import EvalRun


def save_run(run: EvalRun, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(path: str | Path) -> EvalRun:
    return EvalRun.model_validate_json(Path(path).read_text())
