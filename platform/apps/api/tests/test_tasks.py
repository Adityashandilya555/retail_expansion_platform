"""The tasks API against the local stack (#26): `pytest -m integration` after scripts/dev-up.sh.

pilot runs Matrix Retail plus a second flow (`audit`) and a legal section hidden below L2; qsr
runs its own template, only to have another client's task id. One case, MX-0001, is driven
through the API to `legal_dd_checklist` once for the module; the tests then follow it.
Registry rows get random slugs; the vault tables and deployments are cleaned before and after.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from zm_api import tasks
from zm_api.context import ClientContext, PlatformRegistry, Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonClient, OperatonError
from zm_api.publish import publish
from zm_api.releases import cache_clear, seed_draft
from zm_api.users import seed_users
from zm_worker.reconcile import reconcile_all

from .conftest import JWT_SECRET, bearer, token
from .test_publish import (
    ENGINES,
    INI,
    PLATFORM_URL,
    SERVICE_PASSWORD,
    VAULTS,
    platform_sql,
    register,
    vault_sql,
)
from .test_tasks_unit import matrix_retail, with_hidden_section

pytestmark = pytest.mark.integration

KEY = Fernet.generate_key().decode()
USERS = {
    "bd1": ("bd", 1),
    "bd2": ("bd", 2),
    "legal1": ("legal", 1),
    "legal2": ("legal", 2),
}
TABLES = ("app.cases", "app.case_counters", "app.releases", "app.drafts")


def env_vars() -> dict[str, str]:
    return {
        "SECRETS_ENCRYPTION_KEY": KEY,
        "JWT_SECRET": JWT_SECRET,
        "PLATFORM_DATABASE_URL": PLATFORM_URL,
    }


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces conftest's per-test key: the module's registry rows are sealed with KEY."""
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def workflow() -> dict[str, Any]:
    doc = with_hidden_section(matrix_retail())
    doc["fields"]["audit_store"] = {"type": "text", "label": "Store", "required": True}
    doc["fields"]["audit_notes"] = {"type": "textarea", "label": "Notes"}
    doc["forms"]["audit_start"] = {
        "sections": [{"key": "store", "title": "Store", "rows": [[{"field": "audit_store"}]]}]
    }
    doc["forms"]["audit_check"] = {
        "sections": [{"key": "notes", "title": "Notes", "rows": [[{"field": "audit_notes"}]]}]
    }
    doc["flows"].append(
        {
            "key": "audit",
            "name": "Store audit",
            "case_label": "Audit",
            "case_code": {"pattern": "AU-{seq:3}"},
            "start": {
                "performers": [{"department": "bd", "level": 1, "or_above": True}],
                "form": "audit_start",
            },
            "stages": [
                {
                    "key": "audit_visit",
                    "name": "Visit",
                    "department": "bd",
                    "tasks": [
                        {
                            "id": "audit_check",
                            "name": "Check the store",
                            "performer": {"department": "bd", "level": 1},
                            "form": "audit_check",
                            "outcomes": [
                                {"id": "pass", "label": "Pass"},
                                {"id": "fail", "label": "Fail"},
                            ],
                        }
                    ],
                }
            ],
        }
    )
    return doc


def clear_vaults() -> None:
    for vault in VAULTS:
        for table in TABLES:
            vault_sql(vault, f"delete from {table}")
        vault_sql(vault, "delete from app.users where email like %s", "%@tasks.test")


def op(engine: str) -> httpx.Client:
    return httpx.Client(base_url=ENGINES[engine], auth=("zm-service", SERVICE_PASSWORD))


