from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import EvalRun
from .storage import load_run

app = FastAPI(title="EvalForge API", version="0.1.0")
RUN_DIR = Path("runs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runs")
def list_runs() -> list[dict]:
    if not RUN_DIR.exists():
        return []
    rows = []
    for path in sorted(RUN_DIR.glob("*.json"), reverse=True):
        try:
            run = load_run(path)
            rows.append({
                "id": run.id,
                "file": path.name,
                "suite": run.suite_name,
                "agent": run.agent_name,
                "created_at": run.created_at,
                "summary": run.summary.model_dump(),
            })
        except Exception:
            continue
    return rows


@app.get("/runs/{filename}", response_model=EvalRun)
def get_run(filename: str) -> EvalRun:
    safe = Path(filename).name
    path = RUN_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return load_run(path)
