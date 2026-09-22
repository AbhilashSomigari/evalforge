from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import evalforge.api as api_module
from evalforge import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUN_DIR", tmp_path)
    monkeypatch.delenv(db.DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv(api_module.API_KEY_ENV, raising=False)
    monkeypatch.delenv(api_module.RATE_LIMIT_ENV, raising=False)
    monkeypatch.setattr(api_module, "_request_times", defaultdict(list))
    return TestClient(api_module.app)


def test_open_by_default(client):
    assert client.get("/runs").status_code == 200


def test_health_never_requires_auth(client, monkeypatch):
    monkeypatch.setenv(api_module.API_KEY_ENV, "secret-token")
    assert client.get("/health").status_code == 200


def test_rejects_missing_key_when_configured(client, monkeypatch):
    monkeypatch.setenv(api_module.API_KEY_ENV, "secret-token")
    response = client.get("/runs")
    assert response.status_code == 401


def test_rejects_wrong_key(client, monkeypatch):
    monkeypatch.setenv(api_module.API_KEY_ENV, "secret-token")
    response = client.get("/runs", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_accepts_correct_key(client, monkeypatch):
    monkeypatch.setenv(api_module.API_KEY_ENV, "secret-token")
    response = client.get("/runs", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200


def test_rate_limit_disabled_by_default(client):
    for _ in range(10):
        assert client.get("/runs").status_code == 200


def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    monkeypatch.setenv(api_module.RATE_LIMIT_ENV, "2")
    assert client.get("/runs").status_code == 200
    assert client.get("/runs").status_code == 200
    assert client.get("/runs").status_code == 429


def test_rate_limit_does_not_apply_to_health(client, monkeypatch):
    monkeypatch.setenv(api_module.RATE_LIMIT_ENV, "1")
    client.get("/runs")
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_request_id_is_generated_and_echoed(client):
    response = client.get("/runs")
    assert response.headers.get("X-Request-ID")


def test_request_id_from_client_is_preserved(client):
    response = client.get("/runs", headers={"X-Request-ID": "trace-abc123"})
    assert response.headers["X-Request-ID"] == "trace-abc123"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("https://a.example", ["https://a.example"]),
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ("  ,  ", []),
    ],
)
def test_parse_cors_origins(raw, expected):
    assert api_module.parse_cors_origins(raw) == expected
