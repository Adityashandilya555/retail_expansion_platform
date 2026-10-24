"""Provisioning queue (S2-02a, #24): rows of platform.provisioning_steps, claimed SKIP LOCKED (D8).

A step is claimable when every earlier step (lower seq) of its client is done and it is
- pending, and its backoff has passed (BACKOFF × (2^attempts − 1): 0 s, 10 s, 30 s, 70 s, 150 s);
- running, but its worker stopped heartbeating for LEASE (killed mid-step);
- needs_human, and RECHECK has passed (the step re-checks whether the person did it).
Steps are idempotent, so a step re-run after a kill just finds its earlier work.
"""

import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
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


class NeedsHuman(Exception):
    """A person has to act; the message is the exact click path."""


@dataclass(frozen=True)
class Settings:
    access_token: str
    org: str
    region: str
    instance_size: str
    prefix: str
    # Railway (S2-02b): the step that needs them fails with a clear error while they're unset
    railway_token: str | None = None
    railway_token_type: str = "project"  # project → Project-Access-Token header; else Bearer
    railway_project: str | None = None
    railway_environment: str | None = None
    railway_region: str = "asia-southeast1-eqsg3a"
    operaton_image: str | None = None

    @classmethod
    def from_env(cls) -> "Settings | None":
        """None (provisioning off) without a Management API token, e.g. in local compose."""
        token, org = os.environ.get("SUPABASE_ACCESS_TOKEN"), os.environ.get("SUPABASE_ORG_ID")
        if not (token and org):
            return None
        return cls(
            access_token=token,
            org=org,
            region=os.environ.get("SUPABASE_REGION", "ap-southeast-1"),
            instance_size=os.environ.get("SUPABASE_CLIENT_INSTANCE_SIZE", "micro"),
            prefix=os.environ.get("SUPABASE_PROJECT_PREFIX", "zm-"),
            railway_token=os.environ.get("RAILWAY_API_TOKEN") or None,
            railway_token_type=os.environ.get("RAILWAY_API_TOKEN_TYPE", "project"),
            railway_project=os.environ.get("RAILWAY_PROJECT_ID") or None,
            railway_environment=os.environ.get("RAILWAY_ENVIRONMENT_ID") or None,
            railway_region=os.environ.get("RAILWAY_REGION", "asia-southeast1-eqsg3a"),
            operaton_image=os.environ.get("OPERATON_IMAGE") or None,
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
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    # (template_key, newest workflow_json of that key) from platform.templates
    template: Callable[[], tuple[str, dict[str, Any]] | None] = lambda: None

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
    returning s.client_id, s.step, s.attempts
"""


def _exec(conn: psycopg.Connection, query: Any, params: Sequence[Any]) -> tuple[Any, ...] | None:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        return cur.fetchone() if cur.description else None


def claim(conn: psycopg.Connection, known: Sequence[str]) -> tuple[Any, str, int] | None:
    """Take the next runnable step (client_id, step, attempts) and mark it running."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            CLAIM,
            {"known": list(known), "backoff": BACKOFF, "lease": LEASE, "recheck": RECHECK},
        )
        return cur.fetchone()


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):  # status and body; never the request headers
        request, body = exc.request, exc.response.text[:500]
        return f"{exc.response.status_code} from {request.method} {request.url.path}: {body}"
    return (str(exc) or type(exc).__name__)[:1000]


def attempt(step: Step, ctx: Context, attempts: int) -> tuple[str, str | None, int]:
    """Run one attempt: (status, last_error, attempts) to store."""
    try:
        step(ctx)
    except NeedsHuman as exc:
        return "needs_human", str(exc), attempts
    except Exception as exc:
        log.exception("step failed", extra={"client": ctx.slug})
        attempts += 1
        return ("failed" if attempts >= MAX_ATTEMPTS else "pending"), _describe(exc), attempts
    return "done", None, attempts


def open_context(
    conn: psycopg.Connection,
    client_id: Any,
    settings: Settings,
    mgmt: httpx.Client,
    http: httpx.Client,
    step: str | None = None,
) -> Context:
    """A Context on one client row; `step` is the row its heartbeat refreshes."""
    row = _exec(conn, "select slug, secrets from platform.clients where id = %s", (client_id,))
    assert row is not None  # provisioning_steps.client_id references clients
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
        template=lambda: _exec(
            conn,
            "select t.key, t.workflow_json from platform.templates t"
            " join platform.clients c on c.template_key = t.key"
            " where c.id = %s order by t.version desc limit 1",
            (client_id,),
        ),
        heartbeat=lambda: _exec(  # without a step (deprovision) it matches no row
            conn,
            "update platform.provisioning_steps set updated_at = now()"
            " where client_id = %s and step = %s",
            (client_id, step),
        ),
    )


def run_one(
    conn: psycopg.Connection,
    steps: Mapping[str, Step],
    settings: Settings,
    mgmt: httpx.Client,
    http: httpx.Client,
) -> bool:
    """Claim and run one step; False when nothing is runnable now."""
    claimed = claim(conn, list(steps))
    if claimed is None:
        return False
    client_id, step, attempts = claimed
    _exec(
        conn,
        "update platform.clients set status = 'provisioning', updated_at = now()"
        " where id = %s and status = 'new'",
        (client_id,),
    )
    ctx = open_context(conn, client_id, settings, mgmt, http, step)
    slug = ctx.slug
    log.info("step %s: attempt %d", step, attempts + 1, extra={"client": slug})
    status, error, attempts = attempt(steps[step], ctx, attempts)
    _exec(
        conn,
        "update platform.provisioning_steps"
        " set status = %s::platform.step_status, attempts = %s, last_error = %s,"
        " updated_at = now() where client_id = %s and step = %s",
        (status, attempts, error, client_id, step),
    )
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
) -> int:
    """Run steps until none is runnable now; returns how many ran."""
    ran = 0
    while run_one(conn, steps, settings, mgmt, http):
        ran += 1
    return ran
