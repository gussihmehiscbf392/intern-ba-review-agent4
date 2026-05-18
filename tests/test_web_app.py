from __future__ import annotations

from fastapi.testclient import TestClient

from review_agent.web_app import app


def test_index_is_public_when_password_is_not_configured(monkeypatch):
    monkeypatch.delenv("REVIEW_APP_PASSWORD", raising=False)

    response = TestClient(app).get("/")

    assert response.status_code == 200


def test_index_requires_basic_auth_when_password_is_configured(monkeypatch):
    monkeypatch.setenv("REVIEW_APP_USER", "mentor")
    monkeypatch.setenv("REVIEW_APP_PASSWORD", "secret")

    response = TestClient(app).get("/")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_index_accepts_valid_basic_auth(monkeypatch):
    monkeypatch.setenv("REVIEW_APP_USER", "mentor")
    monkeypatch.setenv("REVIEW_APP_PASSWORD", "secret")

    response = TestClient(app).get("/", auth=("mentor", "secret"))

    assert response.status_code == 200
