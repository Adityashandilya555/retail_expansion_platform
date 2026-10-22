"""Client resolver (#21): the client comes only from the JWT; cross-client ids are 404."""

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from zm_api import secrets
from zm_api.context import (
    ClientCtx,
    OwnerCtx,
    PlatformRegistry,
    Resolver,
    client_router,
    owner_router,
)
from zm_api.errors import NotFound, ServiceUnavailable
from zm_api.main import create_app
from zm_api.registry import Client

from .conftest import PILOT_URL, bearer, token

# Each client's own data: a site code exists only in its own client's database.
SITES = {"pilot-local": {"MX-0001"}, "qsr-local": {"QSR-0001"}}


class FakeRegistry:
    def __init__(self, rows: dict[str, Client]) -> None:
        self.rows = rows
        self.calls = 0

    async def __call__(self, slug: str) -> Client | None:
        self.calls += 1
        return self.rows.get(slug)


@pytest.fixture
def registry(rows: dict[str, Client]) -> FakeRegistry:
    return FakeRegistry(rows)


@pytest.fixture
def api(registry: FakeRegistry) -> TestClient:
    app = create_app(resolver=Resolver(registry))

    @app.get("/app/probe/sites/{code}")
    async def site(code: str, ctx: ClientCtx) -> dict[str, str]:
        if code not in SITES[ctx.client.slug]:
            raise NotFound(f"No site {code}")
        return {"code": code, "client": ctx.client.slug}

    @app.get("/owner/probe/me")
    async def me(owner: OwnerCtx) -> dict[str, str]:
        return {"owner": str(owner.owner_id)}

    app_routes, owner_routes = client_router(), owner_router()

    @app_routes.get("/probe/open")
    async def forgot_ctx() -> dict[str, bool]:
        return {"ok": True}

    @owner_routes.get("/probe/open")
    async def owner_forgot_ctx() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(app_routes)
    app.include_router(owner_routes)
    return TestClient(app)


def test_namespace_routers_need_their_token_even_without_a_ctx_param(api: TestClient) -> None:
    assert api.get("/app/probe/open").status_code == 401
    assert api.get("/app/probe/open", headers=bearer(token())).json() == {"ok": True}
    assert api.get("/owner/probe/open", headers=bearer(token())).status_code == 403
    assert api.get("/app/probe/open", headers=bearer(token(kind="owner"))).status_code == 403
    owner = token(kind="owner", client=None, sub="6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c")
    assert api.get("/owner/probe/open", headers=bearer(owner)).json() == {"ok": True}


def test_pilot_token_reading_a_qsr_site_gets_404_problem(api: TestClient) -> None:
    """Proves the client comes only from the token, using a probe over each client's own dict.
    It does not prove data isolation in the client databases: repeat it against the real
    `/app/sites` route (with pilot-local and qsr-local vaults) when #91 lands."""
    r = api.get("/app/probe/sites/QSR-0001", headers=bearer(token(client="pilot-local")))
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "No site QSR-0001",
        "request_id": r.headers["x-request-id"],
        "errors": None,
    }
    own = api.get("/app/probe/sites/QSR-0001", headers=bearer(token(client="qsr-local")))
    assert own.status_code == 200 and own.json()["client"] == "qsr-local"


def test_client_comes_from_the_token_not_the_request(api: TestClient) -> None:
    r = api.get(
        "/app/probe/sites/MX-0001?client=qsr-local",
        headers={**bearer(token(client="pilot-local")), "X-Client": "qsr-local"},
    )
    assert r.json() == {"code": "MX-0001", "client": "pilot-local"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic Zm9vOmJhcg=="},
        bearer("not-a-jwt"),
        bearer(token(exp=1)),
        bearer(token(kind="robot")),
        bearer(token(client="no-such-client")),
        bearer(token(client=None)),
    ],
    ids=["none", "basic", "garbage", "expired", "unknown-kind", "unknown-client", "no-client"],
)
def test_bad_tokens_are_401_problem(api: TestClient, headers: dict[str, str]) -> None:
    r = api.get("/app/probe/sites/MX-0001", headers=headers)
    assert r.status_code == 401
    assert r.headers["content-type"] == "application/problem+json"
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_signed_with_another_secret_is_401(api: TestClient) -> None:
    forged = jwt.encode({"kind": "client", "client": "pilot-local", "sub": "x"}, "other", "HS256")
    assert api.get("/app/probe/sites/MX-0001", headers=bearer(forged)).status_code == 401


