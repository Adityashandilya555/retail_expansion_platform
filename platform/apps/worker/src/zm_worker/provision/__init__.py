"""Provisioning worker (S2-02a/b): builds each client's vault from platform.provisioning_steps.

The API inserts a new client's steps (S2-01); the worker runs those it knows, in seq order.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import psycopg

from zm_worker.provision import railway, supabase
from zm_worker.provision.runner import Settings, open_context, run_pending

STEPS = {**supabase.STEPS, **railway.STEPS}


@contextmanager
def _clients(
    platform_dsn: str, settings: Settings
) -> Iterator[tuple[psycopg.Connection, httpx.Client, httpx.Client]]:
    # settings DSNs are SQLAlchemy URLs (postgresql+asyncpg://…); libpq wants plain postgresql://
    with (
        psycopg.connect(platform_dsn.replace("+asyncpg", "", 1), autocommit=True) as conn,
        httpx.Client(
            base_url=supabase.API,
            headers={"Authorization": f"Bearer {settings.access_token}"},
            timeout=30,
        ) as mgmt,
        httpx.Client(timeout=30) as http,
    ):
        yield conn, mgmt, http


def run(platform_dsn: str, settings: Settings) -> int:
    """One pass over the queue; returns how many steps ran."""
    with _clients(platform_dsn, settings) as (conn, mgmt, http):
        return run_pending(conn, STEPS, settings, mgmt, http)


def deprovision(platform_dsn: str, settings: Settings, slug: str) -> list[str]:
    """`zm-worker deprovision <slug> --yes` (test clients): what was deleted."""
    with _clients(platform_dsn, settings) as (conn, mgmt, http):
        row = conn.execute("select id from platform.clients where slug = %s", (slug,)).fetchone()
        if row is None:
            raise LookupError(f"no client {slug!r}")
        return railway.deprovision(open_context(conn, row[0], settings, mgmt, http))
