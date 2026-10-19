"""`zm-api templates sync` against local pg-platform (#18): `pytest -m integration`."""

import argparse
import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import cli

pytestmark = pytest.mark.integration

URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:"
    f"{os.environ.get('PG_PLATFORM_PORT', 5433)}/postgres"
)
INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(autouse=True)
def platform_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", URL)
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")


async def rows() -> list[dict]:
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "select key, version, name, workflow_json->'flows'->0->>'key' as flow "
                    "from platform.templates where key in ('matrix-retail', 'qsr') order by key"
                )
            )
            return [dict(r) for r in result.mappings()]
    finally:
        await engine.dispose()


def test_sync_twice_keeps_one_row_per_key_and_version() -> None:
    assert cli.main(["templates", "sync"]) == 0
    first = asyncio.run(rows())
    assert cli.main(["templates", "sync"]) == 0
    second = asyncio.run(rows())
    assert first == second
    assert [(r["key"], r["version"], r["flow"]) for r in second] == [
        ("matrix-retail", 1, "store_launch"),
        ("qsr", 1, "store_launch"),
    ]
