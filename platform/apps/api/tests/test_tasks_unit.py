"""Tasks API pieces without the stack (#26): forms as a user sees them, case codes, who may start
and open what, routing variables, and the routes' guards. The full flow runs in test_tasks.py
(`-m integration`)."""

import asyncio
import copy
import json
import uuid
from types import SimpleNamespace
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
from zm_api.operaton import OperatonClient, OperatonError
from zm_api.registry import Client
from zm_api.templates import load_templates
from zm_compiler import compile
from zm_schema import migrate

from .conftest import bearer, token

START = {
    "site_name": "Indiranagar",
    "city": "Bengaluru",
    "address": "12th Main",
    "store_model": "kiosk",
}


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


def test_show_labels_come_from_the_releases_fields() -> None:
    """A task's read-only `show` panel names each value as the release does (#92)."""
    labels = tasks.field_labels(matrix_retail(), ["dd_notes", "dd_title_doc", "gone"])
    assert labels == {"dd_notes": "Notes", "dd_title_doc": "Title document clear", "gone": "gone"}


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


def test_every_routing_variable_is_sent_and_only_scalars_within_the_text_column() -> None:
    values = {
        "ca_amount": 5,
        "kyc_done": True,
        "address": {"text": "x"},
        "items": [1],
        "short": "ok",
        "long": "x" * 4001,
        "unrouted": "secret",
    }
    names = ["ca_amount", "kyc_done", "address", "items", "short", "long", "blank"]
    # too big or not a scalar: null; left out: not sent, so an earlier value stays
    assert tasks.routing_values(names, values) == {
        "ca_amount": 5,
        "kyc_done": True,
        "address": None,
        "items": None,
        "short": "ok",
        "long": None,
    }


def test_a_case_starts_with_every_routing_variable_of_its_flow() -> None:
    doc = matrix_retail()
    release = compile(migrate(doc), "pilot-local")
    names = tasks.flow_routing_names(release.routing_vars, doc["flows"][0])
    assert {"design_exec", "bd_review_draft_outcome", "lic_fssai"} <= names
    approvals = {k for k in release.routing_vars if "__appr_" in k}
    assert all(f"{k}_outcome" in names for k in approvals)
    assert "initiator" not in names  # set from the user at start, never null
    assert tasks.assignee_fields(doc["flows"][0]) >= {"design_exec", "pe_exec", "project_exec"}


def test_a_form_that_shows_an_assignee_field_must_fill_it() -> None:
    doc = matrix_retail()
    doc["fields"]["design_exec"]["required"] = False  # optional in the form, yet it assigns
    spec = form(doc, "design_allocate")
    tasks._validate(spec, {})  # the schema alone lets it through
    with pytest.raises(FormInvalid) as e:
        tasks._validate(spec, {}, {"design_exec"})
    assert [(err.path, err.message) for err in e.value.errors] == [
        ("/design_exec", "Required: it assigns a later step")
    ]
    tasks._validate(spec, {"design_exec": str(uuid.uuid4())}, {"design_exec"})
    tasks._validate(form(doc, "start:store_launch"), START, {"design_exec"})  # not on this form


def test_hidden_fields_are_the_ones_no_visible_section_places() -> None:
    doc = with_hidden_section(matrix_retail())
    assert tasks.hidden_fields(doc, {"legal": 1}) == {"dd_partner_notes"}
    assert tasks.hidden_fields(doc, {"legal": 2}) == set()


def test_widening_a_case_code_keeps_its_counter() -> None:
    assert tasks.counter_key("MX-{seq:3}") == tasks.counter_key("MX-{seq:4}") == "MX-{seq}"
    assert tasks.counter_key("AU-{seq:2}") != tasks.counter_key("MX-{seq:2}")