@dataclass
class World:
    http: TestClient
    slugs: dict[str, str]
    ids: dict[str, uuid.UUID]
    users: dict[str, uuid.UUID]
    qsr_task: str

    def auth(self, user: str, **headers: str) -> dict[str, str]:
        return {**bearer(token(client=self.slugs["pilot"], sub=str(self.users[user]))), **headers}

    def tasks(self, user: str, **params: str) -> list[dict[str, Any]]:
        response = self.http.get("/app/tasks", params=params, headers=self.auth(user))
        assert response.status_code == 200, response.text
        return response.json()

    def task(self, user: str, form_key: str, case: str, scope: str = "mine") -> dict[str, Any]:
        found = [t for t in self.tasks(user, scope=scope, case=case) if t["form_key"] == form_key]
        assert len(found) == 1, (user, form_key, found)
        return found[0]

    def submit(
        self, user: str, task_id: str, key: str | None = None, **body: Any
    ) -> httpx.Response:
        headers = self.auth(user, **{"Idempotency-Key": key or str(uuid.uuid4())})
        return self.http.post(f"/app/tasks/{task_id}/submit", json=body, headers=headers)

    def do(self, user: str, form_key: str, case: str, **body: Any) -> dict[str, Any]:
        response = self.submit(user, self.task(user, f"zm:{form_key}", case)["id"], **body)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "committed"
        return response.json()

    def start(self, user: str, flow: str, values: dict[str, Any]) -> httpx.Response:
        body = {"flow": flow, "values": values}
        return self.http.post("/app/cases", json=body, headers=self.auth(user))


class Stack:
    """A resolver for direct calls, opened and closed in one event loop (see test_publish.py)."""

    def __init__(self) -> None:
        self.registry = PlatformRegistry()
        self.resolver = Resolver(self.registry)

    async def ctx(self, slug: str) -> ClientContext:
        ctx = await self.resolver.resolve(slug)
        assert ctx is not None
        return ctx

    async def __aenter__(self) -> "Stack":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.resolver.aclose()


START = {
    "site_name": "Indiranagar",
    "city": "Bengaluru",
    "address": "12th Main",
    "store_model": "kiosk",
}


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear_vaults()
        cache_clear()

        async def setup() -> tuple[dict, dict, dict, str]:
            ids, slugs = {}, {}
            for name in ("pilot", "qsr"):
                ids[name], slugs[name] = await register(name, name)
            vault_sql(
                "pilot",
                "insert into app.drafts (id, workflow, template_key) values (1, %s::jsonb, 'x')",
                json.dumps(workflow()),
            )
            async with Stack() as stack:
                pilot, qsr = await stack.ctx(slugs["pilot"]), await stack.ctx(slugs["qsr"])
                await publish(pilot, stack.registry.engine(), None)
                await seed_draft(qsr, "qsr")
                released = await publish(qsr, stack.registry.engine(), None)
                seeded = await seed_users(
                    pilot,
                    [
                        {"email": f"{u}@tasks.test", "name": u.title(), "department": d, "level": n}
                        for u, (d, n) in USERS.items()
                    ],
                )
                users = {s.email.split("@")[0]: s.id for s in seeded}
                # a task on qsr's own engine, started directly: only its id matters here
                keys = json.loads(
                    vault_sql("qsr", "select process_keys::text from app.releases")[0][0]
                )
                instance = await qsr.operaton.start_by_key(
                    next(iter(keys.values())),
                    values={"initiator": str(uuid.uuid4()), "initiator_groups": ",,"},
                )
                found = await qsr.operaton.list_tasks({"processInstanceId": instance["id"]})
                if not found:  # the first task sits in a stage process
                    found = await qsr.operaton.list_tasks({"processDefinitionKeyLike": "%"})
                assert released.version == 1 and found
                return ids, slugs, users, found[0]["id"]

        ids, slugs, users, qsr_task = asyncio.run(setup())
        try:
            with TestClient(create_app()) as http:
                yield World(http, slugs, ids, users, qsr_task)
        finally:
            asyncio.run(
                platform_sql(
                    "delete from platform.clients where id = any(:ids)", ids=list(ids.values())
                )
            )
            clear_vaults()
            cache_clear()
            for name in ("pilot", "qsr"):
                with op(name) as client:
                    found = client.get("deployment", params={"name": f"{slugs[name]}-release"})
                    for d in found.json():
                        client.delete(f"deployment/{d['id']}", params={"cascade": "true"})


