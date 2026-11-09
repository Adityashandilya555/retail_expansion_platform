"""`zm-worker compat-live` (S2-09, #43, D14): would every client's live release still work with
this build? The `api` service runs it before each deploy (`.railway/railway.ts`), after the vault
migrations: each ready client's active release is validated and compiled with this build's schema
and UI packages (`zm_compiler.compat.check_document`). Any problem fails the pre-deploy, so Railway
keeps the running version. Client configs never leave production: this reads them in place.
"""

import logging
import sys
from collections.abc import Callable, Iterable
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from zm_compiler.compat import Problem, check_document
from zm_worker.dsn import libpq
from zm_worker.provision.runner import redact
from zm_worker.vault_migrate import ready_clients

log = logging.getLogger("zm_worker.compat_live")

Load = Callable[[str], tuple[int, dict[str, Any]] | None]


def active_release(dsn: str) -> tuple[int, dict[str, Any]] | None:
    """(version, workflow) of the client's active release, as the API's own role reads it."""
    with psycopg.connect(libpq(dsn), autocommit=True, row_factory=tuple_row) as conn:
        row = conn.execute("select version, workflow from app.releases where active").fetchone()
    return None if row is None else (row[0], row[1])


def check(clients: Iterable[tuple[str, bytes | None]], load: Load | None = None) -> list[Problem]:
    """Every problem in the clients' live releases. A vault that can't be read is one too: the
    gate can't vouch for a release it didn't see."""
    from zm_api import secrets as sealed

    read = load or active_release
    problems: list[Problem] = []
    for slug, box in clients:
        secrets: dict[str, Any] = sealed.unseal(box) if box else {}
        dsn = secrets.get("zm_api_dsn")
        if not dsn:
            log.info("skipped: no vault in the registry", extra={"client": slug})
            continue
        try:
            found = read(dsn)
        except Exception as e:  # its vault is down, or its role can't read releases
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            message = redact(first, secrets)  # never a password in the deploy log
            problems.append(
                Problem(f"client {slug}", "vault", f"couldn't read the active release: {message}")
            )
            continue
        if found is None:
            continue  # nothing published yet
        version, workflow = found
        problems += check_document(f"client {slug} v{version}", workflow)
    return problems


def main(platform_dsn: str) -> int:
    clients = ready_clients(platform_dsn)
    problems = check(clients, active_release)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        broken = len({p.source.split(" v")[0] for p in problems})
        print(
            f"compat-live: {broken} of {len(clients)} live release(s) would break with this build",
            file=sys.stderr,
        )
        return 1
    print(f"compat-live: {len(clients)} live release(s) validate and compile with this build")
    return 0
