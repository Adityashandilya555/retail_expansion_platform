"""Control-plane schema and registry (S1-04, #17) on local pg-platform: `pytest -m integration`.

Note: the round-trip test runs `downgrade base`, which empties pg-platform's `platform` schema.
"""

import argparse
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import secrets
from zm_api.registry import get_client

pytestmark = pytest.mark.integration

URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:"
    f"{os.environ.get('PG_PLATFORM_PORT', 5433)}/postgres"
)
INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic(*args: str) -> None:
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture(autouse=True)
def platform_db(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", URL)
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    alembic("upgrade", "head")


async def sql(statement: str, **params: object) -> list[dict]:
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params)
            return [dict(row) for row in result.mappings()] if result.returns_rows else []
    finally:
        await engine.dispose()


async def lookup(by: str, value: object):
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as conn:
            return await get_client(conn, by, value)  # type: ignore[arg-type]
    finally:
        await engine.dispose()


def new_client(code: str, sealed: bytes | None = None) -> dict:
    slug = f"t-{uuid.uuid4().hex[:8]}"
    return asyncio.run(
        sql(
            "insert into platform.clients (slug, name, workspace_code, template_key, secrets) "
            "values (:slug, 'Test', :code, 'qsr', :sealed) returning id, slug",
            slug=slug,
            code=code,
            sealed=sealed,
        )
    )[0]


def test_upgrade_downgrade_upgrade() -> None:
    alembic("downgrade", "base")
    assert asyncio.run(sql("select to_regclass('platform.clients') as t"))[0]["t"] is None
    alembic("upgrade", "head")
    tables = asyncio.run(
        sql("select table_name from information_schema.tables where table_schema = 'platform'")
    )
    assert {t["table_name"] for t in tables} >= {
        "clients",
        "provisioning_steps",
        "owners",
        "owner_refresh_tokens",
        "templates",
        "releases",
    }


def test_workspace_codes_differing_only_in_case_collide() -> None:
    code = f"WS{uuid.uuid4().hex[:6]}"
    new_client(code.upper())
    with pytest.raises(IntegrityError):
        new_client(code.lower())


def test_get_client_by_each_key_and_secrets_round_trip() -> None:
    data = {"zm_api_dsn": "postgresql://zm_api:p@db/postgres", "service_role_key": "k"}
    code = f"WS{uuid.uuid4().hex[:6]}"
    row = new_client(code.upper(), secrets.seal(data))

    by_id = asyncio.run(lookup("id", row["id"]))
    assert by_id is not None and by_id.slug == row["slug"]
    assert asyncio.run(lookup("slug", row["slug"])) == by_id
    assert asyncio.run(lookup("workspace_code", code.lower())) == by_id
    assert asyncio.run(lookup("slug", "no-such-client")) is None

    assert by_id.secrets is not None and b"zm_api" not in by_id.secrets
    assert secrets.unseal(by_id.secrets) == data
