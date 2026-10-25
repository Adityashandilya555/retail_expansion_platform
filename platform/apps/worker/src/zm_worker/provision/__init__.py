"""Provisioning worker (S2-02a/b): builds each client's vault from platform.provisioning_steps.

The API inserts a new client's steps (S2-01); the worker runs those it knows, in seq order, in its
own thread, so a step that waits minutes (a new project, a deploy) never holds up the reconciler.
"""

import logging
import threading

import httpx
import psycopg

from zm_worker.dsn import libpq
from zm_worker.provision import supabase
from zm_worker.provision.runner import Settings, run_pending

log = logging.getLogger("zm_worker.provision")

STEPS = supabase.STEPS


def loop(platform_dsn: str, settings: Settings, stop: threading.Event, interval: float) -> None:
    """Run the queue every `interval` until `stop`, on one connection kept across passes
    (reopened after an error) instead of a new pooler session per tick."""
    conn: psycopg.Connection | None = None
    with (
        httpx.Client(
            base_url=supabase.API,
            headers={"Authorization": f"Bearer {settings.access_token}"},
            timeout=30,
        ) as mgmt,
        httpx.Client(timeout=30) as http,
    ):
        while not stop.is_set():
            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(libpq(platform_dsn), autocommit=True)
                run_pending(conn, STEPS, settings, mgmt, http, stop)
            except Exception:  # e.g. the registry is down: try again next pass
                log.exception("provisioning pass failed")
                if conn is not None:
                    conn.close()
                    conn = None
            stop.wait(interval)
    if conn is not None:
        conn.close()
