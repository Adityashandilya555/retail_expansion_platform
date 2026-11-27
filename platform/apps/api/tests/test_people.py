"""People management (S2-23) on the local stack: `pytest -m integration` after scripts/dev-up.sh.

pilot runs a small legal flow whose one stage offers a Legal L1 and a Legal L2 task in parallel.
The owner invites the client admin; the admin invites Priya (Legal L1), moves and deactivates
her; the owner hands Ana the link the invite answered. Invite emails go through
the worker's sender on a mock Resend, never the real one. The tests share one module world and
run in file order.
"""

import argparse
import asyncio
import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from zm_api import people, users
from zm_api.main import create_app
from zm_api.operaton import OperatonError
from zm_api.publish import publish
from zm_api.releases import cache_clear
from zm_api.users import create_owner
from zm_worker import invites

from .conftest import bearer, token
from .test_publish import INI, VAULTS, engine_client, platform_sql, register, vault_sql
from .test_tasks import Stack, env_vars

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).resolve().parents[3] / "packages/templates/qsr/workflow.json"
BASE_URL = "https://app.people.test"
TABLES = ("app.cases", "app.case_counters", "app.releases", "app.drafts")
ADMIN, PRIYA, DEPUTY = "admin@people.test", "priya@people.test", "deputy@people.test"
ANA = "ana@people.test"
PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces conftest's per-test key: the registry row is sealed with test_tasks' KEY."""
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def workflow() -> dict[str, Any]:
    """qsr with one legal stage: an L1 and an L2 task at once, any legal level may start."""
    doc = json.loads(TEMPLATE.read_text())
    flow = doc["flows"][0]
    flow["start"]["performers"] = [{"department": "legal", "level": 1, "or_above": True}]
    legal = [
        {"id": "ppl_l1", "name": "Licence check", "performer": {"department": "legal", "level": 1}},
        {
            "id": "ppl_l2",
            "name": "Licence review",
            "performer": {"department": "legal", "level": 2},
        },
    ]
    flow["stages"] = [
        {
            "key": "statutory",
            "name": "Statutory licences",
            "department": "legal",
            "tasks": [{"parallel": [{**t, "form": "qsr_licences"} for t in legal]}],
        }
    ]
    return doc


def clear_vault() -> None:
    for table in TABLES:
        vault_sql("pilot", f"delete from {table}")
    vault_sql("pilot", "delete from app.users where email like %s", "%@people.test")


@dataclass
class World:
    http: TestClient
    client_id: uuid.UUID
    slug: str
    owner: dict[str, str]
    """The owner's Authorization header."""
    sent: list[dict[str, Any]] = field(default_factory=list)
    """Every email the mock Resend took."""
    ids: dict[str, str] = field(default_factory=dict)
    access: dict[str, dict[str, str]] = field(default_factory=dict)
    """Email → Authorization header from accepting the invite."""

    def send_invites(self, status: int = 200) -> dict[str, int]:
        """One worker pass of invite emails for pilot, on a mock Resend answering `status`."""

        def resend(request: httpx.Request) -> httpx.Response:
            assert request.url == invites.RESEND_URL
            if status == 200:
                self.sent.append(json.loads(request.content))
            return httpx.Response(status, json={"id": str(uuid.uuid4())})

        async def go() -> dict[str, int]:
            async with (
                Stack() as stack,
                httpx.AsyncClient(transport=httpx.MockTransport(resend)) as mail,
            ):
                return dict(await invites.send(await stack.ctx(self.slug), mail))

        return asyncio.run(go())

    def token_for(self, email: str) -> str:
        """The invite token in the last email to `email`."""
        text = next(m["text"] for m in reversed(self.sent) if m["to"] == [email])
        found = re.search(rf"{re.escape(BASE_URL)}/invite/(\S+)", text)
        assert found, text
        return found.group(1)

    def accept(self, email: str, password: str = PASSWORD) -> httpx.Response:
        body = {"token": self.token_for(email), "password": password}
        return self.http.post("/auth/invite/accept", json=body)

    def accept_link(self, invite_path: str) -> httpx.Response:
        """Accepts the link an invite answered (#169), as the invitee opening it would."""
        body = {"token": invite_path.removeprefix("/invite/"), "password": PASSWORD}
        return self.http.post("/auth/invite/accept", json=body)

    def admin(self) -> dict[str, str]:
        return self.access[ADMIN]

    def tasks(self, email: str) -> set[str]:
        response = self.http.get("/app/tasks", headers=self.access[email])
        assert response.status_code == 200, response.text
        return {t["form_key"] for t in response.json()}

    def groups(self, email: str) -> set[str]:
        with engine_client("pilot") as op:
            found = op.get("group", params={"member": self.ids[email]}).json()
        return {g["id"] for g in found}


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in {
            **env_vars(),
            "EMAIL_FROM": "Z-Matrix <noreply@people.test>",
            "CLIENT_APP_BASE_URL": BASE_URL,
        }.items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear_vault()
        cache_clear()

        async def setup() -> tuple[uuid.UUID, str, uuid.UUID]:
            client_id, slug = await register("pilot", "pilot")
            vault_sql(
                "pilot",
                "insert into app.drafts (id, workflow, template_key) values (1, %s::jsonb, 'x')",
                json.dumps(workflow()),
            )
            async with Stack() as stack:
                await publish(await stack.ctx(slug), stack.registry.engine(), None)
                owner = await create_owner(
                    stack.registry, f"owner+{slug}@people.test", "Owner", "x" * 16
                )
            return client_id, slug, owner

        client_id, slug, owner = asyncio.run(setup())
        try:
            # https keeps the Secure refresh cookie
            with TestClient(create_app(), base_url="https://testserver") as http:
                owner_token = bearer(token("owner", client=None, sub=str(owner)))
                yield World(http, client_id, slug, owner_token)
        finally:
            asyncio.run(platform_sql("delete from platform.clients where id = :id", id=client_id))
            asyncio.run(platform_sql("delete from platform.owners where id = :id", id=owner))
            clear_vault()
            cache_clear()
            with engine_client("pilot") as op:
                for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                    op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def test_the_owner_invites_the_client_admin_who_accepts_and_can_sign_in(world: World) -> None:
    body = {"email": ADMIN, "name": "Client Admin"}
    path = f"/owner/clients/{world.client_id}/client-admin"
    response = world.http.post(path, json=body, headers=world.owner)
    assert response.status_code == 201, response.text
    admin = response.json()
    assert (admin["status"], admin["is_client_admin"], admin["memberships"]) == (
        "invited",
        True,
        [],
    )
    world.ids[ADMIN] = admin["id"]
    again = world.http.post(path, json=body, headers=world.owner)  # not accepted yet: a new link
    assert (again.status_code, again.json()["id"]) == (201, admin["id"]), again.text
    assert again.json()["invite_path"] != admin["invite_path"]
    unknown = f"/owner/clients/{uuid.uuid4()}/client-admin"
    assert world.http.post(unknown, json=body, headers=world.owner).status_code == 404

    assert world.send_invites(status=500) == {"unsent": 1}  # kept for the next pass
    assert world.send_invites() == {"sent": 1}
    assert world.send_invites() == {}  # sent once: the token is gone from the database
    email = world.sent[-1]
    assert email["to"] == [ADMIN] and email["from"] == "Z-Matrix <noreply@people.test>"
    assert world.slug.upper() in email["text"]  # the workspace code for later sign-ins
    raw = world.token_for(ADMIN)
    assert again.json()["invite_path"] == f"/invite/{raw}"  # the owner got the emailed link
    stored = vault_sql(
        "pilot",
        "select i.token_hash, i.sealed, i.sent_at from app.invites i join app.users u"
        " on u.id = i.user_id where u.email = %s",
        ADMIN,
    )
    # one invite: the second one replaced the first
    assert len(stored) == 1 and stored[0][1] is None and stored[0][2] is not None
    assert raw.split(".", 1)[1] not in stored[0][0]

    assert world.accept(ADMIN, "short").status_code == 422
    accepted = world.accept(ADMIN)
    assert accepted.status_code == 200, accepted.text
    world.access[ADMIN] = bearer(accepted.json()["access_token"])
    me = world.http.get("/app/me", headers=world.admin()).json()
    assert (me["email"], me["is_client_admin"]) == (ADMIN, True)
    assert world.accept(ADMIN).status_code == 404  # one-time

    login = {"workspace_code": world.slug.upper(), "email": ADMIN, "password": PASSWORD}
    assert world.http.post("/auth/login", json=login).status_code == 200
    assert world.http.post(path, json=body, headers=world.owner).status_code == 409  # active now


