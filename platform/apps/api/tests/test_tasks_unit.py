"""Tasks API pieces without the stack (#26): forms as a user sees them, case codes, who may start
and open what, routing variables, and the routes' guards. The full flow runs in test_tasks.py
(`-m integration`)."""

import asyncio
import copy
import uuid
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zm_api import errors, tasks
from zm_api.context import Resolver
from zm_api.errors import FieldError, FormInvalid, NotFound
from zm_api.main import create_app
from zm_api.operaton import OperatonClient
from zm_api.registry import Client
from zm_api.templates import load_templates
from zm_compiler import compile
from zm_schema import migrate

from .conftest import bearer, token


def matrix_retail() -> dict[str, Any]:
    return copy.deepcopy(next(t.workflow for t in load_templates() if t.key == "matrix-retail"))


def with_hidden_section(doc: dict[str, Any]) -> dict[str, Any]:
    doc["fields"]["dd_partner_notes"] = {"type": "textarea", "label": "Partner notes"}
    doc["forms"]["legal_dd_checklist"]["sections"].append(
        {
            "key": "partner",
            "title": "Partner review",
            "hidden_below_level": {"department": "legal", "level": 2},
            "rows": [[{"field": "dd_partner_notes"}]],
        }
    )
    return doc


def form(doc: dict[str, Any], key: str) -> dict[str, Any]:
    spec = compile(migrate(doc), "pilot-local").forms[key]
    return {"schema": spec.schema, "layout": spec.layout}


def test_a_section_hidden_below_l2_is_left_out_for_l1() -> None:
    full = form(with_hidden_section(matrix_retail()), "legal_dd_checklist")
    l1 = tasks.visible_form(full, {"legal": 1})
    assert "dd_partner_notes" not in l1["schema"]["properties"]
    assert [s["key"] for s in l1["layout"]] == [s["key"] for s in full["layout"]][:-1]
    assert l1["schema"]["x-zm-layout"] == l1["layout"]
    assert "dd_partner_notes" in full["schema"]["properties"]  # the release's copy is untouched
    # someone with no level in legal sees it as level 0
    assert tasks.visible_form(full, {}) == tasks.visible_form(full, {"bd": 2})

    l2 = tasks.visible_form(full, {"legal": 2})
    assert l2 == full
    # a value for the hidden field is not a field of L1's form
    with pytest.raises(FormInvalid) as e:
        tasks._validate(l1, {"dd_partner_notes": "x"})
    assert e.value.errors == [
        FieldError(path="/dd_partner_notes", message="Not a field of this form")
    ]
    tasks._validate(l2, {"dd_partner_notes": "x"})


def test_a_field_also_in_a_visible_section_stays_and_stays_required() -> None:
    doc = matrix_retail()
    doc["forms"]["start"]["sections"].append(
        {
            "key": "again",
            "title": "Again",
            "hidden_below_level": {"department": "bd", "level": 2},
            "rows": [[{"field": "site_name"}]],
        }
    )
    l1 = tasks.visible_form(form(doc, "start:store_launch"), {"bd": 1})
    assert "site_name" in l1["schema"]["properties"]
    assert "site_name" in l1["schema"]["required"]
    assert [s["key"] for s in l1["layout"]][-1] != "again"


def test_case_codes_and_initiator_groups() -> None:
    assert tasks.case_code("MX-{seq:4}", 1) == "MX-0001"
    assert tasks.case_code("AU-{seq:2}-x", 123) == "AU-123-x"
    assert tasks.initiator_groups({"legal__L2", "bd__L1"}) == ",bd__L1,legal__L2,"
    assert tasks.initiator_groups(set()) == ",,"  # matches no `contains(',g,')`


def test_starter_groups_follow_or_above_like_the_compiler() -> None:
    doc = matrix_retail()
    flow = doc["flows"][0]
    assert tasks.starter_groups(doc, flow) == {"bd__L1", "bd__L2"}
    flow["start"]["performers"] = [
        {"department": "legal", "level": 1},
        {"initiator": True},
    ]
    assert tasks.starter_groups(doc, flow) == {"legal__L1"}


def test_stage_of_finds_tasks_parallel_tasks_and_approval_steps() -> None:
    doc = matrix_retail()
    assert tasks.stage_of(doc, "legal_dd_checklist")["key"] == "legal"
    assert tasks.stage_of(doc, "nso_readiness")["key"] == "nso"
    parallel = next(
        t["parallel"][0]["id"]
        for s in doc["flows"][0]["stages"]
        for t in s["tasks"]
        if "parallel" in t
    )
    assert tasks.stage_of(doc, parallel)["key"] == "nso"
    assert tasks.stage_of(doc, "fin_supervisor__appr_management_L1")["key"] == "finance"
    assert tasks.stage_of(doc, "nowhere") is None


def test_team_groups_cover_every_level_of_l2_departments() -> None:
    doc = matrix_retail()
    me = tasks.Person(uuid.uuid4(), {"legal": 2, "bd": 1})
    assert tasks.team_groups(doc, me) == {"legal__L1", "legal__L2"}
    assert tasks.team_groups(None, me) == {"legal__L1", "legal__L2"}  # nothing published yet
    assert tasks.team_groups(doc, tasks.Person(uuid.uuid4(), {"bd": 1})) == set()


