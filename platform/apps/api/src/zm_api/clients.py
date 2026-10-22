"""Owner console API (#29): create clients, follow their provisioning, retry a failed step.

Creating a client inserts it and its provisioning steps (`pending`, in `seq` order) in one
transaction; the provisioning worker (#24, #25) picks the steps up. Provisioning is queued the
moment the client exists, so the workspace code is editable only in the create form, where the
console suggests one; after that it never changes. Publishing is `POST /owner/clients/{id}/publish`
(#23, `zm_api.publish`).
"""

import re
import secrets
import string
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api.context import OwnerCtx, owner_router
from zm_api.errors import Conflict, FieldError, NotFound, UnprocessableContent

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
            if "workspace_code" in str(exc.orig):
                raise Conflict(
                    "Workspace code taken",
                    errors=_field("/workspace_code", "This workspace code is already taken"),
                ) from None
            raise Conflict(
                "Slug taken", errors=_field("/slug", "Another client already uses this slug")
            ) from None
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
    """Re-queue the failed (or `needs_human`) step only; steps already done are left alone."""
    async with _platform(request).begin() as conn:
        exists = await conn.execute(
            text("select 1 from platform.clients where id = :id"), {"id": client_id}
        )
        if exists.first() is None:
            raise NotFound("No such client")
        requeued = await conn.execute(
            text(
                "update platform.provisioning_steps set status = 'pending', attempts = 0,"
                " last_error = null, updated_at = now() where client_id = :id"
                " and status in ('failed', 'needs_human') returning step"
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
