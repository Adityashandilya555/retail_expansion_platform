"""Provisioning worker (S2-02a/b): builds each client's vault from platform.provisioning_steps.

The API inserts a new client's steps (S2-01); the worker runs those it knows, in seq order.
"""

import httpx
import psycopg

from zm_worker.provision import supabase
from zm_worker.provision.runner import Settings, run_pending

STEPS = supabase.STEPS


def run(platform_dsn: str, settings: Settings) -> int:
    """One pass over the queue; returns how many steps ran."""
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
        return run_pending(conn, STEPS, settings, mgmt, http)
