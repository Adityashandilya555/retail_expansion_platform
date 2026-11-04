"""Appearance per user (#93): `GET /app/me` carries it and `PUT /app/me/preferences` saves it.

Validation needs no database. Persistence runs on the local stack (`pytest -m integration` after
scripts/dev-up.sh, which applies vault migration 0007), with test_auth's registry and pilot vault.
"""

import argparse
import asyncio
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api.auth import hash_password
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.registry import Client

from .conftest import bearer, token
from .test_auth import INI, PILOT_DSN, PILOT_OPERATON, PLATFORM_URL, add_client, login

VAULT = PILOT_DSN.replace("postgresql://", "postgresql+asyncpg://")


async def run_sql(dsn: str, sql: str, **params: str) -> None:
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


@pytest.fixture
def api(rows: dict[str, Client]) -> TestClient:
    async def lookup(slug: str) -> Client | None:
        return rows.get(slug)

    return TestClient(create_app(Resolver(lookup)))


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"color_scheme": "sepia"}, "/color_scheme"),
        ({"accent": "Copper"}, "/accent"),
        ({"accent": "x"}, "/accent"),
        ({"accent": "a" * 21}, "/accent"),
        ({"accent": "neon-2"}, "/accent"),
        ({"accent": "copper", "density": "compact"}, "/density"),  # only colours (D19)
    ],
)
def test_a_bad_preference_is_422_on_its_field(api: TestClient, body: dict, path: str) -> None:
    r = api.put("/app/me/preferences", json=body, headers=bearer(token()))
    assert r.status_code == 422
    assert [e["path"] for e in r.json()["errors"]] == [path]


@pytest.fixture
def pilot(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str, str]]:
    """A ready client on the pilot vault with one active user: (workspace code, email, password)."""
    monkeypatch.setenv("PLATFORM_DATABASE_URL", PLATFORM_URL)
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")
    run = uuid.uuid4().hex[:8]
    slug, code, email, password = (
        f"prefs-{run}",
        f"PREFS-{run}",
        f"prefs+{run}@pilot.demo",
        "pw-" + run,
    )

    async def go() -> None:
        await add_client(slug, f"Prefs {run}", code, PILOT_DSN, PILOT_OPERATON)
        await run_sql(
            VAULT,
            "insert into app.users (email, name, password_hash, status)"
            " values (:e, 'Prefs User', :h, 'active')",
            e=email,
            h=hash_password(password),
        )

    asyncio.run(go())
    yield code, email, password
    asyncio.run(run_sql(VAULT, "delete from app.users where email = :e", e=email))
    asyncio.run(run_sql(PLATFORM_URL, "delete from platform.clients where slug = :s", s=slug))


@pytest.mark.integration
def test_dark_and_copper_follow_the_user_to_another_browser(pilot: tuple[str, str, str]) -> None:
    code, email, password = pilot

    def signed_in(api: TestClient) -> dict[str, str]:
        api.cookies.clear()  # a browser of its own
        return bearer(login(api, code, email, password).json()["access_token"])

    with TestClient(create_app(), base_url="https://testserver") as api:
        first = signed_in(api)
        unset = {"color_scheme": None, "accent": None}
        assert api.get("/app/me", headers=first).json()["preferences"] == unset
        put = api.put("/app/me/preferences", json={"color_scheme": "dark"}, headers=first)
        assert put.status_code == 200
        put = api.put("/app/me/preferences", json={"accent": "copper"}, headers=first)
        assert put.json() == {"color_scheme": "dark", "accent": "copper"}  # only sent keys change

        other = signed_in(api)
        me = api.get("/app/me", headers=other).json()
        assert me["preferences"] == {"color_scheme": "dark", "accent": "copper"}
        # the shell owns the accent list: an id it doesn't know is kept, and skipped there
        neon = api.put("/app/me/preferences", json={"accent": "neon"}, headers=other)
        assert neon.json() == {"color_scheme": "dark", "accent": "neon"}


@pytest.mark.integration
def test_someone_deactivated_after_signing_in_cant_save(pilot: tuple[str, str, str]) -> None:
    code, email, password = pilot
    with TestClient(create_app(), base_url="https://testserver") as api:
        auth = bearer(login(api, code, email, password).json()["access_token"])
        asyncio.run(
            run_sql(VAULT, "update app.users set status = 'deactivated' where email = :e", e=email)
        )
        r = api.put("/app/me/preferences", json={"accent": "copper"}, headers=auth)
        assert r.status_code == 401
