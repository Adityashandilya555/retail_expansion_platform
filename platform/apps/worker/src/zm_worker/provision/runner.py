"""Provisioning queue (S2-02a, #24): rows of platform.provisioning_steps, claimed SKIP LOCKED (D8).

A step is claimable when every earlier step (lower seq) of its client is done and it is
- pending, and its backoff has passed (BACKOFF × (2^attempts − 1): 0 s, 10 s, 30 s, 70 s, 150 s);
- running, but its worker stopped heartbeating for LEASE (killed mid-step);
- needs_human, and RECHECK has passed (the step re-checks whether the person did it).
Steps are idempotent, so a step re-run after a kill just finds its earlier work.

The claim's `updated_at` is the lease token: heartbeats and the final update only match while it
is unchanged, so a worker whose lease ran out can't overwrite the worker that took over.
"""

import logging
import os
import secrets as random
import string
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

from zm_api import secrets as sealed

log = logging.getLogger("zm_worker.provision")

MAX_ATTEMPTS = 5
BACKOFF = timedelta(seconds=10)
LEASE = timedelta(minutes=2)
RECHECK = timedelta(minutes=1)
STARTUP_TIMEOUT = 600.0  # a new Supabase project or Operaton deployment gets 10 minutes
POLL_SECONDS = 10.0
ALPHABET = string.ascii_letters + string.digits  # URL-safe, so it goes into DSNs unescaped
NOT_SECRET = {"supabase_ref", "pooler_host", "project_name"}  # useful in errors, kept readable


def password() -> str:
    return "".join(random.choice(ALPHABET) for _ in range(32))


class NeedsHuman(Exception):
    """A person has to act; the message is the exact click path."""


class Stopped(Exception):
    """The worker is shutting down: the step is handed back unchanged, without an attempt."""


class LeaseLost(Exception):
    """Another worker took the step over (this one stalled past LEASE)."""


@dataclass(frozen=True)
class Settings:
    access_token: str
    org: str
    region: str
    instance_size: str
    prefix: str

    @classmethod
    def from_env(cls) -> "Settings | None":
        """None (provisioning off) until the token, the org and the project prefix are all set.
        The prefix has no default, so a test or dev worker can't touch production's `zm-` projects.
        """
        token, org = os.environ.get("SUPABASE_ACCESS_TOKEN"), os.environ.get("SUPABASE_ORG_ID")
        prefix = os.environ.get("SUPABASE_PROJECT_PREFIX")
        if not (token and org and prefix):
            return None
        return cls(
            access_token=token,
            org=org,
            region=os.environ.get("SUPABASE_REGION", "ap-southeast-1"),
            instance_size=os.environ.get("SUPABASE_CLIENT_INSTANCE_SIZE", "micro"),
            prefix=prefix,
        )


@dataclass
class Context:
    """What a step sees: its client, the open secrets, HTTP clients and ways to persist."""

    client_id: Any
    slug: str
    settings: Settings
    secrets: dict[str, Any]
    mgmt: httpx.Client  # Management API, carries the access token
    http: httpx.Client  # the client's own project (no Management token)
    persist_secrets: Callable[[dict[str, Any]], None]
    persist_columns: Callable[[dict[str, Any]], None]
    heartbeat: Callable[[], None]
    sleep: Callable[[float], None] = time.sleep  # raises Stopped when the worker stops
    clock: Callable[[], float] = time.monotonic

    def seal(self, **values: Any) -> None:
        """Merge into clients.secrets and write it at once (before the call that needs it)."""
        self.secrets.update(values)
        self.persist_secrets(self.secrets)

    def register(self, **columns: Any) -> None:
        """Set plain columns of platform.clients (supabase_ref, bucket, …)."""
        self.persist_columns(columns)


Step = Callable[[Context], None]

CLAIM = """
    with next as (
        select s.client_id, s.step
        from platform.provisioning_steps s
        where s.step = any(%(known)s)
          and (%(client)s::uuid is null or s.client_id = %(client)s::uuid)
          and (
                (s.status = 'pending'
                 and s.updated_at <= now() - %(backoff)s * (power(2, s.attempts) - 1))
             or (s.status = 'running' and s.updated_at <= now() - %(lease)s)
             or (s.status = 'needs_human' and s.updated_at <= now() - %(recheck)s)
          )
          and not exists (
                select 1 from platform.provisioning_steps e
                where e.client_id = s.client_id and e.seq < s.seq and e.status <> 'done')
        order by s.updated_at
        limit 1
        for update of s skip locked
    )
    update platform.provisioning_steps s
    set status = 'running', updated_at = now()
    from next
    where s.client_id = next.client_id and s.step = next.step
    returning s.client_id, s.step, s.attempts, s.updated_at
"""


def _exec(conn: psycopg.Connection, query: Any, params: Sequence[Any]) -> tuple[Any, ...] | None:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        return cur.fetchone() if cur.description else None


