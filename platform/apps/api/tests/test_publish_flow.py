"""Publish v1, then v2, through the API (#43, S2-09): `pytest -m integration` after dev-up.sh.

Running sites stay on the release they started on, and new sites start on the newest: a case
started on v1 still gets v1's form for its open task after v2 is published, and a case started
afterwards is on v2 with v2's form. The owner publishes over HTTP, like the console does.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from zm_api.main import create_app
from zm_api.releases import cache_clear
from zm_api.users import seed_users

from .conftest import JWT_SECRET, bearer, token
from .test_publish import INI, PLATFORM_URL, engine_client, platform_sql, register, vault_sql
from .test_tasks import START, Stack
from .test_tasks_unit import matrix_retail

pytestmark = pytest.mark.integration

KEY = Fernet.generate_key().decode()
TABLES = ("app.submissions", "app.cases", "app.case_counters", "app.releases", "app.drafts")
V1_LABEL = "Carpet area (sq ft)"
V2_LABEL = "Carpet area, as on the lease (sq ft)"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {
        "SECRETS_ENCRYPTION_KEY": KEY,
        "JWT_SECRET": JWT_SECRET,
        "PLATFORM_DATABASE_URL": PLATFORM_URL,
    }.items():
        monkeypatch.setenv(k, v)


def clear_vault() -> None:
    for table in TABLES:
        vault_sql("pilot", f"delete from {table}")
    vault_sql("pilot", "delete from app.users where email like %s", "%@publish-flow.test")


def draft(label: str) -> str:
    doc = matrix_retail()
    doc["fields"]["carpet_area"]["label"] = label
    return json.dumps(doc)


@dataclass
class Site:
    http: TestClient
    client_id: uuid.UUID
    slug: str
    owner: uuid.UUID
    users: dict[str, uuid.UUID]

    def as_user(self, user: str, **headers: str) -> dict[str, str]:
        return {**bearer(token(client=self.slug, sub=str(self.users[user]))), **headers}

    def as_owner(self) -> dict[str, str]:
        owner = jwt.encode(
            {"sub": str(self.owner), "kind": "owner", "exp": 4102444800}, JWT_SECRET, "HS256"
        )
        return {"Authorization": f"Bearer {owner}"}

    def publish(self) -> dict[str, Any]:
        response = self.http.post(
            f"/owner/clients/{self.client_id}/publish", headers=self.as_owner()
        )
        assert response.status_code == 200, response.text
        return response.json()

    def start(self) -> dict[str, Any]:
        body = {"flow": "store_launch", "values": START}
        response = self.http.post("/app/cases", json=body, headers=self.as_user("bd1"))
        assert response.status_code == 201, response.text
        return response.json()

    def task(self, user: str, form_key: str, case: str) -> dict[str, Any]:
        response = self.http.get(
            "/app/tasks", params={"case": case, "scope": "mine"}, headers=self.as_user(user)
        )
        assert response.status_code == 200, response.text
        (found,) = [t for t in response.json() if t["form_key"] == f"zm:{form_key}"]
        return found

    def shortlist(self, case: str) -> None:
        task = self.task("bd2", "bd_review_draft", case)
        headers = self.as_user("bd2", **{"Idempotency-Key": str(uuid.uuid4())})
        body = {"outcome": "shortlist"}
        response = self.http.post(f"/app/tasks/{task['id']}/submit", json=body, headers=headers)
        assert response.status_code == 200, response.text

    def carpet_area_label(self, case: str) -> str:
        """The `carpet_area` title in the form the initiator sees for the case's site details."""
        task = self.task("bd1", "bd_site_details", case)
        detail = self.http.get(f"/app/tasks/{task['id']}", headers=self.as_user("bd1"))
        assert detail.status_code == 200, detail.text
        return detail.json()["form"]["schema"]["properties"]["carpet_area"]["title"]


@pytest.fixture
def site() -> Iterator[Site]:
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")
    clear_vault()
    cache_clear()

    async def setup() -> tuple[uuid.UUID, str, uuid.UUID, dict[str, uuid.UUID]]:
        client_id, slug = await register("pilot", "pilot")
        owner = (
            await platform_sql(
                "insert into platform.owners (email, name, password_hash) values"
                " (:e, 'Owner', 'not-a-hash') returning id",
                e=f"owner-{uuid.uuid4().hex[:8]}@example.com",
            )
        )[0]["id"]
        async with Stack() as stack:
            seeded = await seed_users(
                await stack.ctx(slug),
                [
                    {"email": f"{u}@publish-flow.test", "name": u, "department": "bd", "level": n}
                    for u, n in (("bd1", 1), ("bd2", 2))
                ],
            )
        return client_id, slug, owner, {s.email.split("@")[0]: s.id for s in seeded}

    client_id, slug, owner, users = asyncio.run(setup())
    vault_sql(
        "pilot",
        "insert into app.drafts (id, workflow, template_key)"
        " values (1, %s::jsonb, 'matrix-retail')",
        draft(V1_LABEL),
    )
    try:
        with TestClient(create_app()) as http:
            yield Site(http, client_id, slug, owner, users)
    finally:
        asyncio.run(platform_sql("delete from platform.clients where id = :c", c=client_id))
        asyncio.run(platform_sql("delete from platform.owners where id = :o", o=owner))
        clear_vault()
        cache_clear()
        with engine_client("pilot") as op:
            for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def test_running_sites_keep_their_release_and_new_sites_start_on_the_newest(site: Site) -> None:
    assert site.publish()["version"] == 1
    old = site.start()
    assert old["release_version"] == 1
    site.shortlist(old["code"])
    assert site.carpet_area_label(old["code"]) == V1_LABEL

    vault_sql("pilot", "update app.drafts set workflow = %s::jsonb", draft(V2_LABEL))
    assert site.publish()["version"] == 2

    # the open v1 task still renders v1's form
    assert site.carpet_area_label(old["code"]) == V1_LABEL
    # a site started now is on v2, with v2's forms
    new = site.start()
    assert new["release_version"] == 2
    site.shortlist(new["code"])
    assert site.carpet_area_label(new["code"]) == V2_LABEL
    assert vault_sql("pilot", "select version from app.releases where active") == [(2,)]


def test_only_an_owner_may_publish(site: Site) -> None:
    response = site.http.post(
        f"/owner/clients/{site.client_id}/publish", headers=site.as_user("bd2")
    )
    assert response.status_code == 403
    assert vault_sql("pilot", "select count(*) from app.releases") == [(0,)]
