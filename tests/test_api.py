from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import evalforge.api as api_module
from evalforge.models import EvalRun, RunSummary
from evalforge.storage import save_run


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUN_DIR", tmp_path)
    return TestClient(api_module.app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_runs_empty(client):
    response = client.get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_and_get_run(client, tmp_path):
    run = EvalRun(suite_name="s", agent_name="a", results=[], summary=RunSummary(task_success=1.0))
    save_run(run, tmp_path / "run1.json")

    listed = client.get("/runs").json()
    assert len(listed) == 1
    assert listed[0]["suite"] == "s"

    fetched = client.get(f"/runs/{listed[0]['file']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run.id


def test_list_runs_skips_unreadable_files(client, tmp_path):
    (tmp_path / "corrupt.json").write_text("not json")
    response = client.get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_get_run_404(client):
    response = client.get("/runs/does-not-exist.json")
    assert response.status_code == 404


def test_get_run_rejects_path_traversal(client, tmp_path):
    secret = tmp_path.parent / "secret.json"
    secret.write_text("{}")
    response = client.get("/runs/..%2Fsecret.json")
    assert response.status_code == 404
