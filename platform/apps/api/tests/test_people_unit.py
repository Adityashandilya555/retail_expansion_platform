"""People management (S2-23) without a database: membership checks and who may call what."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zm_api import people
from zm_api.auth import Membership
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.people import InviteOut, membership_errors
from zm_api.registry import Client
from zm_schema.models import Department

from .conftest import bearer, token
from .test_publish_unit import FakePlatform, FakeRegistry

ORG = [
    Department.model_validate(
        {
            "key": "legal",
            "name": "Legal",
            "levels": [{"n": 1, "name": "L1"}, {"n": 2, "name": "L2"}],
        }
    )
]


def m(department: str, level: int) -> Membership:
    return Membership(department=department, level=level, scope="own")


def test_memberships_must_name_a_department_and_level_of_the_release() -> None:
    assert membership_errors(ORG, [m("legal", 1)]) == []
    errors = membership_errors(ORG, [m("legal", 3), m("ops", 1), m("legal", 2), m("legal", 1)])
    assert [(e.path, e.message) for e in errors] == [
        ("/memberships/0", "legal has no level 3 in the published release"),
        ("/memberships/1", "ops isn't a department in the published release"),
        ("/memberships/3", "legal is listed twice"),
    ]
    assert membership_errors([], [m("legal", 1)])[0].path == "/memberships/0"  # nothing published


def test_admin_routes_need_a_client_users_token() -> None:
    app = create_app()
    api = TestClient(app)
    routes = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/app/admin/")
        for method in operations
    ]
    assert ("PATCH", "/app/admin/users/{user_id}") in routes
    for method, path in routes:
        url = path.replace("{user_id}", str(uuid.uuid4()))
        assert api.request(method, url, json={}).status_code == 401, (method, path)
        owner = bearer(token("owner", client=None))
        assert api.request(method, url, headers=owner, json={}).status_code == 403, (method, path)


async def _no_client(slug: str) -> None:
    return None


def test_an_invite_token_for_no_client_is_404() -> None:
    api = TestClient(create_app(resolver=Resolver(_no_client)))
    for value in ("no-dot", "gone-client.abc", ".abc"):
        body = {"token": value, "password": "a-long-enough-password"}
        response = api.post("/auth/invite/accept", json=body)
        assert response.status_code == 404, value
        assert response.json()["detail"] == "This invite link is invalid or has expired"


def test_only_the_two_invite_posts_answer_an_invite_link() -> None:
    """No GET (the people list included) can carry a token: only these POSTs answer InviteOut."""
    doc = create_app().openapi()
    answering = [
        (method.upper(), path)
        for path, operations in doc["paths"].items()
        for method, operation in operations.items()
        if "InviteOut" in json.dumps(operation)
    ]
    assert sorted(answering) == [
        ("POST", "/app/admin/users"),
        ("POST", "/owner/clients/{client_id}/client-admin"),
    ]
    schemas = doc["components"]["schemas"]
    embedding = [n for n, s in schemas.items() if "InviteOut" in json.dumps(s)]
    assert embedding == ["InviteOut"]  # its own title; no other shape carries it
    assert {"invite_path", "invite_expires_at"} <= set(schemas["InviteOut"]["required"])
    for _, path in answering:
        created = doc["paths"][path]["post"]["responses"]["201"]
        assert "Cache-Control" in created["headers"], path


def test_invite_responses_carry_the_link_and_no_store(
    rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = rows["pilot-local"]
    calls: list[tuple[str, bool]] = []

    async def invite(ctx: Any, body: people.UserIn, *, admin: bool = False) -> InviteOut:
        calls.append((body.email, admin))
        return InviteOut(
            id=uuid.uuid4(),
            email=body.email,
            name=body.name,
            status="invited",
            is_client_admin=admin,
            memberships=[],
            created_at=datetime.now(UTC),
            invite_path=f"/invite/{ctx.client.slug}.abc",
            invite_expires_at=datetime.now(UTC),
        )

    async def get_client(_conn: Any, by: str, value: Any) -> Client | None:
        return pilot if (by, value) == ("id", pilot.id) else None

    monkeypatch.setattr(people, "invite", invite)
    monkeypatch.setattr(people, "get_client", get_client)
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.state.platform = FakePlatform()
    app.dependency_overrides[people.client_admin] = uuid.uuid4
    api = TestClient(app)
    body = {"email": "ana@pilot.test", "name": "Ana"}

    owner = bearer(token("owner", client=None))
    by_owner = api.post(f"/owner/clients/{pilot.id}/client-admin", json=body, headers=owner)
    by_admin = api.post("/app/admin/users", json=body, headers=bearer(token("client")))
    for response in (by_owner, by_admin):
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["invite_path"] == "/invite/pilot-local.abc"
    assert calls == [("ana@pilot.test", True), ("ana@pilot.test", False)]