def test_the_admin_invites_priya_legal_l1_who_then_sees_legal_tasks(world: World) -> None:
    body = {
        "email": PRIYA,
        "name": "Priya",
        "memberships": [{"department": "legal", "level": 1, "scope": "own"}],
    }
    response = world.http.post("/app/admin/users", json=body, headers=world.admin())
    assert response.status_code == 201, response.text
    world.ids[PRIYA] = response.json()["id"]
    assert world.send_invites() == {"sent": 1}
    accepted = world.accept(PRIYA)
    assert accepted.status_code == 200, accepted.text
    world.access[PRIYA] = bearer(accepted.json()["access_token"])
    assert world.groups(PRIYA) == {"legal__L1"}

    start = {"flow": "store_launch", "values": {"store_name": "Koramangala", "city": "Bengaluru"}}
    started = world.http.post("/app/cases", json=start, headers=world.access[PRIYA])
    assert started.status_code == 201, started.text
    assert world.tasks(PRIYA) == {"zm:ppl_l1"}


def test_listing_filters_by_status_and_never_shows_an_invite_token(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    listings: list[str] = []

    def emails(status: str | None) -> set[str]:
        params = {"status": status} if status else {}
        response = world.http.get("/app/admin/users", params=params, headers=world.admin())
        assert response.status_code == 200, response.text
        listings.append(response.text)
        return {u["email"] for u in response.json()}

    pending = {"email": "pending@people.test", "name": "Pending Person"}
    created = world.http.post("/app/admin/users", json=pending, headers=world.admin())
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    secret = created.json()["invite_path"].rsplit(".", 1)[1]  # the token's random part
    assert {ADMIN, PRIYA, "pending@people.test"} <= emails(None)
    assert "pending@people.test" in emails("invited") and PRIYA not in emails("invited")
    assert {ADMIN, PRIYA} <= emails("active")
    listed = world.http.get("/app/admin/users", headers=world.admin()).json()
    priya = next(u for u in listed if u["email"] == PRIYA)
    assert priya["memberships"] == [{"department": "legal", "level": 1, "scope": "own"}]
    assert all("invite" not in key for key in priya)
    assert not [text for text in listings if secret in text]
    assert caplog.records and not [r for r in caplog.records if secret in str(vars(r))]


def test_an_invite_that_fails_before_answering_leaves_the_old_link_working(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = {"email": "retry@people.test", "name": "Retry Person"}
    first = world.http.post("/app/admin/users", json=person, headers=world.admin())
    assert first.status_code == 201, first.text

    async def read_fails(*_: object, **__: object) -> None:
        raise RuntimeError("the vault stopped answering")

    with monkeypatch.context() as mp:
        mp.setattr(people, "_users", read_fails)  # the read that builds the answer
        again = world.http.post("/app/admin/users", json=person, headers=world.admin())
    assert again.status_code == 500
    accepted = world.accept_link(first.json()["invite_path"])  # nothing was committed
    assert accepted.status_code == 200, accepted.text


def test_deactivated_priya_cant_refresh_and_leaves_legal_l1(world: World) -> None:
    world.http.cookies.clear()
    session = world.http.post(
        "/auth/login",
        json={"workspace_code": world.slug.upper(), "email": PRIYA, "password": PASSWORD},
    )
    assert session.status_code == 200
    path = f"/app/admin/users/{world.ids[PRIYA]}"
    off = world.http.patch(path, json={"status": "deactivated"}, headers=world.admin())
    assert off.status_code == 200, off.text
    assert off.json()["status"] == "deactivated"
    assert world.http.post("/auth/refresh").status_code == 401
    assert world.groups(PRIYA) == set()
    assert world.http.get("/app/tasks", headers=world.access[PRIYA]).status_code == 401

    on = world.http.patch(path, json={"status": "active"}, headers=world.admin())
    assert on.status_code == 200 and on.json()["status"] == "active"
    assert world.groups(PRIYA) == {"legal__L1"}


def test_priya_moved_to_l2_sees_l2_tasks_on_her_next_request(world: World) -> None:
    body = {"memberships": [{"department": "legal", "level": 2, "scope": "own"}]}
    path = f"/app/admin/users/{world.ids[PRIYA]}"
    moved = world.http.patch(path, json=body, headers=world.admin())
    assert moved.status_code == 200, moved.text
    assert world.tasks(PRIYA) == {"zm:ppl_l2"}  # the same access token: no new sign-in
    assert world.groups(PRIYA) == {"legal__L2"}


def test_non_admins_get_403_and_levels_outside_the_release_422(world: World) -> None:
    priya, path = world.access[PRIYA], f"/app/admin/users/{world.ids[PRIYA]}"
    assert world.http.get("/app/admin/users", headers=priya).status_code == 403
    assert world.http.get("/app/admin/departments", headers=priya).status_code == 403
    invite = {"email": "x@people.test", "name": "X"}
    assert world.http.post("/app/admin/users", json=invite, headers=priya).status_code == 403
    assert world.http.patch(path, json={"status": "active"}, headers=priya).status_code == 403

    departments = world.http.get("/app/admin/departments", headers=world.admin()).json()
    legal = next(d for d in departments if d["key"] == "legal")
    assert [lv["n"] for lv in legal["levels"]] == [1, 2, 3]

    l9 = [{"department": "legal", "level": 9, "scope": "own"}]
    ops = [{"department": "ops", "level": 1, "scope": "own"}]
    for memberships in (l9, ops):
        response = world.http.patch(path, json={"memberships": memberships}, headers=world.admin())
        assert response.status_code == 422, response.text
        assert [e["path"] for e in response.json()["errors"]] == ["/memberships/0"]
        created = world.http.post(
            "/app/admin/users", json={**invite, "memberships": memberships}, headers=world.admin()
        )
        assert created.status_code == 422
    assert world.groups(PRIYA) == {"legal__L2"}  # nothing changed

    me = f"/app/admin/users/{world.ids[ADMIN]}"
    assert (
        world.http.patch(me, json={"status": "deactivated"}, headers=world.admin()).status_code
        == 409
    )


def test_an_expired_invite_is_404_until_the_admin_invites_the_person_again(world: World) -> None:
    late = {"email": "late@people.test", "name": "Late Person"}
    first = world.http.post("/app/admin/users", json=late, headers=world.admin())
    assert first.status_code == 201
    world.send_invites()
    old = world.token_for(late["email"])
    never_sent = {"email": "never-sent@people.test", "name": "Never Sent"}
    created = world.http.post("/app/admin/users", json=never_sent, headers=world.admin())
    assert created.status_code == 201
    of_people = " where user_id in (select id from app.users where email in (%s, %s))"
    emails = late["email"], never_sent["email"]
    vault_sql(
        "pilot",
        "update app.invites set expires_at = now() - interval '1 minute'" + of_people,
        *emails,
    )
    assert world.accept(late["email"]).status_code == 404
    bad = {"token": f"{world.slug}.nope", "password": PASSWORD}
    assert world.http.post("/auth/invite/accept", json=bad).status_code == 404
    assert world.send_invites() == {}  # the worker skips expired invites
    sealed = vault_sql("pilot", "select sealed from app.invites" + of_people, *emails)
    assert sealed == [(None,), (None,)]  # and forgets their tokens, even the one never sent

    legal = [{"department": "legal", "level": 1, "scope": "own"}]
    again = {**late, "name": "Late, Again", "memberships": legal}
    reissued = world.http.post("/app/admin/users", json=again, headers=world.admin())
    assert reissued.status_code == 201, reissued.text
    person = reissued.json()
    assert (person["id"], person["name"], person["memberships"]) == (
        first.json()["id"],
        "Late, Again",
        legal,
    )
    assert world.send_invites() == {"sent": 1}
    assert world.token_for(late["email"]) != old
    accepted = world.accept(late["email"])
    assert accepted.status_code == 200, accepted.text

    for taken in (late, {"email": PRIYA, "name": "Priya"}):  # active people keep the 409
        response = world.http.post("/app/admin/users", json=taken, headers=world.admin())
        assert response.status_code == 409, response.text

    deputy = {"email": DEPUTY, "name": "Deputy"}
    owner_path = f"/owner/clients/{world.client_id}/client-admin"
    by_owner = world.http.post(owner_path, json=deputy, headers=world.owner)
    assert by_owner.status_code == 201, by_owner.text
    world.ids[DEPUTY] = by_owner.json()["id"]


def test_the_owner_inviting_a_pending_admin_again_keeps_the_memberships_set_for_them(
    world: World,
) -> None:
    legal = [{"department": "legal", "level": 1, "scope": "own"}]
    path = f"/app/admin/users/{world.ids[DEPUTY]}"
    set_legal = world.http.patch(path, json={"memberships": legal}, headers=world.admin())
    assert set_legal.status_code == 200, set_legal.text
    owner_path = f"/owner/clients/{world.client_id}/client-admin"
    again = world.http.post(
        owner_path, json={"email": DEPUTY, "name": "Deputy"}, headers=world.owner
    )
    assert again.status_code == 201, again.text
    assert (again.json()["memberships"], again.json()["is_client_admin"]) == (legal, True)


def test_a_client_admin_cant_renew_another_admins_invite_to_take_it_over(world: World) -> None:
    """Renewing an invite kills the old link and answers the new one, so only the owner renews a
    client admin's: otherwise one admin could accept as a pending other."""
    owner_path = f"/owner/clients/{world.client_id}/client-admin"
    renewed = world.http.post(
        owner_path, json={"email": DEPUTY, "name": "Deputy"}, headers=world.owner
    )
    assert renewed.status_code == 201, renewed.text
    for email in (DEPUTY, ADMIN):  # pending or active
        taken = world.http.post(
            "/app/admin/users", json={"email": email, "name": "Mine now"}, headers=world.admin()
        )
        assert taken.status_code == 403, taken.text
        message = "Only the platform owner can invite or re-invite a client admin"
        assert taken.json()["detail"] == message
    accepted = world.accept_link(renewed.json()["invite_path"])  # the owner's link still works
    assert accepted.status_code == 200, accepted.text
    world.access[DEPUTY] = bearer(accepted.json()["access_token"])
    me = world.http.get("/app/me", headers=world.access[DEPUTY]).json()
    assert (me["email"], me["name"], me["is_client_admin"]) == (DEPUTY, "Deputy", True)


def test_a_failed_operaton_mirror_is_retried_by_the_reconciler(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def engine_down(*_: object, **__: object) -> None:
        raise OperatonError("user", None, None)

    path = f"/app/admin/users/{world.ids[PRIYA]}"
    l1 = {"memberships": [{"department": "legal", "level": 1, "scope": "own"}]}
    with monkeypatch.context() as mp:
        mp.setattr(users, "mirror", engine_down)
        assert world.http.patch(path, json=l1, headers=world.admin()).status_code == 502
    assert world.groups(PRIYA) == {"legal__L2"}  # the change is kept; Operaton is behind
    behind = "select unsynced_since is not null from app.users where email = %s"
    assert vault_sql("pilot", behind, PRIYA) == [(True,)]

    async def reconcile() -> Counter[str]:
        async with Stack() as stack:
            return await users.resync(await stack.ctx(world.slug), older_than=0)

    assert asyncio.run(reconcile()) == Counter(mirrored=1)
    assert world.groups(PRIYA) == {"legal__L1"}
    assert vault_sql("pilot", behind, PRIYA) == [(False,)]
    assert asyncio.run(reconcile()) == Counter()  # caught up


def test_two_admins_deactivating_each_other_at_once_leave_one_admin(world: World) -> None:
    pairs = [(ADMIN, DEPUTY), (DEPUTY, ADMIN)]
    pilot = f"postgresql://postgres:postgres@localhost:{VAULTS['pilot']}/postgres"
    # both rows held, so both requests get past the admin check before either changes anything
    with ThreadPoolExecutor(2) as pool, psycopg.connect(pilot) as hold:
        hold.execute("select 1 from app.users where email in (%s, %s) for update", (ADMIN, DEPUTY))
        calls = [
            pool.submit(
                world.http.patch,
                f"/app/admin/users/{world.ids[other]}",
                json={"status": "deactivated"},
                headers=world.access[me],
            )
            for me, other in pairs
        ]
        waiting = "select count(*) from pg_stat_activity where wait_event_type = 'Lock'"
        for _ in range(200):
            if vault_sql("pilot", waiting)[0][0] >= 2:
                break
            time.sleep(0.05)
        hold.rollback()
        statuses = sorted(c.result().status_code for c in calls)
    assert statuses == [200, 409]
    active = vault_sql(
        "pilot",
        "select email from app.users where is_client_admin and status = 'active'"
        " and email in (%s, %s)",
        ADMIN,
        DEPUTY,
    )
    assert len(active) == 1


def test_the_owner_hands_ana_the_link_the_invite_answered_and_it_works_once(world: World) -> None:
    """No worker runs here: the link needs no email. Runs last, because Ana becomes one more
    active client admin, which would let both deactivations in the test above pass."""
    path = f"/owner/clients/{world.client_id}/client-admin"
    body = {"email": ANA, "name": "Ana"}
    first, second = (world.http.post(path, json=body, headers=world.owner) for _ in range(2))
    for response in (first, second):
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
    link = second.json()
    assert re.fullmatch(rf"/invite/{re.escape(world.slug)}\.[\w-]{{43}}", link["invite_path"])
    left = datetime.fromisoformat(link["invite_expires_at"]) - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < left <= timedelta(days=7)

    killed = world.accept_link(first.json()["invite_path"])
    assert killed.status_code == 404  # the second invite killed it
    accepted = world.accept_link(link["invite_path"])
    assert accepted.status_code == 200, accepted.text
    me = world.http.get("/app/me", headers=bearer(accepted.json()["access_token"])).json()
    assert (me["email"], me["is_client_admin"]) == (ANA, True)
    assert world.accept_link(link["invite_path"]).status_code == 404  # once