def test_client_not_ready_is_503(registry: FakeRegistry, api: TestClient) -> None:
    registry.rows["pilot-local"] = replace(registry.rows["pilot-local"], status="provisioning")
    r = api.get("/app/probe/sites/MX-0001", headers=bearer(token()))
    assert r.status_code == 503
    assert r.json()["title"] == "Service Unavailable"


def test_owner_context_needs_an_owner_token(api: TestClient) -> None:
    owner = token(kind="owner", client=None, sub="6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c")
    assert api.get("/owner/probe/me", headers=bearer(owner)).json() == {
        "owner": "6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c"
    }
    assert api.get("/owner/probe/me", headers=bearer(token())).status_code == 403


def test_a_valid_token_on_the_other_surface_is_403_problem(api: TestClient) -> None:
    owner = token(kind="owner", client=None, sub="6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c")
    r = api.get("/app/probe/sites/MX-0001", headers=bearer(owner))
    assert r.status_code == 403 and r.headers["content-type"] == "application/problem+json"
    assert "www-authenticate" not in r.headers


def resolve(resolver: Resolver, slug: str):
    return asyncio.run(resolver.resolve(slug))


def test_pilot_resolves_to_pilot_operaton_and_database(registry: FakeRegistry) -> None:
    resolver = Resolver(registry)
    ctx = resolve(resolver, "pilot-local")
    assert ctx.client.slug == "pilot-local"
    assert str(ctx.operaton.base_url) == PILOT_URL + "/"
    assert ctx.operaton.http.timeout == httpx.Timeout(10.0)
    with respx.mock:
        route = respx.get(f"{PILOT_URL}/task/t1").respond(200, json={"id": "t1"})
        asyncio.run(ctx.operaton.get_task("t1"))
    expected = "Basic " + base64.b64encode(b"zm-service:pilot-pw").decode()
    assert route.calls.last.request.headers["authorization"] == expected
    url = ctx.db.url
    assert (url.drivername, url.username, url.host, url.database) == (
        "postgresql+asyncpg",
        "zm_api",
        "pilot-db",
        "postgres",
    )
    assert ctx.db.pool.size() == 5
    assert ctx.bucket == "files"
    qsr = resolve(resolver, "qsr-local")
    assert qsr.db.url.host == "qsr-db" and str(qsr.operaton.base_url).startswith(
        "http://operaton-qsr"
    )


def test_registry_rows_are_cached_for_60_seconds(registry: FakeRegistry) -> None:
    now = [1000.0]
    resolver = Resolver(registry, clock=lambda: now[0])
    first = resolve(resolver, "pilot-local")
    now[0] += 59
    assert resolve(resolver, "pilot-local") is first
    assert registry.calls == 1
    now[0] += 2
    resolve(resolver, "pilot-local")
    assert registry.calls == 2


def test_a_changed_row_gets_new_resources(registry: FakeRegistry) -> None:
    now = [1000.0]
    resolver = Resolver(registry, clock=lambda: now[0])
    first = resolve(resolver, "pilot-local")
    row = registry.rows["pilot-local"]
    registry.rows["pilot-local"] = replace(
        row, operaton_url="http://moved.test/engine-rest", updated_at=row.updated_at + timedelta(1)
    )
    now[0] += 61
    second = resolve(resolver, "pilot-local")
    assert second.db is not first.db
    assert str(second.operaton.base_url) == "http://moved.test/engine-rest/"
    assert first.operaton.http.is_closed


