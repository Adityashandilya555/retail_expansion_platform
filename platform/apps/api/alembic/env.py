"""Alembic environment. `-x db=platform` picks the target; the URL comes only from the env."""

import asyncio
import os

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

# -x db=<name> → (env var with the URL, schema holding the tables and alembic_version)
TARGETS = {"platform": ("PLATFORM_DATABASE_URL", "platform")}


def target() -> tuple[str, str]:
    db = context.get_x_argument(as_dictionary=True).get("db")
    if db not in TARGETS:
        raise SystemExit(f"pass -x db=<{'|'.join(TARGETS)}>")
    var, schema = TARGETS[db]
    url = os.environ.get(var)
    if not url:
        raise SystemExit(f"set {var}")
    return url, schema


def run(connection: Connection, schema: str) -> None:
    # alembic_version lives in the schema, so it must exist before Alembic writes to it
    connection.execute(text(f"create schema if not exists {schema}"))
    context.configure(connection=connection, version_table_schema=schema)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    url, schema = target()
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run, schema)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) mode is not supported; run against a database")
asyncio.run(main())
