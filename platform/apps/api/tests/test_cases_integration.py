"""The cases read API against the local stack (#91): `pytest -m integration` after dev-up.sh.

pilot runs Matrix Retail with a start-form section hidden below BD L2. Three cases are driven
through the API:
- A: started by BD L2, now at Legal and Finance (parallel);
- B: still in its first stage;
- C: closed in Legal (negative verdict, change request not raised).

Another client's code is the isolation suite's check (#31, `pytest -m isolation`).
"""

import argparse
import asyncio
import json
import os
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

from zm_api.main import create_app
from zm_api.operaton import OperatonClient
from zm_api.publish import publish
from zm_api.releases import cache_clear
from zm_api.users import seed_users

from .conftest import JWT_SECRET, bearer, token
from .test_publish import ENGINES, INI, PLATFORM_URL, platform_sql, register, vault_sql
from .test_tasks import Stack
from .test_tasks_unit import matrix_retail

pytestmark = pytest.mark.integration

KEY = Fernet.generate_key().decode()
DOMAIN = "cases.test"
USERS = {"bd1": ("bd", 1), "bd2": ("bd", 2), "legal1": ("legal", 1), "legal2": ("legal", 2)}
START = {
    "site_name": "Indiranagar",
    "city": "Bengaluru",
    "address": "12th Main",
    "store_model": "kiosk",
}


def engine_client(_: str = "pilot") -> httpx.Client:
    """pilot's engine as its service account (only pilot is used here)."""
    password = os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only")
    return httpx.Client(base_url=ENGINES["pilot"], auth=("zm-service", password), timeout=15)


def env_vars() -> dict[str, str]:
    return {
        "SECRETS_ENCRYPTION_KEY": KEY,
        "JWT_SECRET": JWT_SECRET,
        "PLATFORM_DATABASE_URL": PLATFORM_URL,
    }


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def workflow() -> dict[str, Any]:
    doc = matrix_retail()
    doc["fields"]["deal_notes"] = {"type": "textarea", "label": "Deal notes"}
    doc["forms"]["start"]["sections"].append(
        {
            "key": "deal",
            "title": "Deal",
            "hidden_below_level": {"department": "bd", "level": 2},
            "rows": [[{"field": "deal_notes"}]],
        }
    )
    return doc


def clear() -> None:
    for table in ("app.cases", "app.case_counters", "app.releases", "app.drafts"):
        vault_sql("pilot", f"delete from {table}")
    vault_sql("pilot", "delete from app.users where email like %s", f"%@{DOMAIN}")


@dataclass
class World:
    http: TestClient
    slug: str
    users: dict[str, uuid.UUID]
    codes: dict[str, str]

    def auth(self, user: str) -> dict[str, str]:
        return bearer(token(client=self.slug, sub=str(self.users[user])))

    def get(self, user: str, path: str, **params: Any) -> Any:
        response = self.http.get(path, params=params, headers=self.auth(user))
        assert response.status_code == 200, response.text
        return response.json()

    def do(self, user: str, task: str, case: str, **body: Any) -> None:
        found = [
            t
            for t in self.get(user, "/app/tasks", scope="mine", case=case)
            if t["form_key"] == f"zm:{task}"
        ]
        assert found, (user, task, case)
        detail = self.get(user, f"/app/tasks/{found[0]['id']}")
        values = body.pop("values", None) or fill(detail["form"]["schema"])
        headers = {**self.auth(user), "Idempotency-Key": str(uuid.uuid4())}
        response = self.http.post(
            f"/app/tasks/{found[0]['id']}/submit", json={"values": values, **body}, headers=headers
        )
        assert response.status_code == 200, response.text


def fill(schema: dict[str, Any]) -> dict[str, Any]:
    """The required fields of a form, with plain values."""
    out: dict[str, Any] = {}
    for name in schema.get("required", []):
        prop = schema["properties"][name]
        if prop.get("x-zm-type") == "outcome":
            continue
        if "enum" in prop:
            out[name] = prop["enum"][0]
        elif prop.get("type") in {"number", "integer"}:
            out[name] = 1
        elif prop.get("type") == "boolean":
            out[name] = True
        elif prop.get("format") == "date":
            out[name] = "2026-10-01"
        else:
            out[name] = "Demo"
    return out


