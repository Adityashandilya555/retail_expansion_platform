"""Owner console API (#29): create clients, queue their provisioning, retry a failed step.

Unit tests run anywhere; the `integration` ones use the platform database from
scripts/dev-up.sh (pg-platform only: no engine is needed). Every run makes its own clients.
"""

import argparse
import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import clients, templates
from zm_api.main import create_app
from zm_api.users import create_owner

from .conftest import bearer, token

# --- unit -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Matrix Retail", "matrix-retail"),
        ("  Burger King (India) ", "burger-king-india"),
        ("Café Coffee Day", "caf-coffee-day"),
        ("x" * 60, "x" * 40),
        ("a" * 39 + " b", "a" * 39),
    ],
)
def test_the_slug_comes_from_the_name(name: str, slug: str) -> None:
    assert clients.slugify(name) == slug


def test_a_name_without_letters_or_digits_has_no_slug() -> None:
    assert clients.slugify("!!! ???") == ""


def test_the_suggested_workspace_code_is_the_slug_plus_four_random_characters() -> None:
    codes = {clients.suggest_code("matrix-retail") for _ in range(20)}
    assert all(re.fullmatch(r"MATRIX-RETAIL-[A-Z0-9]{4}", c) for c in codes)
    assert len(codes) > 1


def test_every_owner_route_refuses_a_client_users_token() -> None:
    app = create_app()
    api = TestClient(app)
    owner_routes = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/owner/") and not path.startswith("/owner/auth/")
        for method in operations
    ]
    assert ("POST", "/owner/clients") in owner_routes
    assert ("POST", "/owner/clients/{client_id}/provisioning/retry") in owner_routes
    for method, path in owner_routes:
        url = path.replace("{client_id}", str(uuid.uuid4()))
        response = api.request(method, url, headers=bearer(token("client")), json={})
        assert response.status_code == 403, (method, path)
        assert api.request(method, url, json={}).status_code == 401, (method, path)


# --- integration: the platform database -----------------------------------------------------

PLATFORM_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:{os.environ.get('PG_PLATFORM_PORT', 5433)}"
    "/postgres"
)
INI = Path(__file__).resolve().parents[1] / "alembic.ini"
RUN = uuid.uuid4().hex[:6]


