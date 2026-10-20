"""Publish pieces without the stack (#23): the release resource, blocks, the endpoint's guards,
draft seeding and the sign-in look. The full flow runs in test_publish.py (`-m integration`)."""

import asyncio
import copy
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from zm_api import auth, publish, releases
from zm_api.context import Resolver
from zm_api.errors import NotFound, UnprocessableContent
from zm_api.main import create_app
from zm_api.registry import Client
from zm_api.templates import load_templates
from zm_compiler import compile
from zm_schema import migrate

from .conftest import bearer, token


def matrix_retail() -> dict[str, Any]:
    return copy.deepcopy(next(t.workflow for t in load_templates() if t.key == "matrix-retail"))


def test_release_json_is_stable_and_changes_with_a_form_label() -> None:
    doc = matrix_retail()
    first = publish.release_json(compile(migrate(doc), "pilot-local"))
    assert first == publish.release_json(compile(migrate(copy.deepcopy(doc)), "pilot-local"))
    doc["fields"]["site_name"]["label"] = "Site name (as on the lease)"
    changed = compile(migrate(doc), "pilot-local")
    assert publish.release_json(changed) != first
    body = json.loads(first)
    assert set(body) == {"digest", "forms", "ui", "routing_vars", "process_keys"}
    # the BPMN itself doesn't change with a label: zm-release.json is what makes a new deployment
    assert changed.bpmn == compile(migrate(matrix_retail()), "pilot-local").bpmn


def test_blocks_used_lists_each_placed_block_once() -> None:
    ui = compile(migrate(matrix_retail()), "pilot-local").ui
    assert publish.blocks_used(ui) == {
        "case_fields": None,
        "case_header": None,
        "case_table": None,
        "case_tasks": None,
        "task_inbox": None,
    }
    assert publish.blocks_used({}) == {}


def test_an_invalid_draft_is_422() -> None:
    with pytest.raises(UnprocessableContent, match="can't be published"):
        publish._compile("pilot-local", {"schema_version": 2})
    broken = matrix_retail()
    broken["flows"][0]["stages"][0]["after"] = ["nowhere"]
    with pytest.raises(UnprocessableContent):
        publish._compile("pilot-local", broken)


class FakeRegistry:
    def __init__(self, rows: dict[str, Client]) -> None:
        self.rows = rows

    async def __call__(self, slug: str) -> Client | None:
        return self.rows.get(slug)


class FakePlatform:
    """Stands in for app.state.platform; `get_client` is patched, so the connection is unused."""

    def engine(self) -> "FakePlatform":
        return self

    @asynccontextmanager
    async def connect(self):  # noqa: ANN201
        yield None

    async def aclose(self) -> None:
        return None


@pytest.fixture
def api(rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def get_client(_conn: Any, by: str, value: Any) -> Client | None:
        assert by == "id"
        return next((row for row in rows.values() if row.id == value), None)

    monkeypatch.setattr(publish, "get_client", get_client)
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.state.platform = FakePlatform()
    return TestClient(app)


def test_publish_needs_an_owner_token(api: TestClient, rows: dict[str, Client]) -> None:
    url = f"/owner/clients/{rows['pilot-local'].id}/publish"
    assert api.post(url).status_code == 401
    assert api.post(url, headers=bearer(token("client"))).status_code == 403


def test_publish_of_an_unknown_client_is_404(api: TestClient) -> None:
    response = api.post(
        f"/owner/clients/{uuid.uuid4()}/publish",
        headers=bearer(token("owner", client=None)),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No such client"


def test_publish_of_a_client_not_ready_is_503(api: TestClient, rows: dict[str, Client]) -> None:
    from dataclasses import replace

    rows["pilot-local"] = replace(rows["pilot-local"], status="provisioning")
    response = api.post(
        f"/owner/clients/{rows['pilot-local'].id}/publish",
        headers=bearer(token("owner", client=None)),
    )
    assert response.status_code == 503


def test_seed_draft_refuses_an_unknown_template() -> None:
    with pytest.raises(NotFound, match="No template"):
        asyncio.run(releases.seed_draft(None, "../../etc/passwd"))  # type: ignore[arg-type]


def test_cli_drafts_seed_reports_an_unknown_client(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from zm_api import cli

    class NoClients:
        async def resolve(self, slug: str) -> None:
            return None

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(cli, "Resolver", lambda *_: NoClients())
    monkeypatch.setattr(cli, "PlatformRegistry", lambda: None)
    assert cli.main(["drafts", "seed", "--client", "nobody", "--template", "qsr"]) == 1
    assert "no client nobody" in capsys.readouterr().err


def test_sign_in_look_falls_back_when_the_vault_is_unreachable(rows: dict[str, Client]) -> None:
    resolver = Resolver(FakeRegistry(rows))

    async def go() -> tuple[str, str | None]:
        try:
            return await auth._look(resolver, rows["pilot-local"])
        finally:
            await resolver.aclose()

    assert asyncio.run(go()) == ("quartz", None)


def test_sign_in_look_reads_the_active_release(
    rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Ctx:
        class db:  # noqa: N801
            @staticmethod
            @asynccontextmanager
            async def connect():  # noqa: ANN205
                yield None

    class OneClient:
        async def context(self, row: Client) -> Ctx:
            return Ctx()

    async def active(_conn: Any) -> Any:
        return type("R", (), {"ui": {"theme": "quartz", "accent": "copper"}})()

    monkeypatch.setattr(releases, "active_release", active)
    assert asyncio.run(auth._look(OneClient(), rows["pilot-local"])) == ("quartz", "copper")  # type: ignore[arg-type]

    async def none(_conn: Any) -> None:
        return None

    monkeypatch.setattr(releases, "active_release", none)
    assert asyncio.run(auth._look(OneClient(), rows["pilot-local"])) == ("quartz", None)  # type: ignore[arg-type]


BASE = "http://operaton.test/engine-rest"


@respx.mock
def test_groups_are_created_once_even_when_two_publishes_race() -> None:
    from zm_api.operaton import OperatonClient, OperatonError

    op = OperatonClient(BASE, auth=("zm-service", "pw"), client="pilot-local")
    ctx = type("Ctx", (), {"operaton": op})()
    release = compile(migrate(matrix_retail()), "pilot-local")
    existing = {"bd__L1"}
    raced = {"bd__L2"}  # another publish creates it between our read and our create

    def get_group(request):  # noqa: ANN001, ANN202
        gid = request.url.path.rsplit("/", 1)[-1]
        if gid in existing:
            return httpx.Response(200, json={"id": gid})
        return httpx.Response(404, json={"type": "InvalidRequestException", "message": "none"})

    def create(request):  # noqa: ANN001, ANN202
        gid = json.loads(request.content)["id"]
        if gid in raced:
            existing.add(gid)
            return httpx.Response(400, json={"type": "BadUserRequestException", "message": "dup"})
        existing.add(gid)
        return httpx.Response(204)

    respx.get(url__regex=rf"{BASE}/group/[^/]+$").mock(side_effect=get_group)
    created = respx.post(f"{BASE}/group/create").mock(side_effect=create)
    asyncio.run(publish._ensure_groups(ctx, release))  # type: ignore[arg-type]
    assert existing == {g.id for g in release.groups}
    assert created.call_count == len(release.groups) - 1  # bd__L1 already existed

    # any other failure still stops the publish
    respx.post(f"{BASE}/group/create").respond(500, json={"type": "X", "message": "boom"})
    existing.clear()
    with pytest.raises(OperatonError):
        asyncio.run(publish._ensure_groups(ctx, release))  # type: ignore[arg-type]
    asyncio.run(op.aclose())
