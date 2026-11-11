"""Reviewing change requests in the owner console (S2-24b, #46).

The summaries and the versioning-impact dry run need no database. The rest is
`pytest -m integration` after scripts/dev-up.sh, on the isolation client's vault (pg-iso) and
engine (operaton-iso), with a fake Anthropic client standing in for the agent.
"""

import argparse
import asyncio
import copy
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from zm_api import cr_review
from zm_api.main import create_app
from zm_api.releases import cache_clear, seed_draft

from .agent.test_loop import EMPTY, Fake, last_result, reply, sse, text, tool_use
from .conftest import bearer, token
from .test_publish import INI, engine_client, platform_sql, register, vault_sql
from .test_tasks import Stack, env_vars
from .test_tasks_unit import matrix_retail

Doc = dict[str, Any]

STATE_AND_DISTRICT = {
    "kind": "add_field",
    "form": "start",
    "fields": [{"label": "State", "type": "text"}, {"label": "District", "type": "text"}],
}
RENAME_CITY = {"kind": "rename_field_label", "field": "city", "label": "Town"}
CITY_REQUIRED = {"kind": "set_required", "field": "city", "required": True}
NOC_UPLOAD = {"kind": "add_document_type", "form": "start", "label": "Fire NOC"}
KIOSK_PLUS = {"kind": "add_select_option", "field": "store_model", "label": "Kiosk plus"}


# --- summaries and the dry run (no database) ---------------------------------------------------


def test_each_kind_reads_as_one_line() -> None:
    assert cr_review.summary(STATE_AND_DISTRICT) == "Add State, District to the start form"
    assert cr_review.summary(RENAME_CITY) == "Rename city to Town"
    assert cr_review.summary(CITY_REQUIRED) == "Make city required"
    assert cr_review.summary({**CITY_REQUIRED, "required": False}) == "Make city optional"
    assert cr_review.summary(NOC_UPLOAD) == "Add a Fire NOC upload to the start form"
    assert cr_review.summary(KIOSK_PLUS) == "Add the option Kiosk plus to store_model"


def test_the_dry_run_applies_the_request_to_a_copy_of_the_draft() -> None:
    draft = matrix_retail()
    before = copy.deepcopy(draft)
    after = cr_review.dry_run(draft, STATE_AND_DISTRICT)
    assert draft == before  # the draft itself is never touched
    assert after["fields"]["state"] == {"type": "text", "label": "State", "required": False}
    assert after["fields"]["district"]["label"] == "District"
    placed = [c["field"] for s in after["forms"]["start"]["sections"] for r in s["rows"] for c in r]
    assert placed[-2:] == ["state", "district"]


def test_a_taken_key_gets_a_suffix() -> None:
    draft = matrix_retail()
    draft["fields"]["state"] = {"type": "text", "label": "State (old)"}
    after = cr_review.dry_run(draft, STATE_AND_DISTRICT)
    assert "state_2" in after["fields"] and after["fields"]["state"]["label"] == "State (old)"


def test_impact_lists_no_problems_for_additive_requests_and_says_what_they_do() -> None:
    draft = matrix_retail()
    for payload in (STATE_AND_DISTRICT, RENAME_CITY, CITY_REQUIRED, NOC_UPLOAD, KIOSK_PLUS):
        found = cr_review.impact(draft, [draft["fields"]], payload)
        assert found.problems == [], (payload["kind"], found.problems)
        assert found.notes, payload["kind"]
    required = cr_review.impact(draft, [draft["fields"]], CITY_REQUIRED)
    assert any("started on the next release" in n for n in required.notes)


def test_impact_names_what_the_draft_no_longer_has() -> None:
    draft = matrix_retail()
    del draft["fields"]["store_model"]
    found = cr_review.impact(draft, [matrix_retail()["fields"]], KIOSK_PLUS)
    assert [p.path for p in found.problems] == ["/payload/field"]
    assert "store_model isn't in the draft" in found.problems[0].message


def test_impact_reports_the_versioning_rules() -> None:
    """A draft that already breaks S2-25 shows it: publishing it would be refused."""
    draft = matrix_retail()
    published = matrix_retail()["fields"]
    draft["fields"]["city"] = {**draft["fields"]["city"], "type": "number"}
    found = cr_review.impact(draft, [published], RENAME_CITY)
    assert any("city" in p.message for p in found.problems)


# --- on the local stack ------------------------------------------------------------------------


