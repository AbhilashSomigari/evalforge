from __future__ import annotations

import os
from typing import Any

from evalforge.models import EvalRun

DATABASE_URL_ENV = "EVALFORGE_DATABASE_URL"

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
"""


def database_url() -> str | None:
    return os.getenv(DATABASE_URL_ENV)


def is_enabled() -> bool:
    return database_url() is not None


def _connect() -> Any:
    # Imported lazily so the postgres extra stays optional for file-backend-only use.
    import psycopg

    url = database_url()
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not set")
    return psycopg.connect(url)


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
    with _connect() as conn:
        row = conn.execute("SELECT data FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        return None
    return EvalRun.model_validate(row[0])


def latest_run(suite_name: str, git_branch: str) -> EvalRun | None:
    """The most recent run of `suite_name` on `git_branch` - the auto-resolved
    baseline for `evalforge run --baseline-branch`."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT data FROM runs
            WHERE suite_name = %s AND git_branch = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (suite_name, git_branch),
        ).fetchone()
    if row is None:
        return None
    return EvalRun.model_validate(row[0])


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
        run = EvalRun.model_validate(data)
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
