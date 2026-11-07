"""Owner console API (#29): create clients, follow their provisioning, retry a failed step.

Creating a client inserts it and its provisioning steps (`pending`, in `seq` order) in one
transaction; the provisioning worker (#24, #25) picks the steps up. Provisioning is queued the
moment the client exists, so the workspace code is editable only in the create form, where the
console suggests one; after that it never changes. Publishing is `POST /owner/clients/{id}/publish`
(#23, `zm_api.publish`). `GET /owner/clients/overview` is the owner dashboard (#47).
"""

import asyncio
import re
import secrets
import string
import time
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import releases
from zm_api.context import ClientContext, OwnerCtx, Resolver, owner_router
from zm_api.errors import (
    ApiError,
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

OVERVIEW_TTL = 60.0
"""Seconds the dashboard's answer is reused: a down engine turns red within this."""
PROBE_TIMEOUT = 3.0
"""Seconds a client's vault, then its engine, get to answer."""
clock = time.monotonic  # tests move it

_USERS = text(
    "select count(*) filter (where status = 'active') as active,"
    " count(*) filter (where status = 'invited') as invited,"
    " count(*) filter (where status = 'deactivated') as deactivated from app.users"
)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def _vault(ctx: ClientContext) -> tuple[Probe, UserCounts | None, releases.ReleaseRow | None]:
    """`select 1`, then the user counts and the active release."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT), ctx.db.connect() as conn:
            await conn.execute(text("select 1"))
            up = Probe(up=True, ms=_ms(start))
            users = UserCounts(**(await conn.execute(_USERS)).mappings().one())
            return up, users, await releases.active_release(conn)
    except (SQLAlchemyError, OSError, TimeoutError):
        return Probe(up=False, ms=None), None, None


async def _engine(
    ctx: ClientContext, flows: dict[str, str] | None
) -> tuple[Probe, list[FlowCases] | None]:
    """Operaton's /health, then the running instances of each flow's main process (`flows`: flow
    key → process key; None when the vault didn't answer)."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            await ctx.operaton.health()
            up = Probe(up=True, ms=_ms(start))
            if flows is None:
                return up, None
            return up, [
                FlowCases(flow=flow, count=await ctx.operaton.count_instances(key))
                for flow, key in flows.items()
            ]
    except (ApiError, TimeoutError):  # OperatonError, or NotFound with /health turned off
        return Probe(up=False, ms=None), None


async def _overview(resolver: Resolver, row: Client) -> ClientOverview:
    out = ClientOverview(id=row.id, slug=row.slug, status=row.status)
    try:
        ctx = await resolver.context(row)
    except ServiceUnavailable:  # not ready: nothing to probe yet
        return out
    except InvalidToken:  # its secrets don't open with this SECRETS_ENCRYPTION_KEY: unreachable
        down = Probe(up=False, ms=None)
        out.health = Health(operaton=down, db=down)
        return out
    db, out.users, active = await _vault(ctx)
    flows = None if not db.up else active.process_keys if active else {}
    operaton, out.cases_in_progress = await _engine(ctx, flows)
    out.health = Health(operaton=operaton, db=db)
    if active:
        out.last_release = LastRelease(version=active.version, published_at=active.published_at)
    return out


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
    # ponytail: no lock, so two requests after expiry both probe; fine for a few owners
    now, cached = clock(), getattr(request.app.state, "overview", None)
    if cached is not None and now - cached[0] < OVERVIEW_TTL:
        return cached[1]
    async with _platform(request).connect() as conn:
        rows = await conn.execute(text("select * from platform.clients order by created_at desc"))
        found = [Client(**r) for r in rows.mappings()]
    # ponytail: this opens every ready client's resources; past Resolver's max_clients (32) the
    # dashboard would churn them, so probe with short-lived ones then
    resolver = request.app.state.resolver
    out = list(await asyncio.gather(*(_overview(resolver, row) for row in found)))
    request.app.state.overview = (now, out)
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
        return await _detail(conn, client_id)


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
        return await _detail(conn, client_id)