@dataclass
class World:
    http: TestClient
    client_id: uuid.UUID
    slug: str
    owner: uuid.UUID
    admin: str
    """The client admin's app.users id in the vault."""

    def as_owner(self) -> dict[str, str]:
        return bearer(token("owner", client=None, sub=str(self.owner)))

    def as_admin(self) -> dict[str, str]:
        return bearer(token(client=self.slug, sub=self.admin))

    def request(self, payload: Doc, note: str | None = None) -> str:
        """A request as the client admin sends it (S2-24a)."""
        body = {"payload": payload, "note": note}
        response = self.http.post("/app/admin/change-requests", json=body, headers=self.as_admin())
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def publish(self) -> int:
        url = f"/owner/clients/{self.client_id}/publish"
        response = self.http.post(url, headers=self.as_owner())
        assert response.status_code == 200, response.text
        return response.json()["version"]

    def review(self, cr: str, action: str, **body: Any) -> Any:
        return self.http.post(
            f"/owner/change-requests/{cr}/{action}", json=body, headers=self.as_owner()
        )


TABLES = ("app.draft_changes", "app.cases", "app.case_counters", "app.releases", "app.drafts")


def clear_vault() -> None:
    vault_sql("qsr", EMPTY)
    for table in TABLES:
        vault_sql("qsr", f"set session_replication_role = replica; delete from {table}")
    vault_sql("qsr", "delete from app.users where email like %s", "%@cr-review.test")


@pytest.fixture
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear_vault()
        cache_clear()

        async def setup() -> tuple[uuid.UUID, str, uuid.UUID]:
            client_id, slug = await register("qsr", "qsr")
            owner = (
                await platform_sql(
                    "insert into platform.owners (email, name, password_hash) values"
                    " (:e, 'Owner', 'not-a-hash') returning id",
                    e=f"owner-{uuid.uuid4().hex[:8]}@example.com",
                )
            )[0]["id"]
            async with Stack() as stack:
                await seed_draft(await stack.ctx(slug), "matrix-retail")
            return client_id, slug, owner

        client_id, slug, owner = asyncio.run(setup())
        admin = str(
            vault_sql(
                "qsr",
                "insert into app.users (email, name, status, is_client_admin)"
                " values ('admin@cr-review.test', 'Admin', 'active', true) returning id",
            )[0][0]
        )
        try:
            with TestClient(create_app()) as http:
                yield World(http, client_id, slug, owner, admin)
        finally:
            asyncio.run(platform_sql("delete from platform.clients where id = :c", c=client_id))
            asyncio.run(platform_sql("delete from platform.owners where id = :o", o=owner))
            clear_vault()
            cache_clear()
            with engine_client("qsr") as op:
                for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                    op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def start_form_with(doc: Doc, *keys: str) -> list[Doc]:
    """The start form's sections as arrange_form takes them, with a row for each of `keys` at
    the end of the last one."""
    sections = [
        {
            "key": s["key"],
            "title": s["title"],
            "rows": [
                [{"field": c["field"], "span": c.get("span", 12)} for c in r] for r in s["rows"]
            ],
            "hidden_below_level": s.get("hidden_below_level"),
        }
        for s in doc["forms"]["start"]["sections"]
    ]
    sections[-1]["rows"] += [[{"field": k, "span": 12}] for k in keys]
    return sections


def applies_state_and_district() -> tuple[Any, ...]:
    """How the agent applies the request: read, two fields, then the start form arranged."""
    field = {"type": "text", "required": False, "options": []}
    draft = matrix_retail()
    return (
        reply(tool_use("get_form", {"form": "start"}), stop="tool_use"),
        lambda r: reply(
            tool_use(
                "add_field",
                {
                    **field,
                    "key": "state",
                    "label": "State",
                    "draft_sha256": last_result(r)["draft_sha256"],
                },
            ),
            stop="tool_use",
        ),
        lambda r: reply(
            tool_use(
                "add_field",
                {
                    **field,
                    "key": "district",
                    "label": "District",
                    "draft_sha256": last_result(r)["draft_sha256"],
                },
            ),
            stop="tool_use",
        ),
        lambda r: reply(
            tool_use(
                "arrange_form",
                {
                    "form": "start",
                    "sections": start_form_with(draft, "state", "district"),
                    "draft_sha256": last_result(r)["draft_sha256"],
                },
            ),
            stop="tool_use",
        ),
        reply(text("State and District are on the start form.")),
    )


