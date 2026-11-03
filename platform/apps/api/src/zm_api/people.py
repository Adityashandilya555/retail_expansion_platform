"""People management (S2-23): the owner invites a client's admin; the client admin invites people,
sets their department levels and deactivates them. They can't change the flow, forms or UI.

- An invite is a one-time token `<client slug>.<random>` valid for 7 days. Only the random part's
  sha256 is kept; the whole token is sealed (`zm_api.secrets`) until the worker has emailed
  `CLIENT_APP_BASE_URL/invite/<token>` (`zm_worker.invites`). `POST /auth/invite/accept`
  (`zm_api.auth`) sets the password and makes the person `active`.
- Memberships may only name departments and levels of the active release's org (else 422).
  Every change is mirrored into the client's Operaton at once (`users.sync_user`); requests read
  memberships from the database, so a new level applies on the person's next request.
- Deactivating revokes the person's refresh tokens and drops their Operaton groups.
"""

import secrets as token_bytes
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import releases, secrets
from zm_api.auth import Membership, _sha
from zm_api.context import ClientContext, ClientCtx, UserId, client_context, owner_router
from zm_api.errors import Conflict, FieldError, Forbidden, NotFound, UnprocessableContent
from zm_api.registry import get_client
from zm_api.users import sync_user
from zm_schema.models import Department

Status = Literal["invited", "active", "deactivated"]
Email = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class InviteIn(BaseModel):
    email: Email
    name: Name


class UserIn(InviteIn):
    memberships: list[Membership] = []


class UserPatch(BaseModel):
    memberships: list[Membership] | None = None
    """Replaces every membership; leave out to keep them."""
    status: Literal["active", "deactivated"] | None = None
    """`active` reactivates (an invitee who never accepted goes back to `invited`)."""


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    status: Status
    is_client_admin: bool
    memberships: list[Membership]
    created_at: datetime


_USERS = (
    "select u.id, u.email, u.name, u.status, u.is_client_admin, u.created_at,"
    " coalesce(jsonb_agg(jsonb_build_object('department', m.department, 'level', m.level,"
    " 'scope', m.scope) order by m.department) filter (where m.user_id is not null), '[]')"
    " as memberships from app.users u left join app.memberships m on m.user_id = u.id"
)


async def _users(conn: AsyncConnection, where: str, **params: object) -> list[UserOut]:
    """`where` is one of this module's constant conditions, never input."""
    rows = await conn.execute(text(f"{_USERS} where {where} group by u.id order by u.name"), params)
    return [UserOut(**r) for r in rows.mappings()]


async def _user(ctx: ClientContext, user_id: UUID) -> UserOut:
    async with ctx.db.connect() as conn:
        found = await _users(conn, "u.id = :id", id=user_id)
    if not found:
        raise NotFound("No such person")
    return found[0]


async def departments(conn: AsyncConnection) -> list[Department]:
    """The active release's departments; none before the first publish."""
    release = await releases.active_release(conn)
    org = release.workflow["org"]["departments"] if release else []
    return [Department.model_validate(d) for d in org]


def membership_errors(org: list[Department], memberships: list[Membership]) -> list[FieldError]:
    levels = {d.key: {lv.n for lv in d.levels} for d in org}
    errors, seen = [], set()
    for i, m in enumerate(memberships):
        if m.department not in levels:
            message = f"{m.department} isn't a department in the published release"
        elif m.level not in levels[m.department]:
            message = f"{m.department} has no level {m.level} in the published release"
        elif m.department in seen:
            message = f"{m.department} is listed twice"
        else:
            seen.add(m.department)
            continue
        errors.append(FieldError(path=f"/memberships/{i}", message=message))
    return errors


async def _set_memberships(conn: AsyncConnection, user_id: UUID, ms: list[Membership]) -> None:
    errors = membership_errors(await departments(conn), ms)
    if errors:
        raise UnprocessableContent("Some memberships are not valid", errors=errors)
    await conn.execute(text("delete from app.memberships where user_id = :id"), {"id": user_id})
    for m in ms:
        await conn.execute(
            text(
                "insert into app.memberships (user_id, department, level, scope)"
                " values (:id, :d, :l, :s)"
            ),
            {"id": user_id, "d": m.department, "l": m.level, "s": m.scope},
        )


