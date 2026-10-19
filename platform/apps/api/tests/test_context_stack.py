"""The resolver against the local stack (#21): real registry, the pilot vault as `zm_api`, and
operaton-pilot as `zm-service`. `pytest -m integration` after scripts/dev-up.sh."""

import argparse
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import secrets
from zm_api.context import Resolver, platform_lookup
from zm_api.errors import NotFound
from zm_api.operaton import OperatonError

pytestmark = pytest.mark.integration

PLATFORM_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:{os.environ.get('PG_PLATFORM_PORT', 5433)}"
    "/postgres"
)
ZM_API_PASSWORD = os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local")
PILOT_DSN = f"postgresql://zm_api:{ZM_API_PASSWORD}@localhost:54322/postgres"  # Supabase CLI vault
OPERATON = f"http://localhost:{os.environ.get('OPERATON_PILOT_PORT', 8080)}/engine-rest"
SERVICE_PASSWORD = os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only")
INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(autouse=True)
def platform_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", PLATFORM_URL)
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")


async def add_client(password: str) -> str:
    slug = f"pilot-{uuid.uuid4().hex[:8]}"
    sealed = secrets.seal({"zm_api_dsn": PILOT_DSN, "service_account_password": password})
    engine = create_async_engine(PLATFORM_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into platform.clients (slug, name, workspace_code, template_key,"
                    " status, operaton_url, bucket, secrets) values (:slug, 'Pilot', :code,"
                    " 'matrix-retail', 'ready', :url, 'files', :sealed)"
                ),
                {"slug": slug, "code": slug.upper(), "url": OPERATON, "sealed": sealed},
            )
    finally:
        await engine.dispose()
    return slug


def test_resolved_client_reaches_its_own_vault_and_engine() -> None:
    async def go() -> None:
        slug = await add_client(SERVICE_PASSWORD)
        resolver = Resolver(platform_lookup())
        try:
            ctx = await resolver.resolve(slug)
            assert ctx is not None and ctx.client.slug == slug
            async with ctx.db.connect() as conn:
                assert (await conn.execute(text("select current_user"))).scalar() == "zm_api"
            with pytest.raises(NotFound):  # authenticated: Operaton's own 404, not a 401
                await ctx.operaton.process_definition(f"no_such_{uuid.uuid4().hex[:6]}")
        finally:
            await resolver.aclose()

    asyncio.run(go())


def test_wrong_service_password_is_a_502_from_operaton_401() -> None:
    async def go() -> OperatonError:
        slug = await add_client("wrong-password")
        resolver = Resolver(platform_lookup())
        try:
            ctx = await resolver.resolve(slug)
            assert ctx is not None
            with pytest.raises(OperatonError) as info:
                await ctx.operaton.process_definition("anything")
            return info.value
        finally:
            await resolver.aclose()

    err = asyncio.run(go())
    assert (err.status, err.upstream_status) == (502, 401)