@pytest.fixture(scope="module")
def mx1(world: World) -> dict[str, Any]:
    """MX-0001, started by BD L1 and driven through the API to `legal_dd_checklist`."""
    response = world.start("bd1", "store_launch", START)
    assert response.status_code == 201, response.text
    case = response.json()
    assert (case["code"], case["flow"], case["release_version"]) == ("MX-0001", "store_launch", 1)

    world.do("bd2", "bd_review_draft", "MX-0001", outcome="shortlist")
    site = world.task("bd1", "zm:bd_site_details", "MX-0001")
    # a missing required field: 422 at its path, nothing recorded, the task still open
    missing = world.submit("bd1", site["id"], values={"carpet_area": 1200})
    assert missing.status_code == 422
    assert {e["path"] for e in missing.json()["errors"]} == {
        "/frontage_ft",
        "/monthly_rent",
        "/nearest_competitor_m",
    }
    assert vault_sql(
        "pilot", "select count(*) from app.submissions where operaton_task_id = %s", site["id"]
    ) == [(0,)]
    with op("pilot") as client:
        assert client.get(f"task/{site['id']}").status_code == 200
    world.do(
        "bd1",
        "bd_site_details",
        "MX-0001",
        values={
            "carpet_area": 1200,
            "frontage_ft": 30,
            "monthly_rent": 150000,
            "nearest_competitor_m": 500,
        },
    )
    approve = world.task("bd2", "zm:bd_approve_details", "MX-0001")
    detail = world.http.get(f"/app/tasks/{approve['id']}", headers=world.auth("bd2")).json()
    assert detail["show"]["store_model"] == "kiosk" and detail["show"]["carpet_area"] == 1200
    world.do("bd2", "bd_approve_details", "MX-0001", outcome="approve")
    world.do("bd1", "bd_upload_loi", "MX-0001", values={"loi_signed_on": "2026-10-01"})
    world.do("bd2", "bd_send_legal", "MX-0001", outcome="send")
    return case


def test_initiator_is_the_user_not_the_service_account(world: World, mx1: dict) -> None:
    with op("pilot") as client:
        found = client.get(
            "history/variable-instance",
            params={"processInstanceId": mx1["process_instance_id"], "variableName": "initiator"},
        ).json()
        groups = client.get(
            "history/variable-instance",
            params={
                "processInstanceId": mx1["process_instance_id"],
                "variableName": "initiator_groups",
            },
        ).json()
    assert [v["value"] for v in found] == [str(world.users["bd1"])]
    assert [v["value"] for v in groups] == [",bd__L1,"]
    # the start form's values are in the vault, not in Operaton
    rows = vault_sql(
        "pilot",
        "select field, value from app.case_values where case_id = %s and submission_id is null",
        mx1["id"],
    )
    assert dict(rows) == START


def test_legal_l1_sees_it_l2_under_team_and_bd_l1_not_at_all(world: World, mx1: dict) -> None:
    mine = [t for t in world.tasks("legal1", scope="mine") if t["case_code"] == "MX-0001"]
    assert [(t["form_key"], t["stage"], t["flow"]) for t in mine] == [
        ("zm:legal_dd_checklist", "legal", "store_launch")
    ]
    team = [t for t in world.tasks("legal2", scope="team") if t["case_code"] == "MX-0001"]
    assert "zm:legal_dd_checklist" in [t["form_key"] for t in team]
    assert "zm:legal_dd_checklist" not in [t["form_key"] for t in world.tasks("legal2")]
    assert all(t["form_key"] != "zm:legal_dd_checklist" for t in world.tasks("bd1"))
    response = world.http.get("/app/tasks", params={"scope": "team"}, headers=world.auth("bd1"))
    assert response.status_code == 403
    task_id = mine[0]["id"]
    assert world.http.get(f"/app/tasks/{task_id}", headers=world.auth("bd1")).status_code == 404


