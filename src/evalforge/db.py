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
            INSERT INTO runs (id, schema_version, suite_name, agent_name, created_at, task_success, data)
            VALUES (%(id)s, %(schema_version)s, %(suite_name)s, %(agent_name)s,
                    %(created_at)s, %(task_success)s, %(data)s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                data = EXCLUDED.data,
                task_success = EXCLUDED.task_success
            """,
            {
                "id": run.id,
                "schema_version": run.schema_version,
                "suite_name": run.suite_name,
                "agent_name": run.agent_name,
                "created_at": run.created_at,
                "task_success": run.summary.task_success,
                "data": run.model_dump_json(),
            },
        )


def fetch_run(run_id: str) -> EvalRun | None:
    with _connect() as conn:
        row = conn.execute("SELECT data FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        return None
    return EvalRun.model_validate(row[0])


def list_runs(limit: int = 20, offset: int = 0, suite_name: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT id, suite_name, agent_name, created_at, data FROM runs"
    params: list[Any] = []
    if suite_name:
        query += " WHERE suite_name = %s"
        params.append(suite_name)
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for run_id, suite, agent, created_at, data in rows:
        run = EvalRun.model_validate(data)
        results.append(
            {
                "id": run_id,
                "suite": suite,
                "agent": agent,
                "created_at": created_at,
                "summary": run.summary.model_dump(),
            }
        )
    return results
