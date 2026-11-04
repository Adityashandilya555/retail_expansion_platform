"""Change requests (S2-24a). The checks against a release run without a database; the rest is
`pytest -m integration` after scripts/dev-up.sh.

pilot's active release is matrix-retail, written straight into its vault (nothing is deployed, so
no Operaton is needed). Its client admin and a normal user are seeded in the vault; a second
registry row stands in for client B. The integration tests share one module world.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from zm_api.change_requests import ChangeRequestIn, target_error
from zm_api.main import create_app
from zm_api.releases import cache_clear
from zm_schema import WorkflowV1

from .conftest import bearer, token
from .test_publish import INI, platform_sql, register, vault_sql
from .test_tasks import env_vars
from .test_tasks_unit import matrix_retail

STATE_AND_DISTRICT = {
    "kind": "add_field",
    "form": "start",
    "fields": [{"label": "State", "type": "text"}, {"label": "District", "type": "text"}],
}
TABLES = ("app.cases", "app.case_counters", "app.releases", "app.drafts")


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces conftest's per-test key: the registry row is sealed with test_tasks' KEY."""
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def check(payload: dict[str, Any], wf: WorkflowV1 | None) -> tuple[str, str] | None:
    error = target_error(wf, ChangeRequestIn.model_validate({"payload": payload}).payload)
    return None if error is None else (error.path, error.message)


def test_requests_must_name_forms_and_fields_of_the_release() -> None:
    wf = WorkflowV1.model_validate(matrix_retail())
    assert check(STATE_AND_DISTRICT, wf) is None
    assert check({"kind": "add_document_type", "form": "bd_upload_loi", "label": "NOC"}, wf) is None
    assert check({"kind": "set_required", "field": "city", "required": True}, wf) is None
    assert check({"kind": "rename_field_label", "field": "city", "label": "Town"}, wf) is None
    assert check({"kind": "add_select_option", "field": "store_model", "label": "Mall"}, wf) is None

    assert check({**STATE_AND_DISTRICT, "form": "site"}, wf) == (
        "/payload/form",
        "site isn't a form in the published release",
    )
    assert check({"kind": "set_required", "field": "state", "required": True}, wf) == (
        "/payload/field",
        "state isn't a field in the published release",
    )
    assert check({"kind": "add_select_option", "field": "city", "label": "Mall"}, wf) == (
        "/payload/field",
        "city isn't a select field",
    )
    assert check(STATE_AND_DISTRICT, None) == (  # nothing published yet
        "/payload/form",
        "start isn't a form in the published release",
    )


