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
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError

from zm_api.change_requests import ChangeRequestIn, ChangeRequestOut, target_error
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
SET_REQUIRED = {"kind": "set_required", "field": "city", "required": True}
TABLES = ("app.cases", "app.case_counters", "app.releases", "app.drafts")
URL = "/app/admin/change-requests"


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


def test_ids_options_and_the_whole_payload_are_bounded() -> None:
    def refused(payload: dict[str, Any]) -> list[tuple[int | str, ...]]:
        with pytest.raises(ValidationError) as e:
            ChangeRequestIn.model_validate({"payload": payload})
        return [error["loc"] for error in e.value.errors()]

    model = {"label": "Model", "type": "select", "options": ["Mall", "High street"]}
    assert ChangeRequestIn.model_validate({"payload": {**STATE_AND_DISTRICT, "fields": [model]}})

    fields = ("payload", "add_field", "fields", 0, "options")
    assert refused({"kind": "set_required", "field": "f" * 65, "required": True}) == [
        ("payload", "set_required", "field")
    ]
    assert refused({**STATE_AND_DISTRICT, "form": "f" * 65}) == [("payload", "add_field", "form")]
    assert refused({**STATE_AND_DISTRICT, "fields": [{**model, "options": []}]}) == [fields]
    assert refused({**STATE_AND_DISTRICT, "fields": [{**model, "type": "text"}]}) == [fields]
    huge = {**model, "options": [f"{n:03} {'x' * 196}" for n in range(100)]}  # each fits alone
    assert refused({**STATE_AND_DISTRICT, "fields": [huge] * 20}) == [("payload",)]


def test_a_stored_request_reads_as_stored_even_when_it_no_longer_validates() -> None:
    stale = {"kind": "set_required", "field": "city"}  # stored before `required` was required
    now = datetime.now(UTC)
    row = {
        **dict.fromkeys(("note", "review_note", "shipped_in_version")),
        "id": uuid.uuid4(),
        "kind": "set_required",
        "payload": stale,
        "status": "submitted",
        "created_at": now,
        "updated_at": now,
    }
    assert ChangeRequestOut(**row).payload == stale


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


@pytest.mark.integration
def test_a_stored_request_that_no_longer_validates_still_lists(world: World) -> None:
    stale = {"kind": "set_required", "field": "city"}  # `required` missing
    insert = (
        "insert into platform.change_requests (client_id, submitted_by, kind, payload)"
        " values (:c, :by, :kind, cast(:p as jsonb)) returning id"
    )
    row = {"c": world.ids["pilot"], "by": uuid.UUID(world.users["admin"]), "p": json.dumps(stale)}
    stored = asyncio.run(platform_sql(insert, **row, kind="set_required"))[0]["id"]

    listed = world.http.get(URL, headers=world.auth("admin"))
    assert listed.status_code == 200, listed.text
    mine = next(r for r in listed.json() if r["id"] == str(stored))
    assert (mine["kind"], mine["payload"]) == ("set_required", stale)
    with pytest.raises(IntegrityError):  # the kind column keeps to the payload's
        asyncio.run(platform_sql(insert, **row, kind="add_field"))


@pytest.mark.integration
def test_the_list_comes_in_pages_newest_first(world: World) -> None:
    admin = world.auth("admin")
    sent = [
        world.http.post(URL, json={"payload": SET_REQUIRED}, headers=admin).json()["id"]
        for _ in range(2)
    ]
    newest = world.http.get(URL, params={"limit": 2}, headers=admin).json()
    assert [r["id"] for r in newest] == sent[::-1]
    second = world.http.get(URL, params={"limit": 1, "offset": 1}, headers=admin).json()
    assert [r["id"] for r in second] == sent[:1]
    too_many = world.http.get(URL, params={"limit": 501}, headers=admin)
    assert too_many.status_code == 422, too_many.text


@pytest.mark.integration
def test_a_retry_with_the_same_idempotency_key_returns_the_first_request(world: World) -> None:
    key = str(uuid.uuid4())
    asyncio.run(  # client B used the key too: keys are per client
        platform_sql(
            "insert into platform.change_requests (client_id, submitted_by, kind, payload,"
            " idempotency_key) values (:c, :by, 'set_required', cast(:p as jsonb), :k)",
            c=world.ids["qsr"],
            by=uuid.uuid4(),
            p=json.dumps(SET_REQUIRED),
            k=key,
        )
    )
    headers = {**world.auth("admin"), "Idempotency-Key": key}
    first = world.http.post(URL, json={"payload": SET_REQUIRED}, headers=headers)
    again = world.http.post(URL, json={"payload": SET_REQUIRED}, headers=headers)
    assert (first.status_code, again.status_code) == (201, 201), again.text
    assert again.json() == first.json()
    stored = asyncio.run(
        platform_sql(
            "select client_id from platform.change_requests where idempotency_key = :k"
            " order by created_at",
            k=key,
        )
    )
    assert [r["client_id"] for r in stored] == [world.ids["qsr"], world.ids["pilot"]]

    other = {"payload": {**SET_REQUIRED, "required": False}}
    reused = world.http.post(URL, json=other, headers=headers)
    assert reused.status_code == 409, reused.text


@pytest.mark.integration
def test_a_retry_returns_the_first_request_even_if_its_field_is_gone_now(world: World) -> None:
    """The response was lost, then a release dropped the field: the retry still answers with the
    stored request instead of a 422 about the field."""
    key = str(uuid.uuid4())
    gone = {"kind": "set_required", "field": "gone_since", "required": True}
    asyncio.run(
        platform_sql(
            "insert into platform.change_requests (client_id, submitted_by, kind, payload,"
            " idempotency_key) values (:c, :by, 'set_required', cast(:p as jsonb), :k)",
            c=world.ids["pilot"],
            by=uuid.UUID(world.users["admin"]),
            p=json.dumps(gone),
            k=key,
        )
    )
    headers = {**world.auth("admin"), "Idempotency-Key": key}
    retry = world.http.post(URL, json={"payload": gone}, headers=headers)
    assert retry.status_code == 201, retry.text
    assert retry.json()["payload"] == gone
    fresh = world.http.post(URL, json={"payload": gone}, headers=world.auth("admin"))
    assert fresh.status_code == 422  # without the key it's a new request, checked as usual
