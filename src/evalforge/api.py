from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .logging_utils import get_logger, log, new_request_id, set_request_id
from .models import EvalRun
from .storage import load_run

logger = get_logger(__name__)

API_KEY_ENV = "EVALFORGE_API_KEY"
CORS_ORIGINS_ENV = "EVALFORGE_CORS_ORIGINS"
RATE_LIMIT_ENV = "EVALFORGE_RATE_LIMIT_PER_MINUTE"
_RATE_LIMIT_WINDOW_S = 60.0

# Per-process, in-memory: resets on restart and isn't shared across replicas.
# Fine for a single instance; a multi-replica deployment needs a shared store
# (e.g. Redis) for this to actually bound the total request rate.
_request_times: dict[str, list[float]] = defaultdict(list)


def parse_cors_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


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

_cors_origins = parse_cors_origins(os.getenv(CORS_ORIGINS_ENV, ""))
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )


@app.middleware("http")
async def _request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    set_request_id(request_id)
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    log(
        logger,
        logging.INFO,
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response


def _require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv(API_KEY_ENV)
    if not expected:
        return  # Auth is opt-in: unset means open, which the README calls out explicitly.
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _check_rate_limit(request: Request) -> None:
    limit = int(os.getenv(RATE_LIMIT_ENV, "0"))
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _request_times[client]
    cutoff = now - _RATE_LIMIT_WINDOW_S
    while hits and hits[0] < cutoff:
        hits.pop(0)
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    hits.append(now)


router = APIRouter(dependencies=[Depends(_require_api_key), Depends(_check_rate_limit)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/runs")
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


@router.get("/runs/{key}", response_model=EvalRun)
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


app.include_router(router)
