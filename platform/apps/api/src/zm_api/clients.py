"""Owner console API (#29): create clients, follow their provisioning, retry a failed step.

Creating a client inserts it and its provisioning steps (`pending`, in `seq` order) in one
transaction; the provisioning worker (#24, #25) picks the steps up. Provisioning is queued the
moment the client exists, so the workspace code is editable only in the create form, where the
console suggests one; after that it never changes. Publishing is `POST /owner/clients/{id}/publish`
(#23, `zm_api.publish`). `GET /owner/clients/overview` is the owner dashboard (#47).
"""

import asyncio
import logging
import re
import secrets
import string
import time
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from zm_api import releases
from zm_api.context import ClientContext, OwnerCtx, open_context, owner_router
from zm_api.errors import (
    Conflict,
    FieldError,
    NotFound,
    ServiceUnavailable,
    UnprocessableContent,
)
from zm_api.registry import Client

STEPS = (
    "supabase_project",  # S2-02a (#24)
    "vault_migrations",
    "data_api_off",
    "bucket",
    "registry",
    "operaton_service",  # S2-02b (#25)
    "operaton_health",
    "seed_draft",
)
"""Provisioning steps in `seq` order; each is idempotent, and the worker runs them in order."""

SLUG_MAX = 40  # ends up in `zm-<slug>` and `operaton-<slug>.railway.internal`
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")]
WorkspaceCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{2,63}$")]
ClientStatus = Literal["new", "provisioning", "ready", "failed", "suspended"]
StepStatus = Literal["pending", "running", "done", "failed", "needs_human"]
_log = logging.getLogger("zm_api.clients")


def slugify(name: str) -> str:
    """`Matrix Retail` → `matrix-retail`; empty when the name has no ASCII letters or digits."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:SLUG_MAX].strip("-")


def suggest_code(slug: str) -> str:
    """`<SLUG>-<4 random A-Z0-9>`, e.g. `MATRIX-RETAIL-7Q2K`."""
    alphabet = string.ascii_uppercase + string.digits
    return f"{slug.upper()}-{''.join(secrets.choice(alphabet) for _ in range(4))}"


# --- API shapes ------------------------------------------------------------------------------


class TemplateOut(BaseModel):
    key: str
    name: str


class ClientIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    slug: Slug | None = None
    """Defaults from the name."""
    workspace_code: WorkspaceCode | None = None
    """Defaults to `<SLUG>-<4 random A-Z0-9>`; fixed once the client exists."""
    template_key: str = Field(min_length=1)


class StepOut(BaseModel):
    step: str
    seq: int
    status: StepStatus
    attempts: int
    last_error: str | None
    """For `needs_human`, the exact click path for a person."""
    updated_at: datetime


class ReleaseOut(BaseModel):
    version: int
    deployment_id: str
    published_at: datetime
    published_by: UUID | None


class ClientOut(BaseModel):
    id: UUID
    slug: str
    name: str
    workspace_code: str
    template_key: str
    status: ClientStatus
    created_at: datetime
    last_release: int | None
    """The newest release's version; null before the first publish."""


class ClientDetail(ClientOut):
    steps: list[StepOut]
    releases: list[ReleaseOut]
    """Newest first."""


class UserCounts(BaseModel):
    active: int
    invited: int
    deactivated: int


class FlowCases(BaseModel):
    flow: str
    """The flow's key in the active release."""
    count: int
    """Its cases still running, whichever release they started on."""


class LastRelease(BaseModel):
    version: int
    published_at: datetime


class Probe(BaseModel):
    up: bool
    ms: float | None
    """How long it took to answer; null when it failed or didn't answer within PROBE_TIMEOUT."""


class Health(BaseModel):
    operaton: Probe
    """Operaton Run's `GET /health` over the private network."""
    db: Probe
    """`select 1` on the client's vault."""


class ClientOverview(BaseModel):
    """Counts and status only (PRODUCT.md, owner step 8): never a person's name or email, nor a
    form value. The console names the client from `GET /owner/clients`."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    slug: str
    status: ClientStatus
    """Its provisioning status. Only a ready client is probed; the rest is null until then."""
    users: UserCounts | None = None
    cases_in_progress: list[FlowCases] | None = None
    """Per flow of the active release; null when its vault or engine didn't answer."""
    last_release: LastRelease | None = None
    """The active release."""
    health: Health | None = None


# --- queries ---------------------------------------------------------------------------------

_CLIENTS = (
    "select id, slug, name, workspace_code, template_key, status, created_at,"
    " (select max(version) from platform.releases r where r.client_id = c.id) as last_release"
    " from platform.clients c"
)


