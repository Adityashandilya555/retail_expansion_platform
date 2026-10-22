"""The submission reconciler (#26): one pass over every ready client.

For each client it finishes task submissions stuck between Operaton and the client's database
(`zm_api.tasks.reconcile`). One client failing (vault or engine down) is logged and doesn't stop
the others. Needs PLATFORM_DATABASE_URL and SECRETS_ENCRYPTION_KEY, like the API.
"""

import asyncio
import logging
from collections import Counter

from zm_api import tasks
from zm_api.context import PlatformRegistry, Resolver
from zm_api.registry import ready_clients

log = logging.getLogger("zm_worker.reconcile")


async def reconcile_all(older_than: float = 120.0) -> dict[str, Counter[str]]:
    """Client slug → counts of what happened (`committed`, `failed`, `skipped`, `error`)."""
    registry = PlatformRegistry()
    resolver = Resolver(registry)
    out: dict[str, Counter[str]] = {}
    try:
        async with registry.engine().connect() as conn:
            rows = await ready_clients(conn)
        for row in rows:
            try:
                ctx = await resolver.context(row)
                out[row.slug] = await tasks.reconcile(ctx, older_than=older_than)
            except Exception:
                log.exception("reconcile failed", extra={"client": row.slug})
                out[row.slug] = Counter(error=1)
                continue
            if out[row.slug]:
                log.info("reconciled %s", dict(out[row.slug]), extra={"client": row.slug})
    finally:
        await resolver.aclose()  # also disposes the platform pool
    return out


def run_once(older_than: float = 120.0) -> dict[str, Counter[str]]:
    return asyncio.run(reconcile_all(older_than))
