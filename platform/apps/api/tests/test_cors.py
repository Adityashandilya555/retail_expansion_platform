import pytest
from fastapi.testclient import TestClient

from zm_api import settings
from zm_api.main import create_app

WEB = "https://zm-web.vercel.app"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f" {WEB} , https://zm-console.vercel.app,")
    return TestClient(create_app())


def preflight(client: TestClient, origin: str):
    return client.options(
        "/healthz",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_origins_are_split_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f" {WEB} ,,https://zm-console.vercel.app ")
    assert settings.cors_allowed_origins() == [WEB, "https://zm-console.vercel.app"]


def test_no_origins_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert settings.cors_allowed_origins() == []


def test_preflight_from_allowed_origin(client: TestClient) -> None:
    response = preflight(client, WEB)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB
    assert response.headers["access-control-allow-credentials"] == "true"


def test_preflight_from_unknown_origin_is_refused(client: TestClient) -> None:
    response = preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_from_allowed_origin(client: TestClient) -> None:
    response = client.get("/healthz", headers={"Origin": WEB})
    assert response.json() == {"ok": True}
    assert response.headers["access-control-allow-origin"] == WEB