async def _detail(conn: AsyncConnection, client_id: UUID) -> ClientDetail:
    row = (
        (await conn.execute(text(f"{_CLIENTS} where id = :id"), {"id": client_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFound("No such client")
    steps = await conn.execute(
        text(
            "select step, seq, status, attempts, last_error, updated_at"
            " from platform.provisioning_steps where client_id = :id order by seq"
        ),
        {"id": client_id},
    )
    releases = await conn.execute(
        text(
            "select version, deployment_id, published_at, published_by from platform.releases"
            " where client_id = :id order by version desc"
        ),
        {"id": client_id},
    )
    return ClientDetail(
        **row,
        steps=[StepOut(**s) for s in steps.mappings()],
        releases=[ReleaseOut(**r) for r in releases.mappings()],
    )


def _field(path: str, message: str) -> list[FieldError]:
    return [FieldError(path=path, message=message)]


_TAKEN = {
    "clients_slug_key": ("Slug taken", "/slug", "Another client already uses this slug"),
    "clients_workspace_code_key": (
        "Workspace code taken",
        "/workspace_code",
        "This workspace code is already taken",
    ),
}
"""Unique constraints on `platform.clients` → the 409 for the field they guard."""


# --- the owner dashboard (#47) ---------------------------------------------------------------

PROBE_TIMEOUT = 3.0
"""Seconds a client's vault (`select 1`) and its engine (`/health`) each get to answer; they are
probed at once. Only these two decide its dots."""
QUERY_TIMEOUT = 3.0
"""Seconds the follow-up reads get: the vault's user counts and active release, then the engine's
running cases per flow. Too slow or failing, they show as null while the dots stay green."""
PROBE_WORST = PROBE_TIMEOUT + 2 * QUERY_TIMEOUT
"""Longest a client's probe can take: vault `select 1` then its reads (the engine's /health runs
beside them), then the engine's counts."""
CLIENT_LIMIT = PROBE_WORST + 1
"""Hard cap on one client's whole entry, closing its vault engine and Operaton client included:
past it the client shows as unreachable, so a vault that stalls on close can't hold the lock."""
REGISTRY_TIMEOUT = 3.0
"""Seconds the platform registry gets to list the clients; past it the request fails (503)."""
OVERVIEW_TTL = 30.0
"""Seconds the dashboard's answer is reused, counted from when its probe started."""
CONSOLE_POLL = 15.0
"""The console's poll (POLL_MS in apps/console/src/ClientCards.tsx; a test keeps them equal).

Red within 60 s (#47): an engine that dies just after a probe is seen by the first probe started
after the answer expires (OVERVIEW_TTL), which the console asks for within one poll
(CONSOLE_POLL), and that answer arrives within REGISTRY_TIMEOUT + CLIENT_LIMIT:
30 + 15 + 3 + 10 = 58 s <= 60 s."""
RED_WITHIN = 60.0
clock = time.monotonic  # tests move it

_USERS = text(
    "select count(*) filter (where status = 'active') as active,"
    " count(*) filter (where status = 'invited') as invited,"
    " count(*) filter (where status = 'deactivated') as deactivated from app.users"
)
_DOWN = Probe(up=False, ms=None)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


@dataclass
class _Vault:
    probe: Probe = field(default_factory=lambda: _DOWN)
    users: UserCounts | None = None
    active: releases.ReleaseRow | None = None
    flows: dict[str, str] | None = None
    """Flow key → its main process key in the active release; None when they couldn't be read."""


async def _vault(ctx: ClientContext) -> _Vault:
    """`select 1` within PROBE_TIMEOUT, then the user counts and the active release within
    QUERY_TIMEOUT: when only those fail, the vault is still up."""
    out, start = _Vault(), time.perf_counter()
    try:
        async with AsyncExitStack() as stack:
            async with asyncio.timeout(PROBE_TIMEOUT):
                conn = await stack.enter_async_context(ctx.db.connect())
                await conn.execute(text("select 1"))
            out.probe = Probe(up=True, ms=_ms(start))
            async with asyncio.timeout(QUERY_TIMEOUT):
                users = UserCounts(**(await conn.execute(_USERS)).mappings().one())
                active = await releases.active_release(conn)
            out.users, out.active = users, active
            out.flows = active.process_keys if active else {}
    except Exception:  # down, too slow, or anything else: only this client is affected
        what = "reads" if out.probe.up else "probe"
        _log.warning(
            f"dashboard: vault {what} failed", extra={"client": ctx.client.slug}, exc_info=True
        )
    return out


async def _engine(ctx: ClientContext) -> Probe:
    """Operaton Run's /health within PROBE_TIMEOUT."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            await ctx.operaton.health()
        return Probe(up=True, ms=_ms(start))
    except Exception:  # OperatonError, NotFound with /health turned off, too slow, or anything else
        _log.warning(
            "dashboard: engine probe failed", extra={"client": ctx.client.slug}, exc_info=True
        )
        return _DOWN


async def _cases(ctx: ClientContext, flows: dict[str, str]) -> list[FlowCases] | None:
    """The running instances of each flow's main process, all at once, within QUERY_TIMEOUT."""
    try:
        async with asyncio.timeout(QUERY_TIMEOUT):
            counts = await asyncio.gather(
                *(ctx.operaton.count_instances(k) for k in flows.values())
            )
    except Exception:
        _log.warning(
            "dashboard: engine counts failed", extra={"client": ctx.client.slug}, exc_info=True
        )
        return None
    return [FlowCases(flow=f, count=n) for f, n in zip(flows, counts, strict=True)]


def _unreachable(row: Client) -> ClientOverview:
    return ClientOverview(
        id=row.id, slug=row.slug, status=row.status, health=Health(operaton=_DOWN, db=_DOWN)
    )


async def _overview(row: Client) -> ClientOverview:
    out = ClientOverview(id=row.id, slug=row.slug, status=row.status)
    if row.status != "ready":  # nothing to probe yet
        return out
    try:
        # its own vault engine and Operaton client, not the Resolver's: with more clients ready
        # than the Resolver keeps, it would close the ones other requests are using
        ctx = open_context(row, poolclass=NullPool)
    except Exception:  # no secrets or Operaton URL, or sealed with another key: unreachable
        _log.warning("dashboard: can't open the client", extra={"client": row.slug}, exc_info=True)
        return _unreachable(row)
    try:
        vault, operaton = await asyncio.gather(_vault(ctx), _engine(ctx))
        if operaton.up and vault.flows is not None:
            out.cases_in_progress = await _cases(ctx, vault.flows)
    finally:
        with suppress(Exception):  # closing is best effort; the answer stands
            await ctx.aclose()
    out.health = Health(operaton=operaton, db=vault.probe)
    out.users = vault.users
    if vault.active:
        out.last_release = LastRelease(
            version=vault.active.version, published_at=vault.active.published_at
        )
    return out


async def _bounded(row: Client) -> ClientOverview:
    """`_overview` within CLIENT_LIMIT; past it (e.g. a vault stalling on close) unreachable."""
    try:
        async with asyncio.timeout(CLIENT_LIMIT):
            return await _overview(row)
    except TimeoutError:
        _log.warning("dashboard: probe exceeded its limit", extra={"client": row.slug})
        return _unreachable(row)


@dataclass
class _Overview:
    """The dashboard's cached answer on `app.state.overview`, refreshed by one request at a time."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    at: float = 0.0
    answer: list[ClientOverview] | None = None
    generation: int = 0
    """Bumped by `forget_overview`, so a probe started before it doesn't store its answer."""

    def fresh(self, now: float) -> list[ClientOverview] | None:
        return self.answer if self.answer is not None and now - self.at < OVERVIEW_TTL else None


def _cache(app: Any) -> _Overview:
    cache = getattr(app.state, "overview", None)
    if cache is None:
        cache = app.state.overview = _Overview()
    return cache


def forget_overview(request: Request) -> None:
    """Drop the dashboard's cached answer: a client was created, re-queued or published."""
    cache = _cache(request.app)
    cache.answer, cache.generation = None, cache.generation + 1


# --- routes ----------------------------------------------------------------------------------

routes = owner_router()


def _platform(request: Request) -> Any:
    return request.app.state.platform.engine()


@routes.get("/templates")
async def list_templates(request: Request) -> list[TemplateOut]:
    """Starter templates for a new client (`zm-api templates sync` fills them)."""
    async with _platform(request).connect() as conn:
        rows = await conn.execute(
            text(
                "select distinct on (key) key, name from platform.templates"
                " order by key, version desc"
            )
        )
        return [TemplateOut(**r) for r in rows.mappings()]


@routes.get("/clients")
async def list_clients(request: Request) -> list[ClientOut]:
    async with _platform(request).connect() as conn:
        rows = await conn.execute(text(f"{_CLIENTS} order by created_at desc"))
        return [ClientOut(**r) for r in rows.mappings()]


@routes.get("/clients/overview")  # before /clients/{client_id}, which would take it for an id
async def clients_overview(request: Request) -> list[ClientOverview]:
    """Per client: account counts, cases in progress per flow, the active release, and the health
    of its Operaton and vault. Every client is probed at once, each with PROBE_TIMEOUT, so one
    that is down never holds up or fails the rest. The answer is kept OVERVIEW_TTL seconds."""
    cache = _cache(request.app)
    if (answer := cache.fresh(clock())) is not None:
        return answer
    async with cache.lock:  # one request probes; the others wait for its answer
        now = clock()
        if (answer := cache.fresh(now)) is not None:
            return answer
        generation = cache.generation
        try:
            async with asyncio.timeout(REGISTRY_TIMEOUT), _platform(request).connect() as conn:
                rows = await conn.execute(
                    text("select * from platform.clients order by created_at desc")
                )
                found = [Client(**r) for r in rows.mappings()]
        except TimeoutError:
            _log.warning("dashboard: the platform registry didn't answer")
            raise ServiceUnavailable("The client registry didn't answer; try again") from None
        # each probe opens its own vault connection: about two per client a minute per API process
        probed = await asyncio.gather(*(_bounded(row) for row in found), return_exceptions=True)
        out = []
        for row, got in zip(found, probed, strict=True):
            if isinstance(got, BaseException):  # never expected; only that client turns red
                _log.error("dashboard: probe crashed", extra={"client": row.slug}, exc_info=got)
                got = _unreachable(row)
            out.append(got)
        if cache.generation == generation:
            cache.at, cache.answer = now, out
        return out


@routes.post("/clients", status_code=201)
async def create_client(body: ClientIn, owner: OwnerCtx, request: Request) -> ClientDetail:
    """The client and its provisioning steps, in one transaction; the worker takes it from here."""
    slug = body.slug or slugify(body.name)
    if not slug:
        raise UnprocessableContent(
            "Choose a slug", errors=_field("/slug", "The name has no letters or digits to use")
        )
    code = body.workspace_code or suggest_code(slug)
    async with _platform(request).begin() as conn:
        found = await conn.execute(
            text("select 1 from platform.templates where key = :k limit 1"),
            {"k": body.template_key},
        )
        if found.first() is None:
            raise UnprocessableContent(
                "Unknown template", errors=_field("/template_key", "No such template")
            )
        try:
            client_id = (
                await conn.execute(
                    text(
                        "insert into platform.clients (slug, name, workspace_code, template_key,"
                        " status, created_by) values (:slug, :name, :code, :template,"
                        " 'provisioning', :by) returning id"
                    ),
                    {
                        "slug": slug,
                        "name": body.name,
                        "code": code,
                        "template": body.template_key,
                        "by": owner.owner_id,
                    },
                )
            ).scalar_one()
        except IntegrityError as exc:
            # asyncpg's error (the cause of SQLAlchemy's) names the constraint; anything else,
            # e.g. `created_by` pointing at a removed owner, isn't the person's input
            taken = _TAKEN.get(getattr(exc.orig.__cause__, "constraint_name", None))
            if taken is None:
                raise
            title, path, message = taken
            raise Conflict(title, errors=_field(path, message)) from None
        await conn.execute(
            text(
                "insert into platform.provisioning_steps (client_id, step, seq)"
                " select :id, step, seq from unnest(cast(:steps as text[]))"
                " with ordinality as s(step, seq)"
            ),
            {"id": client_id, "steps": list(STEPS)},
        )
        detail = await _detail(conn, client_id)
    forget_overview(request)  # after the commit, so the next probe sees it
    return detail


@routes.get("/clients/{client_id}")
async def get_client_detail(client_id: UUID, request: Request) -> ClientDetail:
    """The client with its provisioning steps and releases (the console polls this)."""
    async with _platform(request).connect() as conn:
        return await _detail(conn, client_id)


@routes.post("/clients/{client_id}/provisioning/retry")
async def retry_provisioning(client_id: UUID, request: Request) -> ClientDetail:
    """Re-queue the first failed (or `needs_human`) step only; every other step is left alone.

    Should the worker carry on past a stuck step, a later stuck one waits for the next retry."""
    async with _platform(request).begin() as conn:
        exists = await conn.execute(
            text("select 1 from platform.clients where id = :id"), {"id": client_id}
        )
        if exists.first() is None:
            raise NotFound("No such client")
        requeued = await conn.execute(
            text(
                "update platform.provisioning_steps set status = 'pending', attempts = 0,"
                " last_error = null, updated_at = now() where client_id = :id and seq = ("
                " select min(seq) from platform.provisioning_steps where client_id = :id"
                " and status in ('failed', 'needs_human')) returning step"
            ),
            {"id": client_id},
        )
        if requeued.first() is None:
            raise Conflict("No failed step to retry")
        await conn.execute(
            text(
                "update platform.clients set status = 'provisioning', updated_at = now()"
                " where id = :id and status = 'failed'"
            ),
            {"id": client_id},
        )
        detail = await _detail(conn, client_id)
    forget_overview(request)
    return detail