def test_routing_values_are_scalars_within_operatons_text_column() -> None:
    values = {
        "ca_amount": 5,
        "kyc_done": True,
        "address": {"text": "x"},
        "items": [1],
        "short": "ok",
        "long": "x" * 4001,
        "none": None,
        "unrouted": "secret",
    }
    names = ["ca_amount", "kyc_done", "address", "items", "short", "long", "none", "missing"]
    assert tasks.routing_values(names, values) == {
        "ca_amount": 5,
        "kyc_done": True,
        "short": "ok",
        "none": None,
    }


def test_engine_values_fold_outcome_and_comment_back() -> None:
    sub = tasks.Submission(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        operaton_task_id="t1",
        form_key="fin_supervisor",
        release_version=1,
        values={"ca_amount": 5},
        outcome="send_back",
        comment="x" * 5000,
        status="completed",
        attempts=0,
        submitted_by=uuid.uuid4(),
        idempotency_key="k",
    )
    names = ["fin_supervisor_outcome", "fin_supervisor_comment"]
    assert sub.engine_values(names) == {"fin_supervisor_outcome": "send_back"}


def test_who_may_open_a_task() -> None:
    me = tasks.Person(uuid.uuid4(), {"legal": 1})
    lead = tasks.Person(uuid.uuid4(), {"legal": 2})
    other = tasks.Person(uuid.uuid4(), {"bd": 1})
    open_task = {"assignee": None}
    assert tasks.may_open(me, open_task, {"legal__L1"}, "legal") is True
    assert tasks.may_open(me, {"assignee": str(me.id)}, set(), None) is True
    # claimed by someone else: a candidate can read it, not submit it
    assert tasks.may_open(me, {"assignee": str(lead.id)}, {"legal__L1"}, "legal") is False
    assert tasks.may_open(lead, open_task, {"legal__L1"}, "legal") is False  # team, read-only
    assert tasks.may_open(lead, {"assignee": "someone"}, set(), "legal") is False
    with pytest.raises(NotFound):
        tasks.may_open(other, open_task, {"legal__L1"}, "legal")
    with pytest.raises(NotFound):
        tasks.may_open(me, {"assignee": "someone"}, set(), "bd")


def test_form_invalid_is_a_422_problem_with_field_errors() -> None:
    app = FastAPI()
    errors.install(app)

    @app.get("/boom")
    async def boom() -> None:
        raise FormInvalid([FieldError(path="/ca_amount", message="Required")])

    response = TestClient(app).get("/boom")
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"] == [{"path": "/ca_amount", "message": "Required"}]


class FakeRegistry:
    def __init__(self, rows: dict[str, Client]) -> None:
        self.rows = rows

    async def __call__(self, slug: str) -> Client | None:
        return self.rows.get(slug)


def test_routes_need_a_client_token_and_submit_needs_an_idempotency_key(
    rows: dict[str, Client],
) -> None:
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        assert http.get("/app/tasks").status_code == 401
        owner = bearer(token("owner", client=None))
        assert http.get("/app/tasks", headers=owner).status_code == 403
        assert http.post("/app/cases", json={"flow": "x"}, headers=owner).status_code == 403
        user = bearer(token())
        response = http.post("/app/tasks/t1/submit", json={"values": {}}, headers=user)
        assert response.status_code == 400
        assert "Idempotency-Key" in response.json()["detail"]
        long_key = {**user, "Idempotency-Key": "k" * 201}
        assert http.post("/app/tasks/t1/submit", json={}, headers=long_key).status_code == 400


@respx.mock
def test_new_operaton_calls() -> None:
    asyncio.run(_new_operaton_calls())


async def _new_operaton_calls() -> None:
    base = "http://operaton.test/engine-rest"
    op = OperatonClient(base, auth=("zm-service", "pw"), client="pilot-local")
    links = respx.get(f"{base}/task/t%201/identity-links", params={"type": "candidate"}).mock(
        return_value=httpx.Response(200, json=[{"groupId": "legal__L1", "type": "candidate"}])
    )
    instances = respx.get(
        f"{base}/process-instance", params={"processInstanceIds": "a,b", "maxResults": "2"}
    ).mock(return_value=httpx.Response(200, json=[{"id": "a", "businessKey": "MX-0001"}]))
    delete = respx.delete(
        f"{base}/process-instance/a", params={"skipCustomListeners": "true"}
    ).mock(return_value=httpx.Response(204))
    try:
        assert await op.identity_links("t 1") == [{"groupId": "legal__L1", "type": "candidate"}]
        assert await op.process_instances(["a", "b"]) == [{"id": "a", "businessKey": "MX-0001"}]
        assert await op.process_instances([]) == []  # no call for nothing
        await op.delete_process_instance("a")
    finally:
        await op.aclose()
    assert links.called and instances.call_count == 1 and delete.called