TO_LEGAL_FROM_L1 = [
    ("bd2", "bd_review_draft", {"outcome": "shortlist"}),
    ("bd1", "bd_site_details", {}),
    ("bd2", "bd_approve_details", {"outcome": "approve"}),
    ("bd1", "bd_upload_loi", {}),
    ("bd2", "bd_send_legal", {"outcome": "send"}),
]


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear()
        cache_clear()

        async def setup() -> tuple[uuid.UUID, str, dict[str, uuid.UUID]]:
            client_id, slug = await register("pilot", "pilot")
            vault_sql(
                "pilot",
                "insert into app.drafts (id, workflow, template_key) values (1, %s::jsonb, 'x')",
                json.dumps(workflow()),
            )
            async with Stack() as stack:
                ctx = await stack.ctx(slug)
                await publish(ctx, stack.registry.engine(), None)
                seeded = await seed_users(
                    ctx,
                    [
                        {"email": f"{u}@{DOMAIN}", "name": u.title(), "department": d, "level": n}
                        for u, (d, n) in USERS.items()
                    ],
                )
            return client_id, slug, {s.email.split("@")[0]: s.id for s in seeded}

        client_id, slug, users = asyncio.run(setup())
        try:
            with TestClient(create_app()) as http:
                w = World(http, slug, users, {})
                yield _drive(w)
        finally:
            asyncio.run(platform_sql("delete from platform.clients where id = :i", i=client_id))
            clear()
            cache_clear()
            with engine_client("pilot") as op:
                for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                    op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def _start(w: World, user: str, values: dict[str, Any]) -> str:
    response = w.http.post(
        "/app/cases", json={"flow": "store_launch", "values": values}, headers=w.auth(user)
    )
    assert response.status_code == 201, response.text
    return response.json()["code"]


def _drive(w: World) -> World:
    # A: BD L2 starts it (so the deal section is filled; its own review step is skipped)
    a = _start(w, "bd2", {**START, "site_name": "Indiranagar", "deal_notes": "Rent can drop 5%"})
    for user, task, body in TO_LEGAL_FROM_L1[1:]:
        w.do("bd2" if user == "bd1" else user, task, a, **body)
    # B: first stage, after BD L2's review with a comment
    b = _start(w, "bd1", {**START, "site_name": "Koramangala"})
    w.do("bd2", "bd_review_draft", b, outcome="shortlist", comment="Good corner plot")
    # C: closed in Legal
    c = _start(w, "bd1", {**START, "site_name": "HSR Layout"})
    for user, task, body in TO_LEGAL_FROM_L1:
        w.do(user, task, c, **body)
    w.do("legal1", "legal_dd_checklist", c)
    w.do("legal2", "legal_dd_verdict", c, outcome="negative")
    w.do("bd2", "legal_change_request", c, outcome="accept")
    w.codes = {"A": a, "B": b, "C": c}
    return w


def rows(w: World, user: str = "bd1", **params: Any) -> dict[str, dict[str, Any]]:
    return {r["code"]: r for r in w.get(user, "/app/cases", **params)}


def states(row: dict[str, Any]) -> dict[str, str]:
    return {s["key"]: s["state"] for s in row["stages"]}


