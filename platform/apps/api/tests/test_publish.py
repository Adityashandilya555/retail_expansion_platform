"""Publish against the local stack (#23): `pytest -m integration` after scripts/dev-up.sh.

Two clients, each with its own vault and engine, like production:
- pilot: the Supabase CLI vault (:54322) and operaton-pilot;
- qsr: the pg-iso vault (:5434) and operaton-iso (:8081).
Registry rows get random slugs, so deployments never collide with other runs. The vaults are shared
by every test on this stack, so each test empties app.releases and app.drafts before and after.

Never send a wrong password as `zm-service` here: Operaton delays every later login (see
test_context_stack.py).
"""

import argparse
import asyncio
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import secrets
from zm_api.context import ClientContext, PlatformRegistry, Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonError
from zm_api.publish import RELEASE_RESOURCE, publish
from zm_api.releases import release_for_task, seed_draft

pytestmark = pytest.mark.integration

PLATFORM_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:{os.environ.get('PG_PLATFORM_PORT', 5433)}"
    "/postgres"
)
ZM_API_PASSWORD = os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local")
SERVICE_PASSWORD = os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only")
PG_ISO = int(os.environ.get("PG_ISO_PORT", "5434"))
VAULTS = {"pilot": 54322, "qsr": PG_ISO}
ENGINES = {
    "pilot": f"http://localhost:{os.environ.get('OPERATON_PILOT_PORT', 8080)}/engine-rest",
    "qsr": f"http://localhost:{os.environ.get('OPERATON_ISO_PORT', 8081)}/engine-rest",
    "down": "http://localhost:9/engine-rest",  # nothing listens on the discard port
}
JWT_SECRET = "integration-jwt-secret-0123456789abcdef0123456789abcd"
INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@dataclass
class Clients:
    ids: dict[str, uuid.UUID]
    slugs: dict[str, str]
    owner: uuid.UUID


class Stack:
    """The resolver and platform pool for one test, opened and closed in that test's event loop."""

    def __init__(self, clients: Clients) -> None:
        self.clients = clients
        self.registry = PlatformRegistry()
        self.resolver = Resolver(self.registry)

    async def ctx(self, name: str) -> ClientContext:
        ctx = await self.resolver.resolve(self.clients.slugs[name])
        assert ctx is not None
        return ctx

    def platform(self):  # noqa: ANN201
        return self.registry.engine()

    async def __aenter__(self) -> "Stack":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.resolver.aclose()  # also disposes the platform pool


def vault_sql(name: str, statement: str, *params: Any) -> list[tuple]:
    """As the vault superuser (tests only): reset and inspect what zm_api wrote."""
    dsn = f"postgresql://postgres:postgres@localhost:{VAULTS[name]}/postgres"
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else []


async def platform_sql(statement: str, **params: Any) -> list[dict]:
    engine = create_async_engine(PLATFORM_URL)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params)
            return [dict(r) for r in result.mappings()] if result.returns_rows else []
    finally:
        await engine.dispose()


async def register(name: str, vault: str) -> tuple[uuid.UUID, str]:
    slug = f"{name}-{uuid.uuid4().hex[:8]}"
    dsn = f"postgresql://zm_api:{ZM_API_PASSWORD}@localhost:{VAULTS[vault]}/postgres"
    sealed = secrets.seal({"zm_api_dsn": dsn, "service_account_password": SERVICE_PASSWORD})
    rows = await platform_sql(
        "insert into platform.clients (slug, name, workspace_code, template_key, status,"
        " operaton_url, bucket, secrets) values (:slug, :name, :code, 'matrix-retail', 'ready',"
        " :url, 'files', :sealed) returning id",
        slug=slug,
        name=name.title(),
        code=slug.upper(),
        url=ENGINES[name],
        sealed=sealed,
    )
    return rows[0]["id"], slug


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clients]:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", PLATFORM_URL)
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")
    for vault in VAULTS:
        vault_sql(vault, "delete from app.releases")
        vault_sql(vault, "delete from app.drafts")

    async def setup() -> Clients:
        ids, slugs = {}, {}
        for name, vault in (("pilot", "pilot"), ("qsr", "qsr"), ("down", "pilot")):
            ids[name], slugs[name] = await register(name, vault)
        owner = (
            await platform_sql(
                "insert into platform.owners (email, name, password_hash) values"
                " (:e, 'Owner', 'not-a-hash') returning id",
                e=f"owner-{uuid.uuid4().hex[:8]}@example.com",
            )
        )[0]["id"]
        return Clients(ids, slugs, owner)

    c = asyncio.run(setup())
    yield c

    for vault in VAULTS:  # other tests on this stack expect no release (e.g. the sign-in look)
        vault_sql(vault, "delete from app.releases")
        vault_sql(vault, "delete from app.drafts")
    for name in ("pilot", "qsr"):
        with httpx.Client(base_url=ENGINES[name], auth=("zm-service", SERVICE_PASSWORD)) as op:
            found = op.get("deployment", params={"name": f"{c.slugs[name]}-release"}).json()
            for d in found:
                op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def deployments(engine: str, name: str) -> list[dict]:
    with httpx.Client(base_url=ENGINES[engine], auth=("zm-service", SERVICE_PASSWORD)) as op:
        return op.get("deployment", params={"name": name}).json()