def claim(
    conn: psycopg.Connection, known: Sequence[str], client: Any = None
) -> tuple[Any, str, int, datetime] | None:
    """Take the next runnable step (client_id, step, attempts, lease token), mark it running.
    `client` limits it to one client (tests on a shared database)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            CLAIM,
            {
                "known": list(known),
                "client": client,
                "backoff": BACKOFF,
                "lease": LEASE,
                "recheck": RECHECK,
            },
        )
        return cur.fetchone()


def redact(text: str, secrets: Mapping[str, Any]) -> str:
    """Replace every secret value in an error text (e.g. one an API echoed back) with ***."""
    for key, value in secrets.items():
        if key not in NOT_SECRET and isinstance(value, str) and len(value) >= 8:
            text = text.replace(value, "***")
    return text


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):  # status and body; never the request headers
        request, body = exc.request, exc.response.text[:500]
        return f"{exc.response.status_code} from {request.method} {request.url.path}: {body}"
    return str(exc) or type(exc).__name__


def failure(
    exc: Exception, attempts: int, secrets: Mapping[str, Any], slug: str
) -> tuple[str, str, int]:
    """(status, last_error, attempts) after a failed attempt; secrets never reach either."""
    log.error("step failed: %s", redact(traceback.format_exc(), secrets), extra={"client": slug})
    attempts += 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
    return status, redact(_describe(exc), secrets)[:1000], attempts


def attempt(step: Step, ctx: Context, attempts: int) -> tuple[str, str | None, int]:
    """Run one attempt: (status, last_error, attempts) to store."""
    try:
        step(ctx)
    except NeedsHuman as exc:
        return "needs_human", redact(str(exc), ctx.secrets), attempts
    except Stopped:
        return "pending", None, attempts
    except Exception as exc:
        return failure(exc, attempts, ctx.secrets, ctx.slug)
    return "done", None, attempts


def open_context(
    conn: psycopg.Connection,
    client_id: Any,
    settings: Settings,
    mgmt: httpx.Client,
    http: httpx.Client,
    step: str | None = None,
    lease: list[datetime] | None = None,
    stop: threading.Event | None = None,
) -> Context:
    """A Context on one client row. With `step` and `lease`, its heartbeat keeps that claim."""
    row = _exec(conn, "select slug, secrets from platform.clients where id = %s", (client_id,))
    if row is None:
        raise LookupError(f"no client {client_id}")
    slug, secrets = row

    def persist_columns(columns: dict[str, Any]) -> None:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(name)) for name in columns
        )
        _exec(
            conn,
            sql.SQL("update platform.clients set {}, updated_at = now() where id = %s").format(
                assignments
            ),
            (*columns.values(), client_id),
        )

    def heartbeat() -> None:
        if step is None or lease is None:
            return
        row = _exec(
            conn,
            "update platform.provisioning_steps set updated_at = now()"
            " where client_id = %s and step = %s and status = 'running' and updated_at = %s"
            " returning updated_at",
            (client_id, step, lease[0]),
        )
        if row is None:
            raise LeaseLost(f"{step} was taken over by another worker")
        lease[0] = row[0]

    def sleep(seconds: float) -> None:
        if stop is None:
            time.sleep(seconds)
        elif stop.wait(seconds):
            raise Stopped

    return Context(
        client_id=client_id,
        slug=slug,
        settings=settings,
        secrets=sealed.unseal(secrets) if secrets else {},
        mgmt=mgmt,
        http=http,
        persist_secrets=lambda data: _exec(
            conn,
            "update platform.clients set secrets = %s, updated_at = now() where id = %s",
            (sealed.seal(data), client_id),
        ),
        persist_columns=persist_columns,
        heartbeat=heartbeat,
        sleep=sleep,
    )


def run_one(
    conn: psycopg.Connection,
    steps: Mapping[str, Step],
    settings: Settings,
    mgmt: httpx.Client,
    http: httpx.Client,
    stop: threading.Event | None = None,
    client: Any = None,
) -> bool:
    """Claim and run one step; False when nothing is runnable now."""
    claimed = claim(conn, list(steps), client)
    if claimed is None:
        return False
    client_id, step, attempts, token = claimed
    lease = [token]
    slug = str(client_id)
    try:  # a broken row or key is the step's error, like any other
        ctx = open_context(conn, client_id, settings, mgmt, http, step, lease, stop)
    except Exception as exc:
        status, error, attempts = failure(exc, attempts, {}, slug)
    else:
        slug = ctx.slug
        log.info("step %s: attempt %d", step, attempts + 1, extra={"client": slug})
        status, error, attempts = attempt(steps[step], ctx, attempts)
    kept = _exec(
        conn,
        "update platform.provisioning_steps"
        " set status = %s::platform.step_status, attempts = %s, last_error = %s,"
        " updated_at = now()"
        " where client_id = %s and step = %s and status = 'running' and updated_at = %s"
        " returning 1",
        (status, attempts, error, client_id, step, lease[0]),
    )
    if kept is None:
        log.warning(
            "step %s: taken over by another worker, result dropped", step, extra={"client": slug}
        )
        return True
    if status == "failed":
        _exec(
            conn,
            "update platform.clients set status = 'failed', updated_at = now() where id = %s",
            (client_id,),
        )
    log.info("step %s: %s", step, status, extra={"client": slug})
    return True


def run_pending(
    conn: psycopg.Connection,
    steps: Mapping[str, Step],
    settings: Settings,
    mgmt: httpx.Client,
    http: httpx.Client,
    stop: threading.Event | None = None,
    client: Any = None,
) -> int:
    """Run steps until none is runnable now, or the worker stops; returns how many ran."""
    ran = 0
    while not (stop is not None and stop.is_set()) and run_one(
        conn, steps, settings, mgmt, http, stop, client
    ):
        ran += 1
    return ran