def submission(**changes: Any) -> tasks.Submission:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "operaton_task_id": "t1",
        "form_key": "fin_supervisor",
        "release_version": 1,
        "values": {"ca_amount": 5},
        "outcome": "send_back",
        "comment": "x" * 5000,
        "status": "pending",
        "attempts": 0,
        "submitted_by": uuid.uuid4(),
        "idempotency_key": "k",
    }
    return tasks.Submission(**{**base, **changes})


def test_engine_values_fold_outcome_and_comment_back() -> None:
    names = ["fin_supervisor_outcome", "fin_supervisor_comment"]
    assert submission().engine_values(names) == {
        "fin_supervisor_outcome": "send_back",
        "fin_supervisor_comment": None,  # 5000 characters stay in app.submissions
    }


def test_which_engine_errors_are_a_clear_refusal() -> None:
    assert tasks.refused(NotFound())
    assert tasks.refused(OperatonError("complete", 400, "BadUserRequestException"))
    assert tasks.refused(OperatonError("claim", 500, "TaskAlreadyClaimedException"))
    # Operaton's own error answer comes after it rolled back: a refusal
    assert tasks.refused(OperatonError("complete", 500, "ProcessEngineException"))
    # no clear answer: Operaton may have done it
    assert not tasks.refused(OperatonError("complete", None, None))  # timeout, unreachable
    assert not tasks.refused(OperatonError("complete", 502, None))  # a proxy's non-JSON 502


class FakeEngine:
    """The reconciler's Operaton calls, scripted per test."""

    def __init__(self, task: dict | None, history: list[dict], complete: Exception | None = None):
        self.task, self.history, self.error = task, history, complete
        self.calls: list[str] = []

    async def get_task(self, task_id: str) -> dict:
        self.calls.append("get_task")
        if self.task is None:
            raise NotFound()
        return self.task

    async def history_task(self, task_id: str) -> list[dict]:
        self.calls.append("history_task")
        return self.history

    async def claim(self, task_id: str, user_id: str) -> None:
        self.calls.append("claim")

    async def complete(self, task_id: str, values: dict) -> None:
        self.calls.append("complete")
        if self.error:
            raise self.error


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """What the reconciler wrote, instead of a database."""
    out: list[tuple[str, ...]] = []

    async def commit(ctx: Any, sid: uuid.UUID) -> None:
        out.append(("commit",))

    async def set_status(ctx: Any, sid: uuid.UUID, status: str, error: str | None = None) -> None:
        out.append(("status", status))

    async def bump(ctx: Any, sid: uuid.UUID) -> None:
        out.append(("attempt",))

    async def names(ctx: Any, sub: tasks.Submission) -> list[str]:
        return ["fin_supervisor_outcome"]

    monkeypatch.setattr(tasks, "commit", commit)
    monkeypatch.setattr(tasks, "_set_status", set_status)
    monkeypatch.setattr(tasks, "_bump_attempts", bump)
    monkeypatch.setattr(tasks, "_routing_names", names)
    return out


def settle(engine: FakeEngine, sub: tasks.Submission) -> str:
    ctx = SimpleNamespace(operaton=engine, client=SimpleNamespace(slug="pilot-local"))
    return asyncio.run(tasks._reconcile_one(ctx, sub))  # type: ignore[arg-type]


DONE = [{"endTime": "2026-10-22T10:00:00.000+0000", "deleteReason": "completed"}]


def test_reconcile_commits_a_completed_one_without_asking_operaton(ledger: list) -> None:
    engine = FakeEngine(None, [])
    assert settle(engine, submission(status="completed")) == "committed"
    assert engine.calls == [] and ledger == [("commit",)]


def test_reconcile_commits_a_pending_one_operaton_already_completed(ledger: list) -> None:
    engine = FakeEngine(None, DONE)
    assert settle(engine, submission()) == "committed"
    assert "complete" not in engine.calls and ledger == [("commit",)]