async def sql(statement: str, **params: Any) -> list[dict[str, Any]]:
    engine = create_async_engine(PLATFORM_URL)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params)
            return [dict(r) for r in result.mappings()] if result.returns_rows else []
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def owner() -> Iterator[str]:
    """An owner's bearer token; the platform schema migrated and the templates synced."""
    saved = dict(os.environ)
    os.environ["PLATFORM_DATABASE_URL"] = PLATFORM_URL
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")

    async def setup() -> uuid.UUID:
        from zm_api.context import PlatformRegistry

        registry = PlatformRegistry()
        try:
            async with registry.engine().begin() as conn:
                await templates.sync(conn, templates.load_templates())
            return await create_owner(registry, f"console+{RUN}@zm.demo", "Console", "x" * 16)
        finally:
            await registry.aclose()

    owner_id = asyncio.run(setup())
    yield token("owner", client=None, sub=str(owner_id))  # signed with conftest's JWT_SECRET
    asyncio.run(sql("delete from platform.clients where slug like :p", p=f"%{RUN}%"))
    asyncio.run(sql("delete from platform.owners where id = :id", id=owner_id))
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def api(owner: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        client.headers["Authorization"] = f"Bearer {owner}"
        yield client


def create(api: TestClient, **body: Any) -> Any:
    return api.post("/owner/clients", json={"template_key": "matrix-retail", **body})


@pytest.mark.integration
def test_templates_come_from_the_platform_table(api: TestClient) -> None:
    found = {t["key"]: t["name"] for t in api.get("/owner/templates").json()}
    assert found["matrix-retail"] == templates.TEMPLATE_NAMES["matrix-retail"]
    assert "qsr" in found


@pytest.mark.integration
def test_creating_matrix_retail_queues_its_provisioning(api: TestClient) -> None:
    response = create(api, name=f"Matrix Retail {RUN}")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == f"matrix-retail-{RUN}"
    assert re.fullmatch(rf"MATRIX-RETAIL-{RUN.upper()}-[A-Z0-9]{{4}}", body["workspace_code"])
    assert (body["status"], body["template_key"], body["last_release"]) == (
        "provisioning",
        "matrix-retail",
        None,
    )
    assert [(s["seq"], s["step"], s["status"]) for s in body["steps"]] == [
        (i, step, "pending") for i, step in enumerate(clients.STEPS, start=1)
    ]
    assert body["releases"] == []

    rows = asyncio.run(
        sql(
            "select c.created_by, count(s.*) as steps from platform.clients c"
            " join platform.provisioning_steps s on s.client_id = c.id where c.id = :id"
            " group by c.created_by",
            id=body["id"],
        )
    )
    assert rows[0]["steps"] == len(clients.STEPS) and rows[0]["created_by"] is not None
    listed = {c["id"]: c for c in api.get("/owner/clients").json()}
    assert listed[body["id"]]["workspace_code"] == body["workspace_code"]
    assert "steps" not in listed[body["id"]]


@pytest.mark.integration
def test_a_duplicate_workspace_code_or_slug_is_409_on_that_field(api: TestClient) -> None:
    code = f"DUP-{RUN}".upper()
    assert create(api, name=f"First {RUN}", workspace_code=code).status_code == 201

    taken = create(api, name=f"Second {RUN}", workspace_code=code.lower())  # citext
    assert taken.status_code == 409
    assert taken.json()["errors"] == [
        {"path": "/workspace_code", "message": "This workspace code is already taken"}
    ]
    slug = create(api, name=f"First {RUN}")
    assert slug.status_code == 409
    assert slug.json()["errors"][0]["path"] == "/slug"
    # nothing half-written: still one client with that slug
    count = "select count(*) as n from platform.clients where slug = :s"
    assert asyncio.run(sql(count, s=f"first-{RUN}"))[0]["n"] == 1


@pytest.mark.integration
def test_bad_input_is_422_on_the_field(api: TestClient) -> None:
    unknown = create(api, name=f"Third {RUN}", template_key="nope")
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["path"] == "/template_key"
    no_slug = create(api, name="!!!")
    assert no_slug.status_code == 422 and no_slug.json()["errors"][0]["path"] == "/slug"
    bad = create(api, name="Fine", slug="Not A Slug", workspace_code="no spaces")
    assert bad.status_code == 422
    assert {e["path"] for e in bad.json()["errors"]} == {"/slug", "/workspace_code"}


@pytest.mark.integration
def test_retry_requeues_only_the_failed_step(api: TestClient) -> None:
    client = create(api, name=f"Retry {RUN}").json()
    asyncio.run(
        sql(
            "update platform.provisioning_steps set status = case"
            " when seq < 3 then 'done' when seq = 3 then 'failed' else 'pending' end"
            "::platform.step_status, attempts = case when seq = 3 then 5 else attempts end,"
            " last_error = case when seq = 3 then 'PATCH postgrest: 500' end"
            " where client_id = :id",
            id=client["id"],
        )
    )
    failed = "update platform.clients set status = 'failed' where id = :id"
    asyncio.run(sql(failed, id=client["id"]))
    before = {s["step"]: s for s in api.get(f"/owner/clients/{client['id']}").json()["steps"]}
    assert before["data_api_off"]["last_error"] == "PATCH postgrest: 500"

    response = api.post(f"/owner/clients/{client['id']}/provisioning/retry")
    assert response.status_code == 200, response.text
    after = {s["step"]: s for s in response.json()["steps"]}
    assert after["data_api_off"] | {"updated_at": None} == {
        **before["data_api_off"],
        "status": "pending",
        "attempts": 0,
        "last_error": None,
        "updated_at": None,
    }
    for step in clients.STEPS:
        if step != "data_api_off":
            assert after[step] == before[step]
    assert response.json()["status"] == "provisioning"

    nothing = api.post(f"/owner/clients/{client['id']}/provisioning/retry")
    assert nothing.status_code == 409


@pytest.mark.integration
def test_a_needs_human_step_is_retried_too_and_shows_its_click_path(api: TestClient) -> None:
    client = create(api, name=f"Human {RUN}").json()
    path = "Dashboard → zm-x → Integrations → Data API → off"
    asyncio.run(
        sql(
            "update platform.provisioning_steps set status = 'needs_human', last_error = :p"
            " where client_id = :id and step = 'data_api_off'",
            id=client["id"],
            p=path,
        )
    )
    steps = api.get(f"/owner/clients/{client['id']}").json()["steps"]
    assert [s["last_error"] for s in steps if s["status"] == "needs_human"] == [path]
    after = api.post(f"/owner/clients/{client['id']}/provisioning/retry").json()["steps"]
    assert [s["status"] for s in after if s["step"] == "data_api_off"] == ["pending"]


@pytest.mark.integration
def test_ready_clients_show_their_releases_newest_first(api: TestClient) -> None:
    client = create(api, name=f"Ready {RUN}").json()
    for v in (1, 2):
        asyncio.run(
            sql(
                "insert into platform.releases (client_id, version, deployment_id,"
                " workflow_sha256) values (:id, :v, :d, 'x')",
                id=client["id"],
                v=v,
                d=f"dep-{v}",
            )
        )
    detail = api.get(f"/owner/clients/{client['id']}").json()
    assert [(r["version"], r["deployment_id"]) for r in detail["releases"]] == [
        (2, "dep-2"),
        (1, "dep-1"),
    ]
    listed = {c["id"]: c for c in api.get("/owner/clients").json()}
    assert listed[client["id"]]["last_release"] == 2


@pytest.mark.integration
def test_an_unknown_client_is_404(api: TestClient) -> None:
    assert api.get(f"/owner/clients/{uuid.uuid4()}").status_code == 404
    assert api.post(f"/owner/clients/{uuid.uuid4()}/provisioning/retry").status_code == 404