def definitions(engine: str, deployment_id: str) -> list[str]:
    with httpx.Client(base_url=ENGINES[engine], auth=("zm-service", SERVICE_PASSWORD)) as op:
        found = op.get("process-definition", params={"deploymentId": deployment_id}).json()
    return sorted(d["key"] for d in found)


def test_first_publish_goes_to_the_clients_own_engine_and_vault(clients: Clients) -> None:
    async def go() -> Any:
        async with Stack(clients) as stack:
            ctx = await stack.ctx("pilot")
            await seed_draft(ctx, "matrix-retail")
            return await publish(ctx, stack.platform(), clients.owner)

    result = asyncio.run(go())
    assert (result.version, result.created) == (1, True)
    name = f"{clients.slugs['pilot']}-release"

    # pilot's engine has the deployment with every process; qsr's engine has nothing of it
    assert [d["id"] for d in deployments("pilot", name)] == [result.deployment_id]
    assert deployments("qsr", name) == []
    keys = definitions("pilot", result.deployment_id)
    assert len(keys) == 11  # main process + 10 stages
    assert "zm_" + clients.slugs["pilot"].replace("-", "_") + "__store_launch" in keys

    # the level groups exist in pilot's engine
    with httpx.Client(base_url=ENGINES["pilot"], auth=("zm-service", SERVICE_PASSWORD)) as op:
        assert op.get("group/legal__L1").status_code == 200
        assert op.get("group/management__L1").status_code == 200

    # the release is in pilot's vault, with forms and ui, and indexed in the control plane
    rows = vault_sql(
        "pilot",
        "select version, active, forms ? 'start:store_launch', ui->>'theme', deployment_id"
        " from app.releases",
    )
    assert rows == [(1, True, True, "quartz", result.deployment_id)]
    assert vault_sql("qsr", "select count(*) from app.releases") == [(0,)]
    index = asyncio.run(
        platform_sql(
            "select version, deployment_id, published_by from platform.releases"
            " where client_id = :c",
            c=clients.ids["pilot"],
        )
    )
    assert index == [
        {"version": 1, "deployment_id": result.deployment_id, "published_by": clients.owner}
    ]


def test_republishing_an_unchanged_draft_makes_no_new_version(clients: Clients) -> None:
    async def go() -> tuple[Any, Any]:
        async with Stack(clients) as stack:
            ctx = await stack.ctx("pilot")
            await seed_draft(ctx, "matrix-retail")
            first = await publish(ctx, stack.platform(), clients.owner)
            return first, await publish(ctx, stack.platform(), clients.owner)

    first, again = asyncio.run(go())
    assert again.created is False
    assert (again.version, again.deployment_id) == (first.version, first.deployment_id)
    assert len(deployments("pilot", f"{clients.slugs['pilot']}-release")) == 1
    assert vault_sql("pilot", "select count(*) from app.releases") == [(1,)]