def test_new_fields_take_a_type_from_the_field_catalogue() -> None:
    adapter = TypeAdapter(ChangeRequestIn)
    fields = [{"label": "Colour", "type": "colour"}]
    for payload in (
        {**STATE_AND_DISTRICT, "fields": fields},
        {**STATE_AND_DISTRICT, "fields": []},
        {"kind": "rename_field_label", "field": "city", "label": "  "},
        {"kind": "drop_field", "field": "city"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python({"payload": payload})


# --- on the local stack ------------------------------------------------------------------------


@dataclass
class World:
    http: TestClient
    ids: dict[str, uuid.UUID]
    """Client name → platform.clients id."""
    users: dict[str, str]
    """admin, priya → app.users id in pilot's vault."""
    slug: str

    def auth(self, user: str) -> dict[str, str]:
        return bearer(token(client=self.slug, sub=self.users[user]))


def clear_vault() -> None:
    for table in TABLES:
        vault_sql("pilot", f"delete from {table}")
    vault_sql("pilot", "delete from app.users where email like %s", "%@cr.test")


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear_vault()
        cache_clear()

        async def setup() -> tuple[dict[str, uuid.UUID], str]:
            ids, slugs = {}, {}
            for name, vault in (("pilot", "pilot"), ("qsr", "qsr")):
                ids[name], slugs[name] = await register(name, vault)
            return ids, slugs["pilot"]

        ids, slug = asyncio.run(setup())
        vault_sql(
            "pilot",
            "insert into app.releases (version, deployment_id, workflow, forms, ui, routing_vars,"
            " process_keys, sha256, active) values (1, %s, %s::jsonb, '{}', '{}', '{}', '{}',"
            " 'x', true)",
            f"{slug}-cr",
            json.dumps(matrix_retail()),
        )
        users = {
            name: str(
                vault_sql(
                    "pilot",
                    "insert into app.users (email, name, status, is_client_admin)"
                    " values (%s, %s, 'active', %s) returning id",
                    f"{name}@cr.test",
                    name.title(),
                    admin,
                )[0][0]
            )
            for name, admin in (("admin", True), ("priya", False))
        }
        try:
            with TestClient(create_app()) as http:
                yield World(http, ids, users, slug)
        finally:
            asyncio.run(  # their change requests go with them (on delete cascade)
                platform_sql(
                    "delete from platform.clients where id = any(:ids)", ids=list(ids.values())
                )
            )
            clear_vault()
            cache_clear()


@pytest.mark.integration
def test_the_admin_asks_for_state_and_district_on_the_site_form(world: World) -> None:
    targets = world.http.get("/app/admin/change-requests/targets", headers=world.auth("admin"))
    assert targets.status_code == 200, targets.text
    site = next(f for f in targets.json()["forms"] if f["id"] == "start")
    assert site["title"] == "Site"
    assert [f["key"] for f in site["fields"]] == ["site_name", "city", "address", "store_model"]
    assert {"text", "select", "document"} <= set(targets.json()["field_types"])

    body = {"payload": STATE_AND_DISTRICT, "note": "Needed for the GST registration"}
    sent = world.http.post("/app/admin/change-requests", json=body, headers=world.auth("admin"))
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["status"] == "submitted" and request["review_note"] is None
    assert request["payload"]["kind"] == "add_field"
    assert [f["label"] for f in request["payload"]["fields"]] == ["State", "District"]

    listed = world.http.get("/app/admin/change-requests", headers=world.auth("admin"))
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()] == [request["id"]]
    one = world.http.get(f"/app/admin/change-requests/{request['id']}", headers=world.auth("admin"))
    assert one.json() == request
    stored = asyncio.run(
        platform_sql(
            "select client_id, submitted_by, kind::text from platform.change_requests"
            " where id = :id",
            id=uuid.UUID(request["id"]),
        )
    )
    assert stored == [
        {
            "client_id": world.ids["pilot"],
            "submitted_by": uuid.UUID(world.users["admin"]),
            "kind": "add_field",
        }
    ]


@pytest.mark.integration
def test_an_unknown_form_or_field_is_422_with_a_message(world: World) -> None:
    before = world.http.get("/app/admin/change-requests", headers=world.auth("admin")).json()
    for payload, path in (
        ({**STATE_AND_DISTRICT, "form": "site"}, "/payload/form"),
        ({"kind": "rename_field_label", "field": "nope", "label": "Town"}, "/payload/field"),
        ({"kind": "add_select_option", "field": "city", "label": "Mall"}, "/payload/field"),
    ):
        response = world.http.post(
            "/app/admin/change-requests", json={"payload": payload}, headers=world.auth("admin")
        )
        assert response.status_code == 422, response.text
        assert [e["path"] for e in response.json()["errors"]] == [path]
        assert response.json()["errors"][0]["message"]
    after = world.http.get("/app/admin/change-requests", headers=world.auth("admin")).json()
    assert after == before  # nothing stored


@pytest.mark.integration
def test_a_normal_user_gets_403_and_another_clients_request_404(world: World) -> None:
    for method, path in (
        ("GET", "/app/admin/change-requests"),
        ("GET", "/app/admin/change-requests/targets"),
        ("POST", "/app/admin/change-requests"),
    ):
        body = {"payload": STATE_AND_DISTRICT}
        response = world.http.request(method, path, json=body, headers=world.auth("priya"))
        assert response.status_code == 403, (method, path, response.text)

    theirs = asyncio.run(
        platform_sql(
            "insert into platform.change_requests (client_id, submitted_by, kind, payload)"
            " values (:c, :by, 'set_required', cast(:p as jsonb)) returning id",
            c=world.ids["qsr"],
            by=uuid.uuid4(),
            p=json.dumps({"kind": "set_required", "field": "city", "required": True}),
        )
    )[0]["id"]
    path = f"/app/admin/change-requests/{theirs}"
    assert world.http.get(path, headers=world.auth("admin")).status_code == 404
    listed = world.http.get("/app/admin/change-requests", headers=world.auth("admin")).json()
    assert str(theirs) not in {r["id"] for r in listed}