def test_reconcile_fails_one_whose_task_ended_otherwise(ledger: list) -> None:
    deleted = [{"endTime": "2026-10-22T10:00:00.000+0000", "deleteReason": "deleted"}]
    assert settle(FakeEngine(None, deleted), submission()) == "failed"
    assert settle(FakeEngine(None, []), submission()) == "failed"
    assert ledger == [("status", "failed"), ("status", "failed")]


def test_reconcile_retries_an_open_task_once(ledger: list) -> None:
    engine = FakeEngine({"assignee": None}, [])
    assert settle(engine, submission()) == "committed"
    assert engine.calls == ["get_task", "claim", "complete"]
    assert ledger == [("attempt",), ("status", "completed"), ("commit",)]


def test_reconcile_fails_an_open_task_after_one_retry(ledger: list) -> None:
    engine = FakeEngine({"assignee": None}, [])
    assert settle(engine, submission(attempts=1)) == "failed"
    assert "complete" not in engine.calls and ledger == [("status", "failed")]


def test_reconcile_never_completes_a_task_someone_else_holds(ledger: list) -> None:
    engine = FakeEngine({"assignee": "someone-else"}, [])
    assert settle(engine, submission()) == "failed"
    assert engine.calls == ["get_task"] and ledger == [("status", "failed")]
    mine = submission()
    engine = FakeEngine({"assignee": str(mine.submitted_by)}, [])
    assert settle(engine, mine) == "committed"
    assert engine.calls == ["get_task", "complete"]  # already ours: no claim


def test_reconcile_retry_refused_fails_but_no_answer_waits(ledger: list) -> None:
    refused = FakeEngine({"assignee": None}, [], OperatonError("complete", 400, "Bad"))
    assert settle(refused, submission()) == "failed"
    silent = FakeEngine({"assignee": None}, [], OperatonError("complete", None, None))
    assert settle(silent, submission()) == "retried"
    assert ledger == [("attempt",), ("status", "failed"), ("attempt",)]


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
    instances = respx.post(f"{base}/process-instance").mock(
        return_value=httpx.Response(200, json=[{"id": "a", "businessKey": "MX-0001"}])
    )
    unclaim = respx.post(f"{base}/task/t1/unclaim").mock(return_value=httpx.Response(204))
    delete = respx.delete(
        f"{base}/process-instance/a", params={"skipCustomListeners": "true"}
    ).mock(return_value=httpx.Response(204))
    definitions = respx.get(
        f"{base}/process-definition", params={"deploymentId": "d1", "key": "zm_x__f"}
    ).mock(return_value=httpx.Response(200, json=[{"id": "zm_x__f:2:abc"}]))
    started = respx.post(f"{base}/process-definition/zm_x__f%3A2%3Aabc/start").mock(
        return_value=httpx.Response(200, json={"id": "pi1"})
    )
    try:
        definition = await op.definition_in("d1", "zm_x__f")
        assert await op.start(definition["id"], business_key="MX-0001", values={"a": 1}) == {
            "id": "pi1"
        }
        assert json.loads(started.calls[0].request.content) == {
            "variables": {"a": {"value": 1, "type": "Long"}},
            "businessKey": "MX-0001",
        }
        respx.get(f"{base}/process-definition", params={"deploymentId": "d2"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        with pytest.raises(NotFound):
            await op.definition_in("d2", "zm_x__f")
        assert await op.identity_links("t 1") == [{"groupId": "legal__L1", "type": "candidate"}]
        assert await op.process_instances(["a", "b"]) == [{"id": "a", "businessKey": "MX-0001"}]
        # in the body: hundreds of ids would overflow the request line
        assert json.loads(instances.calls[0].request.content) == {"processInstanceIds": ["a", "b"]}
        await op.unclaim("t1")
        assert await op.process_instances([]) == []  # no call for nothing
        await op.delete_process_instance("a")
    finally:
        await op.aclose()
    assert (
        links.called
        and instances.call_count == 1
        and delete.called
        and definitions.called
        and unclaim.called
    )
