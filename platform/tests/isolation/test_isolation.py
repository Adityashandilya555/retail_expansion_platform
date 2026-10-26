"""Isolation suite v1 (#31): two local clients can't reach each other. Checks 1–7 of the issue,
plus a negative control proving check 1 catches a leak. Check 7 runs last: a wrong service
password makes Operaton refuse that account for a few seconds, so it waits for it to recover."""

import time

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from zm_api.context import Resolver
from zm_api.main import create_app

from . import checks
from .conftest import JWT_SECRET, PILOT, QSR, World, engine_client, token

pytestmark = pytest.mark.isolation


def test_1_another_clients_ids_are_404(world: World) -> None:
    assert checks.run_all(world.http, world.pilot, world.qsr) == []
    # and the client's own task still opens: the 404 isn't a blanket refusal
    own = world.http.get(f"/app/tasks/{world.pilot.task_id}", headers=world.pilot.auth("bd2"))
    assert own.status_code == 200, own.text


def test_1_another_clients_case_is_404(world: World) -> None:
    paths = world.http.app.openapi()["paths"]
    if not any(p.startswith("/app/cases/{") for p in paths):
        pytest.skip("GET /app/cases/{id} arrives with #91; this check runs from then on")
    for me, other in ((world.pilot, world.qsr), (world.qsr, world.pilot)):
        assert checks.cross_client_case(world.http, me.auth(), other.case["code"]) == []


def test_1_ui_is_the_clients_own(world: World) -> None:
    pilot_pages = {"home", "sites", "bd", "legal", "design", "pe", "project", "nso", "management"}
    qsr_pages = {"home", "stores", "store"}
    foreign = {"Authorization": f"Bearer {token(PILOT, world.qsr.users['bd1'])}"}
    leaks = checks.own_ui_only(
        world.http, world.pilot.auth(), foreign, pilot_pages | {"site"}, qsr_pages
    )
    assert leaks == []


def test_2_a_forged_client_claim_is_401(world: World) -> None:
    claims = {
        "sub": str(world.pilot.users["bd1"]),
        "kind": "client",
        "client": QSR.slug,
        "exp": int(time.time()) + 600,
    }
    for forged in (
        jwt.encode(claims, "not-the-platform-key-0123456789abcdef0123456789", algorithm="HS256"),
        jwt.encode(claims, None, algorithm="none"),
    ):
        response = world.http.get("/app/me", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401, forged
    # even signed with the platform's key, a pilot user's id names nobody in qsr-local
    real = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    assert world.http.get("/app/me", headers={"Authorization": f"Bearer {real}"}).status_code == 401


def test_3_each_engine_holds_only_its_own_client(world: World) -> None:
    for side, other in ((PILOT, QSR), (QSR, PILOT)):
        with engine_client(side) as op:
            keys = {
                d["key"] for d in op.get("process-definition", params={"maxResults": 1000}).json()
            }
        assert any(k.startswith(side.key_prefix) for k in keys), side.slug
        assert not any(k.startswith(other.key_prefix) for k in keys), (side.slug, other.slug)


def _connects(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True
    except psycopg.OperationalError:
        return False


def test_4_database_credentials_dont_cross(world: World) -> None:
    for side, other in ((PILOT, QSR), (QSR, PILOT)):
        assert _connects(side.role_dsn("zm_api", side.api_password)), side.slug
        # this client's zm_api password, on the other client's vault
        assert not _connects(other.role_dsn("zm_api", side.api_password)), (side.slug, other.slug)
    with psycopg.connect(PILOT.role_dsn("zm_engine", PILOT.engine_password)) as engine:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            engine.execute("select count(*) from app.users")


def test_5_a_user_with_another_clients_code_is_401(world: World) -> None:
    body = {
        "workspace_code": QSR.code,
        "email": world.pilot.email("bd1"),
        "password": world.pilot.passwords["bd1"],
    }
    assert world.http.post("/auth/login", json=body).status_code == 401
    own = {**body, "workspace_code": PILOT.code}
    assert world.http.post("/auth/login", json=own).status_code == 200  # the password is right


def test_6_a_client_token_on_the_owner_console_is_403(world: World) -> None:
    assert checks.owner_surface(world.http, world.pilot.auth()) == []


class LeakyResolver(Resolver):
    """A deliberately broken resolver (negative control): every token gets qsr-local."""

    async def resolve(self, slug: str):  # noqa: ANN201
        return await super().resolve(QSR.slug)


def test_8_negative_control_a_leaky_resolver_is_caught(world: World) -> None:
    from zm_api.context import PlatformRegistry

    with TestClient(create_app(LeakyResolver(PlatformRegistry()))) as leaky:
        leaks = checks.cross_client_ids(leaky, world.pilot.auth(), world.qsr.task_id)
    assert leaks, "check 1 didn't notice a resolver that ignores the token's client"


def test_7_one_clients_service_account_cant_use_the_others_engine(world: World) -> None:
    """Runs last (see the module docstring)."""
    with httpx.Client(base_url=QSR.engine, auth=("zm-service", PILOT.service_password)) as op:
        assert op.get("user/count").status_code == 401
    deadline = time.monotonic() + 70  # Operaton's login delay: 3 s, doubling up to 60 s
    with engine_client(QSR) as own:
        while own.get("user/count").status_code != 200:
            assert time.monotonic() < deadline, "operaton-iso still refuses its own account"
            time.sleep(2)
