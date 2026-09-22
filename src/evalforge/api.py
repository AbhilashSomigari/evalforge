from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from . import db
from .models import EvalRun
from .storage import load_run


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # A fresh Postgres has no runs table until something writes to it; the API
    # itself never writes, only evalforge run/ab/replay do, so it must ensure
    # the schema exists rather than assuming a CLI invocation already ran.
    if db.is_enabled():
        db.ensure_schema()
    yield


app = FastAPI(title="EvalForge API", version="0.1.0", lifespan=_lifespan)
RUN_DIR = Path("runs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runs")
def list_runs(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    suite: str | None = None,
) -> list[dict]:
    if db.is_enabled():
        return db.list_runs(limit=limit, offset=offset, suite_name=suite)

    # File-backend fallback: still parses every run on every request. This is the
    # exact cost the Postgres backend above exists to avoid; enable it via
    # EVALFORGE_DATABASE_URL once a suite generates enough runs for that to matter.
    if not RUN_DIR.exists():
        return []
    matched = []
    for path in sorted(RUN_DIR.glob("*.json"), reverse=True):
        try:
            run = load_run(path)
        except Exception:
            continue
        if suite and run.suite_name != suite:
            continue
        matched.append((path, run))
    rows = []
    for path, run in matched[offset : offset + limit]:
        rows.append(
            {
                "id": run.id,
                "file": path.name,
                "suite": run.suite_name,
                "agent": run.agent_name,
                "created_at": run.created_at,
                "summary": run.summary.model_dump(),
            }
        )
    return rows


@app.get("/runs/{key}", response_model=EvalRun)
def get_run(key: str) -> EvalRun:
    if db.is_enabled():
        run = db.fetch_run(key)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    safe = Path(key).name
    path = RUN_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return load_run(path)