@pytest.mark.integration
def test_apply_with_the_agent_then_approve_then_publish_ships_it(world: World) -> None:
    assert world.publish() == 1
    cr = world.request(STATE_AND_DISTRICT, note="Our state report needs them")

    queue = world.http.get(
        "/owner/change-requests", params={"status": "submitted"}, headers=world.as_owner()
    )
    assert queue.status_code == 200, queue.text
    (listed,) = [r for r in queue.json() if r["id"] == cr]
    assert (listed["client_slug"], listed["summary"]) == (
        world.slug,
        "Add State, District to the start form",
    )
    detail = world.http.get(f"/owner/change-requests/{cr}", headers=world.as_owner()).json()
    assert detail["impact"]["problems"] == [] and detail["linked_changes"] == 0
    # approving before anything was applied is refused
    assert world.review(cr, "approve").status_code == 409

    # Apply with agent: a conversation for the request, then the owner's turn
    world.http.app.state.anthropic = fake = Fake(*applies_state_and_district())
    agent = f"/owner/clients/{world.client_id}/agent/conversations"
    started = world.http.post(
        agent, json={"mode": "edit", "change_request_id": cr}, headers=world.as_owner()
    )
    assert started.status_code == 201, started.text
    assert started.json()["change_request_id"] == cr
    status = world.http.get(f"/owner/change-requests/{cr}", headers=world.as_owner()).json()
    assert status["status"] == "in_review"
    turn = world.http.post(
        f"{agent}/{started.json()['id']}/messages",
        json={"text": "Apply the request"},
        headers=world.as_owner(),
    )
    assert turn.status_code == 200, turn.text
    assert [n for n, _ in sse(turn.text) if n == "change"] == ["change"] * 3
    system = [m for m in fake.requests[0]["messages"] if m["role"] == "system"][-1]["content"]
    assert "Add State, District to the start form" in system
    assert "Our state report needs them" in system

    # the draft has both on the start form, and the change log points at the request
    draft = world.http.get(f"/owner/clients/{world.client_id}/draft", headers=world.as_owner())
    workflow = draft.json()["workflow"]
    assert {"state", "district"} <= workflow["fields"].keys()
    rows = workflow["forms"]["start"]["sections"][-1]["rows"]
    assert [r[0]["field"] for r in rows[-2:]] == ["state", "district"]
    linked = vault_sql(
        "qsr", "select count(*) from app.draft_changes where change_request_id = %s", cr
    )
    assert linked == [(3,)]

    approved = world.review(cr, "approve", note="Applied: State and District")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    assert world.publish() == 2
    mine = world.http.get(f"/app/admin/change-requests/{cr}", headers=world.as_admin()).json()
    assert (mine["status"], mine["shipped_in_version"]) == ("shipped", 2)
    assert mine["review_note"] == "Applied: State and District"


@pytest.mark.integration
def test_a_reject_with_a_note_leaves_the_draft_and_the_client_sees_why(world: World) -> None:
    world.publish()  # a request names fields of the published release (S2-24a)
    cr = world.request(RENAME_CITY)
    before = world.http.get(f"/owner/clients/{world.client_id}/draft", headers=world.as_owner())
    rejected = world.review(cr, "reject", note="Reports group by City")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    after = world.http.get(f"/owner/clients/{world.client_id}/draft", headers=world.as_owner())
    assert after.json()["workflow"] == before.json()["workflow"]
    mine = world.http.get(f"/app/admin/change-requests/{cr}", headers=world.as_admin()).json()
    assert (mine["status"], mine["review_note"]) == ("rejected", "Reports group by City")
    # a decided request takes no other decision, and an agent can't be started for it
    assert world.review(cr, "reject", note="again").status_code == 409
    assert world.review(cr, "approve").status_code == 409
    agent = f"/owner/clients/{world.client_id}/agent/conversations"
    late = world.http.post(
        agent, json={"mode": "edit", "change_request_id": cr}, headers=world.as_owner()
    )
    assert late.status_code == 409


@pytest.mark.integration
def test_owners_only_and_the_queue_filters(world: World) -> None:
    world.publish()
    cr = world.request(CITY_REQUIRED)
    as_admin = world.http.get("/owner/change-requests", headers=world.as_admin())
    assert as_admin.status_code == 403
    unknown = world.http.get(f"/owner/change-requests/{uuid.uuid4()}", headers=world.as_owner())
    assert unknown.status_code == 404
    mine = world.http.get(
        "/owner/change-requests",
        params={"client_id": str(world.client_id)},
        headers=world.as_owner(),
    ).json()
    assert [r["id"] for r in mine] == [cr]
    other = world.http.get(
        "/owner/change-requests",
        params={"client_id": str(uuid.uuid4())},
        headers=world.as_owner(),
    ).json()
    assert other == []
    shipped = world.http.get(
        "/owner/change-requests", params={"status": "shipped"}, headers=world.as_owner()
    ).json()
    assert cr not in {r["id"] for r in shipped}
    # an empty note isn't a reason
    assert world.review(cr, "reject", note="  ").status_code == 422
    assert json.loads(json.dumps(mine[0]["payload"])) == CITY_REQUIRED
