"""People management (S2-23) without a database: membership checks and who may call what."""

import uuid

from fastapi.testclient import TestClient

from zm_api.auth import Membership
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.people import membership_errors
from zm_schema.models import Department

from .conftest import bearer, token

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