def test_a_section_hidden_below_l2_is_not_in_l1s_form(world: World, mx1: dict) -> None:
    task = world.task("legal1", "zm:legal_dd_checklist", "MX-0001")
    l1 = world.http.get(f"/app/tasks/{task['id']}", headers=world.auth("legal1")).json()
    assert l1["editable"] is True
    assert l1["case"]["code"] == "MX-0001"
    assert "dd_partner_notes" not in l1["form"]["schema"]["properties"]
    assert "partner" not in [s["key"] for s in l1["form"]["layout"]]
    l2 = world.http.get(f"/app/tasks/{task['id']}", headers=world.auth("legal2")).json()
    assert l2["editable"] is False  # team: read-only
    assert "dd_partner_notes" in l2["form"]["schema"]["properties"]

    response = world.submit("legal1", task["id"], values={"dd_partner_notes": "x"})
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"path": "/dd_partner_notes", "message": "Not a field of this form"}
    ]
    # read-only means read-only
    assert world.submit("legal2", task["id"], values={}).status_code == 404


def test_submit_completes_in_operaton_and_commits_long_text_only_here(
    world: World, mx1: dict
) -> None:
    task = world.task("legal1", "zm:legal_dd_checklist", "MX-0001")
    with op("pilot") as client:
        stage_instance = client.get(f"task/{task['id']}").json()["processInstanceId"]
    notes = "n" * 5000
    key = str(uuid.uuid4())
    values = {"dd_title_doc": True, "dd_sanctioned_plan": True, "dd_notes": notes}
    first = world.submit("legal1", task["id"], key, values=values)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "committed" and first.json()["release_version"] == 1

    # the same key again: the first result, no second completion
    again = world.submit("legal1", task["id"], key, values=values)
    assert again.status_code == 200 and again.json() == first.json()
    # and a key reused for another task is refused
    other = world.task("bd1", "zm:fin_ca_entry", "MX-0001")
    assert world.submit("bd1", other["id"], key, values={}).status_code == 409

    with op("pilot") as client:
        assert client.get(f"task/{task['id']}").status_code == 404
        done = client.get("history/task", params={"taskId": task["id"], "finished": "true"}).json()
        assert [d["deleteReason"] for d in done] == ["completed"]
        verdict = client.post(
            "task",
            json={"processInstanceBusinessKey": "MX-0001", "candidateGroups": ["legal__L2"]},
        ).json()
        assert [t["taskDefinitionKey"] for t in verdict] == ["legal_dd_verdict"]
        engine_vars = client.get(
            "history/variable-instance", params={"processInstanceId": stage_instance}
        ).json()
    assert "dd_notes" not in {v["name"] for v in engine_vars}  # > 4000 chars: never sent

    rows = vault_sql(
        "pilot",
        "select status, release_version, length(values->>'dd_notes'), idempotency_key"
        " from app.submissions where operaton_task_id = %s",
        task["id"],
    )
    assert rows == [("committed", 1, 5000, key)]
    stored = vault_sql(
        "pilot",
        "select count(*) from app.case_values v join app.submissions s on s.id = v.submission_id"
        " where s.operaton_task_id = %s",
        task["id"],
    )
    assert stored == [(3,)]
    # the verdict shows the checklist's values, read from the vault
    verdict_item = world.task("legal2", "zm:legal_dd_verdict", "MX-0001")
    detail = world.http.get(f"/app/tasks/{verdict_item['id']}", headers=world.auth("legal2")).json()
    assert detail["editable"] is True
    assert detail["show"]["dd_notes"] == notes and detail["show"]["dd_title_doc"] is True