def test_clients_beyond_the_cap_are_evicted_and_closed(registry: FakeRegistry) -> None:
    resolver = Resolver(registry, max_clients=1)
    pilot = resolve(resolver, "pilot-local")
    resolve(resolver, "qsr-local")
    assert pilot.operaton.http.is_closed
    again = resolve(resolver, "pilot-local")
    assert again.db is not pilot.db


def test_unknown_client_resolves_to_none(registry: FakeRegistry) -> None:
    assert resolve(Resolver(registry), "nobody") is None


def test_a_row_change_that_isnt_a_resource_keeps_the_live_clients(registry: FakeRegistry) -> None:
    now = [1000.0]
    resolver = Resolver(registry, clock=lambda: now[0])
    first = resolve(resolver, "pilot-local")
    row = registry.rows["pilot-local"]
    registry.rows["pilot-local"] = replace(
        row, name="Pilot (renamed)", updated_at=row.updated_at + timedelta(1)
    )
    now[0] += 61
    second = resolve(resolver, "pilot-local")
    assert second.client.name == "Pilot (renamed)"
    assert second.db is first.db and second.operaton is first.operaton
    assert not first.operaton.http.is_closed


def test_a_suspended_client_is_refused_even_while_its_resources_are_live(
    registry: FakeRegistry,
) -> None:
    now = [1000.0]
    resolver = Resolver(registry, clock=lambda: now[0])
    resolve(resolver, "pilot-local")
    registry.rows["pilot-local"] = replace(registry.rows["pilot-local"], status="suspended")
    now[0] += 61
    with pytest.raises(ServiceUnavailable):
        resolve(resolver, "pilot-local")


def test_a_hanging_registry_answers_503_without_blocking_other_clients(
    registry: FakeRegistry,
) -> None:
    hang = asyncio.Event()

    async def lookup(slug: str) -> Client | None:
        if slug == "qsr-local":
            await hang.wait()  # never set: the control-plane database stopped answering
        return await registry(slug)

    async def go() -> None:
        resolver = Resolver(lookup, lookup_timeout=0.2)
        stuck = asyncio.create_task(resolver.resolve("qsr-local"))
        await asyncio.sleep(0)
        async with asyncio.timeout(0.1):  # pilot doesn't wait for qsr's registry query
            assert (await resolver.resolve("pilot-local")) is not None
        with pytest.raises(ServiceUnavailable):
            await stuck
        await resolver.aclose()

    asyncio.run(go())


@pytest.mark.parametrize(
    "dsn",
    [
        "postgres://zm_api:pw@db.example.com:5432/postgres",
        "postgresql://zm_api:pw@db.example.com:5432/postgres",
        "postgresql+psycopg://zm_api:pw@db.example.com:5432/postgres",
    ],
)
def test_any_postgres_dsn_runs_on_asyncpg(registry: FakeRegistry, dsn: str) -> None:
    row = registry.rows["pilot-local"]
    sealed = secrets.seal({"zm_api_dsn": dsn, "service_account_password": "pw"})
    registry.rows["pilot-local"] = replace(row, secrets=sealed)
    url = resolve(Resolver(registry), "pilot-local").db.url
    assert (url.drivername, url.host, url.username) == (
        "postgresql+asyncpg",
        "db.example.com",
        "zm_api",
    )


def test_aclose_closes_the_registry_too(registry: FakeRegistry) -> None:
    closed: list[bool] = []

    class ClosingRegistry(FakeRegistry):
        async def aclose(self) -> None:
            closed.append(True)

    resolver = Resolver(ClosingRegistry(registry.rows))
    ctx = resolve(resolver, "pilot-local")
    asyncio.run(resolver.aclose())
    assert closed == [True] and ctx.operaton.http.is_closed
    asyncio.run(PlatformRegistry().aclose())  # never used: nothing to dispose, no error