async def invite(ctx: ClientContext, body: UserIn, *, admin: bool = False) -> UserOut:
    """An `invited` person with their memberships and a one-time invite for the worker to email.
    Someone invited who never accepted gets this name and these memberships (a client admin stays
    one) and a new invite in place of the old, whose link stops working; anyone else is a 409."""
    raw = token_bytes.token_urlsafe(32)
    async with ctx.db.begin() as conn:
        user_id = (
            await conn.execute(
                text(
                    "insert into app.users (email, name, is_client_admin) values (:e, :n, :a)"
                    " on conflict (email) do update set name = excluded.name,"
                    " is_client_admin = app.users.is_client_admin or excluded.is_client_admin"
                    " where app.users.status = 'invited' returning id"
                ),
                {"e": body.email, "n": body.name, "a": admin},
            )
        ).scalar_one_or_none()
        if user_id is None:
            raise Conflict(
                "Email taken",
                errors=[FieldError(path="/email", message="Someone here already uses this email")],
            )
        await _set_memberships(conn, user_id, body.memberships)
        await conn.execute(text("delete from app.invites where user_id = :id"), {"id": user_id})
        await conn.execute(
            text(
                "insert into app.invites (user_id, token_hash, sealed, expires_at)"
                " values (:id, :h, :sealed, now() + interval '7 days')"
            ),
            {
                "id": user_id,
                "h": _sha(raw),
                "sealed": secrets.seal({"token": f"{ctx.client.slug}.{raw}"}),
            },
        )
    return await _user(ctx, user_id)


# --- the owner invites a client's admin --------------------------------------------------------

owner_routes = owner_router()


@owner_routes.post("/clients/{client_id}/client-admin", status_code=201)
async def invite_client_admin(client_id: UUID, body: InviteIn, request: Request) -> UserOut:
    """The client's admin, invited by email (they manage people, never the flow, forms or UI)."""
    async with request.app.state.platform.engine().connect() as conn:
        row = await get_client(conn, "id", client_id)
    if row is None:
        raise NotFound("No such client")
    ctx = await request.app.state.resolver.context(row)  # 503 until it's provisioned
    return await invite(ctx, UserIn(**body.model_dump()), admin=True)


# --- the client admin: /app/admin/* ------------------------------------------------------------


async def client_admin(ctx: ClientCtx, uid: UserId) -> UUID:
    async with ctx.db.connect() as conn:
        allowed = (
            await conn.execute(
                text("select is_client_admin from app.users where id = :id and status = 'active'"),
                {"id": uid},
            )
        ).scalar_one_or_none()
    if not allowed:
        raise Forbidden("Only the client admin manages people")
    return uid


routes = APIRouter(
    prefix="/app/admin", tags=["app"], dependencies=[Depends(client_context), Depends(client_admin)]
)


@routes.get("/users")
async def list_users(ctx: ClientCtx, status: Status | None = None) -> list[UserOut]:
    async with ctx.db.connect() as conn:
        return await _users(conn, "u.status = :status" if status else "true", status=status)


@routes.post("/users", status_code=201)
async def invite_user(body: UserIn, ctx: ClientCtx) -> UserOut:
    return await invite(ctx, body)


@routes.patch("/users/{user_id}")
async def update_user(
    user_id: UUID, body: UserPatch, ctx: ClientCtx, me: Annotated[UUID, Depends(client_admin)]
) -> UserOut:
    """New memberships and/or status, then the Operaton mirror. If Operaton fails (502) the change
    is kept; sending the same request again finishes the mirror."""
    if body.status == "deactivated" and user_id == me:
        raise Conflict("You can't deactivate yourself")
    async with ctx.db.begin() as conn:
        found = await conn.execute(
            text("select 1 from app.users where id = :id for update"), {"id": user_id}
        )
        if found.first() is None:
            raise NotFound("No such person")
        if body.memberships is not None:
            await _set_memberships(conn, user_id, body.memberships)
        if body.status == "deactivated":
            await conn.execute(
                text("update app.users set status = 'deactivated' where id = :id"), {"id": user_id}
            )
            await conn.execute(
                text(
                    "update app.refresh_tokens set revoked_at = now()"
                    " where user_id = :id and revoked_at is null"
                ),
                {"id": user_id},
            )
        elif body.status == "active":
            await conn.execute(
                text(
                    "update app.users set status = case when password_hash is null"
                    " then 'invited' else 'active' end where id = :id"
                ),
                {"id": user_id},
            )
    await sync_user(ctx, user_id)
    return await _user(ctx, user_id)


@routes.get("/departments")
async def list_departments(ctx: ClientCtx) -> list[Department]:
    """What memberships may name: the active release's departments and levels."""
    async with ctx.db.connect() as conn:
        return await departments(conn)