def test_a_crash_after_completing_is_finished_by_the_reconciler(
    world: World, mx1: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = world.task("bd1", "zm:fin_ca_entry", "MX-0001")
    completions: list[str] = []
    real_complete = OperatonClient.complete

    async def counting(self: OperatonClient, task_id: str, values: dict) -> None:
        completions.append(task_id)
        await real_complete(self, task_id, values)

    async def crash(ctx: ClientContext, submission_id: uuid.UUID) -> None:
        raise RuntimeError("killed between complete and commit")

    monkeypatch.setattr(OperatonClient, "complete", counting)
    values = {"ca_code": "CA-1", "ca_amount": 50000, "kyc_done": True}

    async def crashing_submit() -> None:
        async with Stack() as stack:
            ctx = await stack.ctx(world.slugs["pilot"])
            with monkeypatch.context() as m:
                m.setattr(tasks, "commit", crash)
                with pytest.raises(RuntimeError):
                    await tasks.submit(
                        ctx, world.users["bd1"], task["id"], values, idempotency_key="crash-1"
                    )

    asyncio.run(crashing_submit())
    status = "select status, attempts from app.submissions where idempotency_key = 'crash-1'"
    assert vault_sql("pilot", status) == [("completed", 0)]
    assert completions == [task["id"]]

    counts = asyncio.run(reconcile_all(older_than=0))  # the worker's pass over every client
    assert counts[world.slugs["pilot"]]["committed"] == 1
    assert vault_sql("pilot", status) == [("committed", 0)]
    assert completions == [task["id"]]  # no second completion
    rows = vault_sql(
        "pilot",
        "select field from app.case_values v join app.submissions s on s.id = v.submission_id"
        " where s.idempotency_key = 'crash-1' order by field",
    )
    assert rows == [("ca_amount",), ("ca_code",), ("kyc_done",)]
    assert asyncio.run(reconcile_all(older_than=0))[world.slugs["pilot"]] == {}  # nothing left


def test_a_submission_operaton_refuses_is_failed(
    world: World, mx1: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone claims the task between our check and our claim: Operaton refuses, the user gets
    409, the submission is failed with the reason, and its key can't be replayed."""
    task = world.task("bd2", "zm:fin_supervisor", "MX-0001")
    real_claim = OperatonClient.claim

    async def raced(self: OperatonClient, task_id: str, user_id: str) -> None:
        await real_claim(self, task_id, str(world.users["bd1"]))
        await real_claim(self, task_id, user_id)

    monkeypatch.setattr(OperatonClient, "claim", raced)
    response = world.submit("bd2", task["id"], "refused-1", outcome="approve")
    assert response.status_code == 409, response.text
    rows = vault_sql(
        "pilot",
        "select status, last_error from app.submissions where idempotency_key = 'refused-1'",
    )
    assert rows[0][0] == "failed" and "TaskAlreadyClaimedException" in rows[0][1]
    assert world.submit("bd2", task["id"], "refused-1", outcome="approve").status_code == 409
    # claimed by someone else now: bd2 may read it, not submit it
    detail = world.http.get(f"/app/tasks/{task['id']}", headers=world.auth("bd2")).json()
    assert detail["editable"] is False
    assert world.submit("bd2", task["id"], outcome="approve").status_code == 404
    with op("pilot") as client:
        client.post(f"task/{task['id']}/unclaim")


def test_another_clients_task_is_404(world: World, mx1: dict) -> None:
    assert (
        world.http.get(f"/app/tasks/{world.qsr_task}", headers=world.auth("bd1")).status_code == 404
    )
    assert world.submit("bd1", world.qsr_task, values={}).status_code == 404


def test_two_flows_run_their_own_processes_and_filter(world: World, mx1: dict) -> None:
    audit = world.start("bd1", "audit", {"audit_store": "Koramangala"})
    assert audit.status_code == 201, audit.text
    assert audit.json()["code"] == "AU-001"
    second = world.start("bd1", "store_launch", START).json()
    assert second["code"] == "MX-0002"
    assert audit.json()["process_instance_id"] != second["process_instance_id"]
    with op("pilot") as client:
        definition = client.get(f"process-instance/{audit.json()['process_instance_id']}").json()
        key = client.get(f"process-definition/{definition['definitionId']}").json()["key"]
    assert key.endswith("__audit")

    audits = world.tasks("bd1", flow="audit")
    assert [(t["form_key"], t["case_code"], t["stage"]) for t in audits] == [
        ("zm:audit_check", "AU-001", "audit_visit")
    ]
    assert all(t["flow"] == "store_launch" for t in world.tasks("bd1", flow="store_launch"))
    assert all(
        t["flow"] == "store_launch" for t in world.tasks("bd2", scope="team", flow="store_launch")
    )

    # per case: only that case's tasks, each with its code
    only = world.tasks("bd2", scope="team", case="MX-0002")
    assert only and {t["case_code"] for t in only} == {"MX-0002"}
    assert "MX-0002" not in {
        t["case_code"] for t in world.tasks("bd2", scope="team", case="MX-0001")
    }
    assert world.tasks("bd1", case="NOPE-1") == []

    # who may start what, and the start form's own checks
    assert world.start("legal1", "audit", {"audit_store": "x"}).status_code == 403
    assert world.start("bd1", "nope", {}).status_code == 404
    bad = world.start("bd1", "audit", {})
    assert bad.status_code == 422
    assert bad.json()["errors"] == [{"path": "/audit_store", "message": "Required"}]
    assert vault_sql("pilot", "select count(*) from app.cases where flow_key = 'audit'") == [(1,)]


def test_no_answer_from_the_engine_keeps_it_pending_for_the_reconciler(
    world: World, mx1: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout around `complete`: Operaton may or may not have it. The user gets 503, the
    submission stays pending (a second one for the task is refused), and the reconciler settles
    both ways: from history when the completion landed, by retrying once when it didn't."""
    landed = world.start("bd1", "audit", {"audit_store": "Whitefield"}).json()["code"]
    lost = world.start("bd1", "audit", {"audit_store": "Hebbal"}).json()["code"]
    completions: list[str] = []
    real_complete = OperatonClient.complete

    async def timeout_after(self: OperatonClient, task_id: str, values: dict) -> None:
        completions.append(task_id)
        await real_complete(self, task_id, values)
        raise OperatonError("complete", None, None)

    async def timeout_before(self: OperatonClient, task_id: str, values: dict) -> None:
        raise OperatonError("complete", None, None)

    first = world.task("bd1", "zm:audit_check", landed)
    with monkeypatch.context() as m:
        m.setattr(OperatonClient, "complete", timeout_after)
        response = world.submit("bd1", first["id"], "lost-1", outcome="pass")
    assert response.status_code == 503, response.text
    second = world.task("bd1", "zm:audit_check", lost)
    with monkeypatch.context() as m:
        m.setattr(OperatonClient, "complete", timeout_before)
        assert world.submit("bd1", second["id"], "lost-2", outcome="fail").status_code == 503
    # a new key for a task whose submission is still being settled is refused
    again = world.submit("bd1", second["id"], "lost-3", outcome="fail")
    assert again.status_code == 409 and "being processed" in again.json()["detail"]

    status = "select status, attempts from app.submissions where idempotency_key = %s"
    assert vault_sql("pilot", status, "lost-1") == [("pending", 0)]
    assert vault_sql("pilot", status, "lost-2") == [("pending", 0)]

    counts = asyncio.run(reconcile_all(older_than=0))
    assert counts[world.slugs["pilot"]]["committed"] == 2
    assert vault_sql("pilot", status, "lost-1") == [("committed", 0)]  # from history
    assert vault_sql("pilot", status, "lost-2") == [("committed", 1)]  # retried once
    assert completions == [first["id"]]
    with op("pilot") as client:
        for task in (first, second):
            done = client.get("history/task", params={"taskId": task["id"]}).json()
            assert [d["deleteReason"] for d in done] == ["completed"]


def test_the_same_key_at_the_same_moment_submits_once(world: World, mx1: dict) -> None:
    code = world.start("bd1", "audit", {"audit_store": "Jayanagar"}).json()["code"]
    task = world.task("bd1", "zm:audit_check", code)

    async def both() -> list:
        async with Stack() as stack:
            ctx = await stack.ctx(world.slugs["pilot"])
            return await asyncio.gather(
                *(
                    tasks.submit(
                        ctx,
                        world.users["bd1"],
                        task["id"],
                        {},
                        outcome="pass",
                        idempotency_key="same-moment",
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )

    results = asyncio.run(both())
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert results[0].id == results[1].id
    assert vault_sql(
        "pilot",
        "select count(*), max(status) from app.submissions where operaton_task_id = %s",
        task["id"],
    ) == [(1, "committed")]
