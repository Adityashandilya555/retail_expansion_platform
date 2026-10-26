"""Sign-in (#22) on the local stack: real registry (pg-platform), the pilot vault and the iso vault
(app.users etc., migration 0002), and pilot's Operaton for the user mirror.

`pytest -m integration` after scripts/dev-up.sh (CI: zm-integration). Every run makes its own
clients, users and owner, so it never collides with earlier runs' rows.
"""

import argparse
import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import secrets
from zm_api.cli import main as cli
from zm_api.context import PlatformRegistry, Resolver
from zm_api.main import create_app
from zm_api.users import create_owner, seed_users

pytestmark = pytest.mark.integration


def port(var: str, default: int) -> int:
    return int(os.environ.get(var, default))


PLATFORM_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:{port('PG_PLATFORM_PORT', 5433)}/postgres"
)
ZM_API_PASSWORD = os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local")
PILOT_DSN = f"postgresql://zm_api:{ZM_API_PASSWORD}@localhost:54322/postgres"
ISO_API_PASSWORD = os.environ.get("ZM_ISO_API_DB_PASSWORD", "zm-api-iso-local")
ISO_DSN = f"postgresql://zm_api:{ISO_API_PASSWORD}@localhost:{port('PG_ISO_PORT', 5434)}/postgres"
PILOT_OPERATON = f"http://localhost:{port('OPERATON_PILOT_PORT', 8080)}/engine-rest"
ISO_OPERATON = f"http://localhost:{port('OPERATON_ISO_PORT', 8081)}/engine-rest"
SERVICE = ("zm-service", os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only"))
ISO_SERVICE_PASSWORD = os.environ.get("ZM_ISO_ADMIN_USER_PASSWORD", "zm-iso-local-only")
INI = Path(__file__).resolve().parents[1] / "alembic.ini"
RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    saved = dict(os.environ)
    os.environ.update(
        PLATFORM_DATABASE_URL=PLATFORM_URL,
        SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        JWT_SECRET="integration-" + "x" * 40,
    )
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")
    yield {"pilot": f"PILOT-{RUN}", "qsr": f"QSR-{RUN}"}
    os.environ.clear()
    os.environ.update(saved)


async def add_client(
    slug: str, name: str, code: str, dsn: str, operaton: str, service_password: str = SERVICE[1]
) -> None:
    sealed = secrets.seal({"zm_api_dsn": dsn, "service_account_password": service_password})
    engine = create_async_engine(PLATFORM_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into platform.clients (slug, name, workspace_code, template_key,"
                    " status, operaton_url, bucket, secrets) values (:slug, :name, :code, 'qsr',"
                    " 'ready', :url, 'files', :sealed)"
                ),
                {"slug": slug, "name": name, "code": code, "url": operaton, "sealed": sealed},
            )
    finally:
        await engine.dispose()


def users_file(tmp: Path) -> Path:
    users = [
        {
            "email": f"legal.l1+{RUN}@pilot.demo",
            "name": "Legal Executive",
            "department": "legal",
            "level": 1,
        },
        {
            "email": f"bd.l1+{RUN}@pilot.demo",
            "name": "BD Executive",
            "department": "bd",
            "level": 1,
        },
        {
            "email": f"gone+{RUN}@pilot.demo",
            "name": "Former User",
            "department": "legal",
            "level": 1,
        },
    ]
    path = tmp / "users.json"
    path.write_text(json.dumps({"users": users}))
    return path


@pytest.fixture(scope="module")
def world(env: dict[str, str], tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Two ready clients, three seeded pilot users (one then deactivated), one owner."""

    async def go() -> dict:
        await add_client(f"pilot-{RUN}", f"Pilot {RUN}", env["pilot"], PILOT_DSN, PILOT_OPERATON)
        await add_client(
            f"qsr-{RUN}", f"QSR {RUN}", env["qsr"], ISO_DSN, ISO_OPERATON, ISO_SERVICE_PASSWORD
        )
        resolver = Resolver(PlatformRegistry())
        try:
            ctx = await resolver.resolve(f"pilot-{RUN}")
            assert ctx is not None
            path = users_file(tmp_path_factory.mktemp("seed"))
            seeded = await seed_users(ctx, json.loads(path.read_text())["users"])
            async with ctx.db.begin() as conn:
                await conn.execute(
                    text("update app.users set status = 'deactivated' where email = :e"),
                    {"e": f"gone+{RUN}@pilot.demo"},
                )
            owner_password = "owner-" + uuid.uuid4().hex
            await create_owner(PlatformRegistry(), f"owner+{RUN}@zm.demo", "Owner", owner_password)
            return {
                "passwords": {s.email: s.password for s in seeded},
                "ids": {s.email: s.id for s in seeded},
                "owner": (f"owner+{RUN}@zm.demo", owner_password),
            }
        finally:
            await resolver.aclose()

    return asyncio.run(go())


@pytest.fixture
def api(world: dict) -> Iterator[TestClient]:
    # `with`: one event loop for the app's pooled connections; https keeps the Secure cookie
    with TestClient(create_app(), base_url="https://testserver") as client:
        yield client


def login(api: TestClient, code: str, email: str, password: str) -> httpx.Response:
    return api.post(
        "/auth/login", json={"workspace_code": code, "email": email, "password": password}
    )


def refresh_with(api: TestClient, path: str, cookie: str, token: str) -> httpx.Response:
    """A refresh from a browser holding only `token` (the cookie jar emptied first)."""
    api.cookies.clear()
    return api.post(path, headers={"Cookie": f"{cookie}={token}"})


def bearer(r: httpx.Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


LEGAL = f"legal.l1+{RUN}@pilot.demo"


def test_workspace_code_gives_the_clients_look_or_a_generic_404(api: TestClient, env: dict) -> None:
    r = api.post("/auth/workspace", json={"workspace_code": env["pilot"].lower()})
    assert r.status_code == 200
    assert r.json() == {"client_name": f"Pilot {RUN}", "theme": "quartz", "accent": None}
    unknown = api.post("/auth/workspace", json={"workspace_code": "NOPE-" + RUN})
    assert unknown.status_code == 404 and unknown.json()["detail"] == "Workspace not found"


def test_legal_l1_signs_in_and_me_shows_legal_l1(api: TestClient, env: dict, world: dict) -> None:
    r = login(api, env["pilot"], LEGAL, world["passwords"][LEGAL])
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    cookie = r.headers["set-cookie"]
    for part in ("zm_refresh=", "HttpOnly", "Secure", "SameSite=strict", "Path=/auth"):
        assert part.lower() in cookie.lower()
    me = api.get("/app/me", headers=bearer(r))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == LEGAL and body["client"]["slug"] == f"pilot-{RUN}"
    assert body["memberships"] == [{"department": "legal", "level": 1, "scope": "own"}]


def test_another_workspaces_code_with_pilot_credentials_is_401(
    api: TestClient, env: dict, world: dict
) -> None:
    r = login(api, env["qsr"], LEGAL, world["passwords"][LEGAL])
    assert r.status_code == 401


def test_deactivated_user_gets_the_same_401_as_a_wrong_password(
    api: TestClient, env: dict, world: dict
) -> None:
    gone = f"gone+{RUN}@pilot.demo"
    deactivated = login(api, env["pilot"], gone, world["passwords"][gone])
    wrong = login(api, env["pilot"], LEGAL, "not-the-password")
    unknown = login(api, "NOPE-" + RUN, LEGAL, world["passwords"][LEGAL])
    assert deactivated.status_code == wrong.status_code == unknown.status_code == 401
    assert deactivated.json()["detail"] == wrong.json()["detail"] == unknown.json()["detail"]


def test_tokens_only_work_on_their_own_surface(api: TestClient, env: dict, world: dict) -> None:
    client = login(api, env["pilot"], LEGAL, world["passwords"][LEGAL])
    email, password = world["owner"]
    owner = api.post("/owner/auth/login", json={"email": email, "password": password})
    assert owner.status_code == 200
    assert api.get("/owner/me", headers=bearer(owner)).json()["email"] == email
    assert api.get("/owner/me", headers=bearer(client)).status_code == 403
    assert api.get("/app/me", headers=bearer(owner)).status_code == 403


def test_a_refresh_token_used_twice_revokes_its_family(
    api: TestClient, env: dict, world: dict
) -> None:
    login(api, env["pilot"], LEGAL, world["passwords"][LEGAL])
    first = api.cookies.get("zm_refresh")
    assert first
    rotated = api.post("/auth/refresh")
    assert rotated.status_code == 200 and api.cookies.get("zm_refresh") != first
    second = api.cookies.get("zm_refresh")
    assert refresh_with(api, "/auth/refresh", "zm_refresh", first).status_code == 401
    # the reuse revoked the family, so the token handed out by the rotation is dead too
    assert refresh_with(api, "/auth/refresh", "zm_refresh", second).status_code == 401


def test_logout_revokes_the_session(api: TestClient, env: dict, world: dict) -> None:
    login(api, env["pilot"], LEGAL, world["passwords"][LEGAL])
    token = api.cookies.get("zm_refresh")
    assert api.post("/auth/logout").status_code == 204
    assert refresh_with(api, "/auth/refresh", "zm_refresh", token).status_code == 401


def test_owner_refresh_rotates(api: TestClient, world: dict) -> None:
    email, password = world["owner"]
    api.post("/owner/auth/login", json={"email": email, "password": password})
    r = api.post("/owner/auth/refresh")
    assert r.status_code == 200 and api.get("/owner/me", headers=bearer(r)).status_code == 200


def test_seeded_user_is_mirrored_into_pilots_operaton(world: dict) -> None:
    uid = world["ids"][LEGAL]
    with httpx.Client(base_url=PILOT_OPERATON, auth=SERVICE, timeout=10) as op:
        assert op.get(f"/user/{uid}/profile").status_code == 200
        groups = {g["id"] for g in op.get("/group", params={"member": str(uid)}).json()}
    assert groups == {"legal__L1"}


def test_seeding_again_keeps_passwords_and_memberships(world: dict, tmp_path: Path) -> None:
    async def go():
        resolver = Resolver(PlatformRegistry())
        try:
            ctx = await resolver.resolve(f"pilot-{RUN}")
            assert ctx is not None
            return await seed_users(ctx, json.loads(users_file(tmp_path).read_text())["users"])
        finally:
            await resolver.aclose()

    again = asyncio.run(go())
    assert all(s.password is None for s in again)  # existing users keep the password they have
    assert {s.id for s in again} == set(world["ids"].values())


def test_cli_seeds_users_and_creates_owners(
    world: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    email = f"cli+{RUN}@pilot.demo"
    monkeypatch.chdir(tmp_path)  # the CLI reads seed files under the current directory only
    person = {"email": email, "name": "Cli User", "department": "bd", "level": 2}
    (tmp_path / "users.json").write_text(json.dumps({"users": [person]}))
    path = "users.json"
    assert cli(["users", "seed", "--client", f"pilot-{RUN}", path]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith(f"{email}\t") and "(unchanged)" not in line
    assert cli(["users", "seed", "--client", "no-such-client", path]) == 1

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "a-long-enough-password")
    owner = ["owners", "create", "--email", f"cli-owner+{RUN}@zm.demo"]
    assert cli(owner) == 0
    assert cli(owner) == 1  # the email is taken
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "short")
    assert cli(["owners", "create", "--email", f"short+{RUN}@zm.demo"]) == 1