def test_a_form_change_is_v2_and_running_cases_keep_v1_forms(clients: Clients) -> None:
    async def go() -> tuple[Any, Any, Any, Any]:
        async with Stack(clients) as stack:
            return await body(stack)

    async def body(stack: Stack) -> tuple[Any, Any, Any, Any]:
        ctx = await stack.ctx("pilot")
        platform = stack.platform()
        await seed_draft(ctx, "matrix-retail")
        v1 = await publish(ctx, platform, clients.owner)

        # a case started on v1: its first task belongs to v1's deployment
        key = "zm_" + ctx.client.slug.replace("-", "_") + "__store_launch"
        business_key = f"MX-{uuid.uuid4().hex[:6]}"
        await ctx.operaton.start_by_key(
            key, business_key=business_key, values={"initiator_groups": ",bd__L1,"}
        )
        task = (await ctx.operaton.list_tasks({"processInstanceBusinessKey": business_key}))[0]

        vault_sql(
            "pilot",
            "update app.drafts set workflow = jsonb_set(workflow,"
            " '{fields,site_name,label}', '\"Site name (as on the lease)\"')",
        )
        v2 = await publish(ctx, platform, clients.owner)
        old = await release_for_task(ctx, task["processDefinitionId"])
        return v1, v2, old, task

    v1, v2, old, task = asyncio.run(go())
    assert (v2.version, v2.created) == (2, True)
    assert v2.deployment_id != v1.deployment_id
    # v2 redeploys every BPMN resource (deploy-changed-only=false), not just what changed
    assert definitions("pilot", v2.deployment_id) == definitions("pilot", v1.deployment_id)
    assert old.version == 1 and old.deployment_id == v1.deployment_id
    title = old.forms["start:store_launch"]["schema"]["properties"]["site_name"]["title"]
    assert title == "Site name"
    assert vault_sql("pilot", "select version from app.releases where active") == [(2,)]
    new_title = vault_sql(
        "pilot",
        "select forms->'start:store_launch'->'schema'->'properties'->'site_name'->>'title'"
        " from app.releases where version = 2",
    )
    assert new_title == [("Site name (as on the lease)",)]
    with httpx.Client(base_url=ENGINES["pilot"], auth=("zm-service", SERVICE_PASSWORD)) as op:
        resources = op.get(f"deployment/{v2.deployment_id}/resources").json()
    assert RELEASE_RESOURCE in {r["name"] for r in resources}
    assert task["formKey"].startswith("zm:")


def test_operaton_down_writes_nothing(clients: Clients) -> None:
    async def go() -> None:
        async with Stack(clients) as stack:
            ctx = await stack.ctx("down")
            await seed_draft(ctx, "matrix-retail")
            with pytest.raises(OperatonError):
                await publish(ctx, stack.platform(), clients.owner)

    asyncio.run(go())
    assert vault_sql("pilot", "select count(*) from app.releases") == [(0,)]
    index = asyncio.run(
        platform_sql(
            "select count(*) as n from platform.releases where client_id = :c",
            c=clients.ids["down"],
        )
    )
    assert index == [{"n": 0}]


def test_concurrent_publishes_make_one_release(clients: Clients) -> None:
    async def go() -> list[Any]:
        async with Stack(clients) as stack:
            ctx = await stack.ctx("qsr")
            await seed_draft(ctx, "qsr")
            platform = stack.platform()
            return list(
                await asyncio.gather(
                    publish(ctx, platform, clients.owner), publish(ctx, platform, clients.owner)
                )
            )

    results = asyncio.run(go())
    assert {r.version for r in results} == {1}
    assert {r.deployment_id for r in results} == {results[0].deployment_id}
    assert sorted(r.created for r in results) == [False, True]
    assert vault_sql("qsr", "select count(*) from app.releases") == [(1,)]


def test_owner_publishes_through_the_api(clients: Clients) -> None:
    asyncio.run(_seed(clients, "pilot"))
    owner_token = jwt.encode(
        {"sub": str(clients.owner), "kind": "owner", "exp": 4102444800},
        JWT_SECRET,
        algorithm="HS256",
    )
    with TestClient(create_app()) as api:
        response = api.post(
            f"/owner/clients/{clients.ids['pilot']}/publish",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["version"], body["created"]) == (1, True)


async def _seed(clients: Clients, name: str) -> None:
    async with Stack(clients) as stack:
        await seed_draft(await stack.ctx(name), "matrix-retail")
