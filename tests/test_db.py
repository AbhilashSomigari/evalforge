from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("psycopg")

import evalforge.api as api_module  # noqa: E402
from evalforge import db  # noqa: E402
from evalforge.models import EvalRun, RunSummary  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv(db.DATABASE_URL_ENV),
    reason=f"{db.DATABASE_URL_ENV} not set; skipping Postgres-backed tests",
)


def _run(suite_name: str = "s", task_success: float = 1.0, **overrides) -> EvalRun:
    return EvalRun(
        suite_name=suite_name,
        agent_name="a",
        results=[],
        summary=RunSummary(task_success=task_success),
        **overrides,
    )


@pytest.fixture(autouse=True)
def clean_runs_table():
    db.ensure_schema()
    with db._connect() as conn:
        conn.execute("TRUNCATE TABLE runs")
    yield


def test_insert_and_fetch_round_trip():
    run = _run()
    db.insert_run(run)
    fetched = db.fetch_run(run.id)
    assert fetched is not None
    assert fetched.id == run.id
    assert fetched.suite_name == "s"
    assert fetched.summary.task_success == 1.0


def test_fetch_missing_run_returns_none():
    assert db.fetch_run("does-not-exist") is None


def test_insert_run_upserts_on_conflict():
    run = _run(task_success=0.5)
    db.insert_run(run)
    updated = run.model_copy(update={"summary": RunSummary(task_success=0.9)})
    db.insert_run(updated)
    fetched = db.fetch_run(run.id)
    assert fetched.summary.task_success == 0.9


def test_list_runs_orders_by_created_at_desc_and_paginates():
    for i in range(3):
        db.insert_run(_run(suite_name=f"suite-{i}"))
    all_rows = db.list_runs(limit=10, offset=0)
    assert len(all_rows) == 3
    page = db.list_runs(limit=1, offset=1)
    assert len(page) == 1
    assert page[0]["id"] == all_rows[1]["id"]


def test_list_runs_filters_by_suite_name():
    db.insert_run(_run(suite_name="alpha"))
    db.insert_run(_run(suite_name="beta"))
    rows = db.list_runs(limit=10, offset=0, suite_name="alpha")
    assert len(rows) == 1
    assert rows[0]["suite"] == "alpha"


def test_latest_run_returns_most_recent_on_branch():
    db.insert_run(_run(suite_name="s", git_branch="main"))
    newest = _run(suite_name="s", git_branch="main")
    db.insert_run(newest)
    db.insert_run(_run(suite_name="s", git_branch="feature-x"))
    found = db.latest_run("s", "main")
    assert found is not None
    assert found.id == newest.id


def test_latest_run_returns_none_when_no_match():
    assert db.latest_run("no-such-suite", "main") is None


def test_ensure_schema_migrates_a_pre_existing_table_without_new_columns():
    # Simulates a database that already had the Phase 2 schema (no git_branch/
    # suite_version columns) before this feature existed - CREATE TABLE IF NOT
    # EXISTS alone wouldn't add them, which is exactly the bug the ALTER TABLE
    # ADD COLUMN IF NOT EXISTS migration in ensure_schema() exists to avoid.
    with db._connect() as conn:
        conn.execute("DROP TABLE IF EXISTS runs")
        conn.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                suite_name TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                task_success DOUBLE PRECISION NOT NULL,
                data JSONB NOT NULL
            )
            """
        )
    db.ensure_schema()
    run = _run(suite_name="s", git_branch="main")
    db.insert_run(run)
    assert db.latest_run("s", "main").id == run.id


def test_api_uses_db_backend_when_enabled(monkeypatch):
    run = _run(suite_name="api-suite")
    db.insert_run(run)
    monkeypatch.setattr(api_module, "RUN_DIR", api_module.RUN_DIR)  # unused in DB mode; sanity no-op
    client = TestClient(api_module.app)

    listed = client.get("/runs", params={"suite": "api-suite"}).json()
    assert len(listed) == 1
    assert listed[0]["id"] == run.id

    fetched = client.get(f"/runs/{run.id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run.id

    missing = client.get("/runs/does-not-exist")
    assert missing.status_code == 404
