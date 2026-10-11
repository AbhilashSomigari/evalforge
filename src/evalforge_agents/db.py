from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from typing import Any

from evalforge_agents.logging_utils import get_logger, log
from evalforge_agents.models import CURRENT_SCHEMA_VERSION, EvalRun
from evalforge_agents.storage import parse_run

DATABASE_URL_ENV = "EVALFORGE_DATABASE_URL"
POOL_MAX_SIZE_ENV = "EVALFORGE_DB_POOL_MAX_SIZE"
_POOL_CONNECT_TIMEOUT_S = 10.0

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    suite_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    task_success DOUBLE PRECISION NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_suite_name_created_at_idx ON runs (suite_name, created_at DESC);
-- Added for baseline-branch lookup: CREATE TABLE IF NOT EXISTS above won't add
-- these to a table that already existed before this feature, so migrate explicitly.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS git_branch TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS suite_version TEXT;
CREATE INDEX IF NOT EXISTS runs_suite_branch_idx ON runs (suite_name, git_branch, created_at DESC);
-- Shared rate-limit counters, so EVALFORGE_RATE_LIMIT_PER_MINUTE bounds the total
-- request rate across API replicas rather than per process. window_start leads
-- the key so pruning old windows is an index range delete.
CREATE TABLE IF NOT EXISTS rate_limits (
    window_start BIGINT NOT NULL,
    client TEXT NOT NULL,
    hits INTEGER NOT NULL,
    PRIMARY KEY (window_start, client)
);
"""

# One pool per database URL (tests point at different URLs within a process).
# A pool instead of a connection per call: the API serves every request from
# here, and opening a fresh Postgres connection per request is the first
# thing that falls over under real traffic.
_pools: dict[str, Any] = {}
_pools_lock = threading.Lock()


def database_url() -> str | None:
    return os.getenv(DATABASE_URL_ENV)


def is_enabled() -> bool:
    return database_url() is not None


def _connect() -> Any:
    """A pooled connection as a context manager: commits on clean exit, rolls
    back on an exception, and returns the connection to the pool either way."""
    url = database_url()
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not set")
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            # Imported lazily so the postgres extra stays optional for file-backend-only use.
            import psycopg
            from psycopg_pool import ConnectionPool

            # One direct connection first: a bad URL, wrong password, or down
            # server fails immediately with psycopg's real error, instead of the
            # pool retrying in the background and surfacing only a PoolTimeout.
            psycopg.connect(url, connect_timeout=int(_POOL_CONNECT_TIMEOUT_S)).close()
            pool = ConnectionPool(
                url,
                min_size=1,
                max_size=int(os.getenv(POOL_MAX_SIZE_ENV, "10")),
                timeout=_POOL_CONNECT_TIMEOUT_S,
                open=True,
            )
            try:
                pool.wait(timeout=_POOL_CONNECT_TIMEOUT_S)
            except Exception:
                pool.close()
                raise
            _pools[url] = pool
    return pool.connection()


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


atexit.register(close_pools)


def ensure_schema() -> None:
    with _connect() as conn:
        conn.execute(_SCHEMA)


def insert_run(run: EvalRun) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (id, schema_version, suite_name, agent_name, created_at,
                               task_success, git_branch, suite_version, data)
            VALUES (%(id)s, %(schema_version)s, %(suite_name)s, %(agent_name)s,
                    %(created_at)s, %(task_success)s, %(git_branch)s, %(suite_version)s,
                    %(data)s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                data = EXCLUDED.data,
                task_success = EXCLUDED.task_success,
                git_branch = EXCLUDED.git_branch,
                suite_version = EXCLUDED.suite_version
            """,
            {
                "id": run.id,
                "schema_version": run.schema_version,
                "suite_name": run.suite_name,
                "agent_name": run.agent_name,
                "created_at": run.created_at,
                "task_success": run.summary.task_success,
                "git_branch": run.git_branch,
                "suite_version": run.suite_version,
                "data": run.model_dump_json(),
            },
        )


def fetch_run(run_id: str) -> EvalRun | None:
    """Raises ValueError for a run stored under an incompatible schema_version,
    exactly like storage.load_run does for a file."""
    with _connect() as conn:
        row = conn.execute("SELECT data FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        return None
    return parse_run(row[0], f"run {run_id}")


def latest_run(suite_name: str, git_branch: str) -> EvalRun | None:
    """The most recent run of `suite_name` on `git_branch` - the auto-resolved
    baseline for `evalforge run --baseline-branch`.

    Runs from an incompatible schema_version can't serve as a baseline; they're
    skipped (and the skip is logged, never silent - same as the file backend's
    storage.try_load_run), falling back to the latest compatible run."""
    with _connect() as conn:
        skipped = conn.execute(
            """
            SELECT id, schema_version FROM runs
            WHERE suite_name = %s AND git_branch = %s AND schema_version <> %s
              AND created_at > COALESCE(
                  (SELECT max(created_at) FROM runs
                   WHERE suite_name = %s AND git_branch = %s AND schema_version = %s),
                  '-infinity')
            """,
            (suite_name, git_branch, CURRENT_SCHEMA_VERSION) * 2,
        ).fetchall()
        row = conn.execute(
            """
            SELECT data FROM runs
            WHERE suite_name = %s AND git_branch = %s AND schema_version = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (suite_name, git_branch, CURRENT_SCHEMA_VERSION),
        ).fetchone()
    for run_id, version in skipped:
        log(
            logger,
            logging.WARNING,
            "skipping run with incompatible schema_version",
            run_id=run_id,
            schema_version=version,
        )
    if row is None:
        return None
    return parse_run(row[0], "latest run")


def list_runs(limit: int = 20, offset: int = 0, suite_name: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT id, suite_name, agent_name, created_at, git_branch, suite_version, data FROM runs"
    params: list[Any] = []
    if suite_name:
        query += " WHERE suite_name = %s"
        params.append(suite_name)
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for run_id, suite, agent, created_at, git_branch, suite_version, data in rows:
        try:
            run = parse_run(data, f"run {run_id}")
        except ValueError as exc:
            # Same policy as the file backend's listing: skip, but say so.
            log(logger, logging.WARNING, "skipping unreadable run", run_id=run_id, error=str(exc))
            continue
        results.append(
            {
                "id": run_id,
                "suite": suite,
                "agent": agent,
                "created_at": created_at,
                "git_branch": git_branch,
                "suite_version": suite_version,
                "summary": run.summary.model_dump(),
            }
        )
    return results


def hit_rate_limit(client: str, window_s: int) -> int:
    """Count one request from `client` in the current fixed window and return
    the window's total so far, atomically across every API replica."""
    window_start = int(time.time()) // window_s * window_s
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO rate_limits (window_start, client, hits) VALUES (%s, %s, 1)
            ON CONFLICT (window_start, client) DO UPDATE SET hits = rate_limits.hits + 1
            RETURNING hits
            """,
            (window_start, client),
        ).fetchone()
        # Prune expired windows cheaply (index range delete on the leading key).
        conn.execute("DELETE FROM rate_limits WHERE window_start < %s", (window_start - window_s,))
    return int(row[0])