def test_the_list_matches_operaton_with_one_history_request(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = OperatonClient.history_variables

    async def counting(self: OperatonClient, ids: list[str], names: list[str], **kw: Any) -> Any:
        calls.append(len(ids))
        return await real(self, ids, names, **kw)

    monkeypatch.setattr(OperatonClient, "history_variables", counting)
    found = rows(world)
    assert calls == [3]  # one request for every case's variables
    a, b = found[world.codes["A"]], found[world.codes["B"]]
    assert a["title"] == "Indiranagar" and a["status"] == "open"
    assert states(b)["bd_qualification"] == "open"
    assert set(states(b).values()) == {"open", "queued"}

    # the same states Operaton holds for A
    pi = vault_sql("pilot", "select process_instance_id from app.cases where code = %s", a["code"])[
        0
    ][0]
    with engine_client("pilot") as op:
        found_vars = op.post("history/variable-instance", json={"processInstanceIdIn": [pi]}).json()
    m = {v["name"].removeprefix("m_"): v["value"] for v in found_vars if v["name"].startswith("m_")}
    expect = {k: ("open" if v == "in progress" else "done") for k, v in m.items()}
    assert {k: s for k, s in states(a).items() if s != "queued"} == expect


def test_parallel_stages_are_open_together(world: World) -> None:
    a = rows(world)[world.codes["A"]]
    assert states(a)["legal"] == states(a)["finance"] == "open"
    assert states(a)["bd_qualification"] == states(a)["bd_loi"] == "done"
    assert a["open_tasks"] == 2  # legal_dd_checklist + fin_ca_entry


def test_a_case_closed_in_legal(world: World) -> None:
    c = rows(world)[world.codes["C"]]
    assert c["status"] == "closed"
    assert states(c)["legal"] == "closed"
    assert states(c)["finance"] == "closed"  # cancelled by the close, not finished
    assert all(states(c)[k] == "queued" for k in ("design", "pe_budget", "nso", "closure"))
    assert c["open_tasks"] == 0
    assert list(rows(world, status="closed")) == [world.codes["C"]]
    assert world.codes["C"] not in rows(world, status="open")
    assert len(rows(world, flow="store_launch")) == 3 and rows(world, flow="nope") == {}


def test_hidden_sections_are_masked_in_both_endpoints(world: World) -> None:
    code = world.codes["A"]
    assert "deal_notes" not in rows(world, "legal1")[code]["fields"]
    assert rows(world, "bd2")[code]["fields"]["deal_notes"] == "Rent can drop 5%"
    l1 = world.get("legal1", f"/app/cases/{code}")
    assert "deal" not in [s["key"] for s in l1["sections"]]
    assert "deal_notes" not in l1["fields"]
    l2 = world.get("bd2", f"/app/cases/{code}")
    deal = next(s for s in l2["sections"] if s["key"] == "deal")
    assert deal["fields"][0]["value"] == "Rent can drop 5%"


def test_unknown_code_is_404(world: World) -> None:
    response = world.http.get("/app/cases/MX-9999", headers=world.auth("bd1"))
    assert response.status_code == 404


def test_the_detail_has_times_tasks_and_decisions(world: World) -> None:
    a = world.get("legal1", f"/app/cases/{world.codes['A']}")
    legal = next(s for s in a["stages"] if s["key"] == "legal")
    assert legal["state"] == "open" and legal["started_at"] and legal["ended_at"] is None
    bd = next(s for s in a["stages"] if s["key"] == "bd_qualification")
    assert bd["state"] == "done" and bd["ended_at"]
    mine = {t["name"]: t for t in a["tasks"]}
    assert (
        mine["Fill DD checklist"]["can_act"] is True
        and mine["Fill DD checklist"]["stage"] == "legal"
    )
    assert a["my_tasks"] == 1
    lead = {t["name"]: t for t in world.get("legal2", f"/app/cases/{world.codes['A']}")["tasks"]}
    assert lead["Fill DD checklist"]["can_act"] is False  # their team's task: read-only

    b = world.get("bd1", f"/app/cases/{world.codes['B']}")
    [review] = [d for d in b["history"] if d["task"] == "bd_review_draft"]
    assert review["outcome"] == "shortlist" and review["comment"] == "Good corner plot"
    assert review["by"] == str(world.users["bd2"]) and review["by_name"] == "Bd2"
    c = world.get("bd1", f"/app/cases/{world.codes['C']}")
    assert [d["task"] for d in c["history"]][-3:] == [
        "legal_dd_checklist",
        "legal_dd_verdict",
        "legal_change_request",
    ]
    assert c["history"][-1]["outcome"] == "accept" and c["tasks"] == []
