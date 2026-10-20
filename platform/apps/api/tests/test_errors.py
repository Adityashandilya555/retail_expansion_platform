"""Error model (RFC 9457 problem+json) and request logs (#21)."""

import json
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import BaseModel

from zm_api.context import ClientCtx, Resolver
from zm_api.logs import JsonFormatter
from zm_api.main import create_app
from zm_api.registry import Client

from .conftest import PILOT_URL, bearer, token


class Body(BaseModel):
    name: str
    count: int


WEB = "https://zm-web.test"


@pytest.fixture
def api(rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def lookup(slug: str) -> Client | None:
        return rows.get(slug)

    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", WEB)
    app = create_app(resolver=Resolver(lookup))

    @app.get("/app/probe/task/{task_id}")
    async def task(task_id: str, ctx: ClientCtx) -> dict:
        return await ctx.operaton.get_task(task_id)

    @app.post("/app/probe/echo")
    async def echo(body: Body, ctx: ClientCtx) -> Body:
        return body

    @app.get("/app/probe/boom")
    async def boom(ctx: ClientCtx) -> None:
        raise RuntimeError("secret internals")

    return TestClient(app)


def request_lines(caplog: pytest.LogCaptureFixture) -> list[dict]:
    fmt = JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.name == "zm_api.request"]


@respx.mock
def test_operaton_500_is_502_problem_with_request_id(api: TestClient) -> None:
    respx.get(f"{PILOT_URL}/task/t1").respond(
        500, json={"type": "ProcessEngineException", "message": "db down"}
    )
    r = api.get("/app/probe/task/t1", headers=bearer(token()))
    assert r.status_code == 502
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["status"] == 502 and body["title"] == "Bad Gateway"
    assert body["request_id"] == r.headers["x-request-id"]
    assert "db down" not in r.text


@respx.mock
def test_operaton_timeout_is_502(api: TestClient) -> None:
    respx.get(f"{PILOT_URL}/task/t1").mock(side_effect=httpx.ConnectTimeout("slow"))
    assert api.get("/app/probe/task/t1", headers=bearer(token())).status_code == 502


@respx.mock
def test_operaton_404_is_our_404(api: TestClient) -> None:
    respx.get(f"{PILOT_URL}/task/gone").respond(
        404, json={"type": "InvalidRequestException", "message": "No task"}
    )
    r = api.get("/app/probe/task/gone", headers=bearer(token()))
    assert r.status_code == 404 and r.json()["title"] == "Not Found"


def test_validation_errors_are_422_with_json_pointers(api: TestClient) -> None:
    r = api.post("/app/probe/echo", json={"count": "many"}, headers=bearer(token()))
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    assert [e["path"] for e in r.json()["errors"]] == ["/name", "/count"]
    assert all(e["message"] for e in r.json()["errors"])


def test_unknown_route_is_404_problem(api: TestClient) -> None:
    r = api.get("/nope")
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"


def test_unhandled_error_is_500_problem_without_internals(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    r = api.get("/app/probe/boom", headers=bearer(token()))
    assert r.status_code == 500
    assert r.json()["request_id"] == r.headers["x-request-id"]
    assert "secret internals" not in r.text
    assert [line["status"] for line in request_lines(caplog)] == [500]


def test_one_json_log_line_per_request_with_client_and_request_id(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    r = api.post("/app/probe/echo", json={"name": "a", "count": 1}, headers=bearer(token()))
    assert r.status_code == 200
    lines = request_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert line["client"] == "pilot-local"
    assert line["request_id"] == r.headers["x-request-id"] and len(line["request_id"]) >= 16
    assert line["route"] == "/app/probe/echo"
    assert line["status"] == 200 and line["method"] == "POST"
    assert isinstance(line["ms"], float)


def test_incoming_request_id_is_kept_and_unsafe_ones_replaced(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert (
        api.get("/healthz", headers={"X-Request-Id": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )
    unsafe = api.get("/healthz", headers={"X-Request-Id": 'evil" injected'}).headers["x-request-id"]
    assert unsafe != 'evil" injected' and " " not in unsafe
    lines = request_lines(caplog)
    assert [(line["request_id"], line["client"], line["route"]) for line in lines] == [
        ("abc-123", None, "/healthz"),
        (unsafe, None, "/healthz"),
    ]


def test_failed_auth_is_logged_once_without_client(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    api.get("/app/probe/task/t1")
    assert [(line["status"], line["client"]) for line in request_lines(caplog)] == [(401, None)]


def test_a_crash_still_carries_cors_headers_so_the_browser_can_read_it(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    r = api.get("/app/probe/boom", headers={**bearer(token()), "Origin": WEB})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == WEB
    assert "x-request-id" in r.headers["access-control-expose-headers"].lower()
    assert r.json()["request_id"] == r.headers["x-request-id"]
    [line] = request_lines(caplog)
    assert line["status"] == 500 and "secret internals" in line["exc"]


def test_cors_is_outermost_and_answers_preflights_itself(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    r = api.options(
        "/app/probe/echo",
        headers={"Origin": WEB, "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == WEB
    assert request_lines(caplog) == []  # a preflight never reaches the app, so it isn't logged
    middleware = [m.cls.__name__ for m in api.app.user_middleware]  # type: ignore[attr-defined]
    assert middleware == ["CORSMiddleware", "RequestLogMiddleware", "CrashGuard"]  # outer first
