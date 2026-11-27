"""People management (S2-23): the owner invites a client's admin; the client admin invites people,
sets their department levels and deactivates them. They can't change the flow, forms or UI.

- An invite is a one-time token `<client slug>.<random>` valid for 7 days. Only the random part's
  sha256 is kept; the whole token is sealed (`zm_api.secrets`) until the worker has emailed
  `CLIENT_APP_BASE_URL/invite/<token>` (`zm_worker.invites`). `POST /auth/invite/accept`
  (`zm_api.auth`) sets the password and makes the person `active`.
- The inviter gets the link once, always (#169): the response that creates or renews an invite
  carries `invite_path` with `Cache-Control: no-store`, so it works without email. No GET answers
  it, and the request log records route templates only. Inviting again kills the old link, so
  only the owner may (re-)invite a client admin; the client admin's route answers 403.
- Memberships may only name departments and levels of the active release's org (else 422).
  Every change is mirrored into the client's Operaton at once (`users.sync_user`), and retried by
  the reconciler if that fails; requests read memberships from the database, so a new level
  applies on the person's next request.
- Deactivating revokes the person's refresh tokens and drops their Operaton groups. The client's
  last active client admin can't be deactivated.
"""

import secrets as token_bytes
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints
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
    memberships: list[Membership] | None = None
    """Leave out to keep a pending invitee's memberships (none for someone new)."""


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


class InviteOut(UserOut):
    """The invited person and their new link, answered once, to the inviter only."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    invite_path: str
    """`/invite/<token>` on the client app. It works once; inviting again kills it."""
    invite_expires_at: datetime


NO_STORE: dict[int | str, dict[str, Any]] = {
    201: {"headers": {"Cache-Control": {"description": "`no-store`: the body holds the link."}}}
}


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


async def invite(ctx: ClientContext, body: UserIn, *, admin: bool = False) -> InviteOut:
    """An `invited` person with their memberships and a one-time invite, whose link goes back to
    the inviter and, sealed, to the worker to email. Someone invited who never accepted gets this
    name, these memberships if given (a client admin stays one) and a new invite in place of the
    old, whose link stops working; anyone else is a 409.

    `admin` is the owner's route: only it may invite or re-invite a client admin. Otherwise a
    client admin could renew another admin's pending invite and accept it as them (403). The
    answer is built before the commit, so a new link is never committed without being returned."""
    raw = token_bytes.token_urlsafe(32)
    token = f"{ctx.client.slug}.{raw}"
    async with ctx.db.begin() as conn:
        user_id = (
            await conn.execute(
                text(
                    "insert into app.users (email, name, is_client_admin) values (:e, :n, :a)"
                    " on conflict (email) do update set name = excluded.name,"
                    " is_client_admin = app.users.is_client_admin or excluded.is_client_admin"
                    " where app.users.status = 'invited' and (:a or not app.users.is_client_admin)"
                    " returning id"
                ),
                {"e": body.email, "n": body.name, "a": admin},
            )
        ).scalar_one_or_none()
        if user_id is None:
            held_by_admin = (
                await conn.execute(
                    text("select is_client_admin from app.users where email = :e"),
                    {"e": body.email},
                )
            ).scalar()
            if held_by_admin and not admin:
                raise Forbidden("Only the platform owner can invite or re-invite a client admin")
            raise Conflict(
                "Email taken",
                errors=[FieldError(path="/email", message="Someone here already uses this email")],
            )
        if body.memberships is not None:
            await _set_memberships(conn, user_id, body.memberships)
        await conn.execute(text("delete from app.invites where user_id = :id"), {"id": user_id})
        expires_at = (
            await conn.execute(
                text(
                    "insert into app.invites (user_id, token_hash, sealed, expires_at)"
                    " values (:id, :h, :sealed, now() + interval '7 days') returning expires_at"
                ),
                {"id": user_id, "h": _sha(raw), "sealed": secrets.seal({"token": token})},
            )
        ).scalar_one()
        person = (await _users(conn, "u.id = :id", id=user_id))[0]
        answer = InviteOut(
            **person.model_dump(), invite_path=f"/invite/{token}", invite_expires_at=expires_at
        )
    return answer


# --- the owner invites a client's admin --------------------------------------------------------

owner_routes = owner_router()


@owner_routes.post("/clients/{client_id}/client-admin", status_code=201, responses=NO_STORE)
async def invite_client_admin(
    client_id: UUID, body: InviteIn, request: Request, response: Response
) -> InviteOut:
    """The client's admin (they manage people, never the flow, forms or UI), with their link."""
    async with request.app.state.platform.engine().connect() as conn:
        row = await get_client(conn, "id", client_id)
    if row is None:
        raise NotFound("No such client")
    ctx = await request.app.state.resolver.context(row)  # 503 until it's provisioned
    response.headers["Cache-Control"] = "no-store"
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
        raise Forbidden("Only the client admin can do this")
    return uid


routes = APIRouter(
    prefix="/app/admin", tags=["app"], dependencies=[Depends(client_context), Depends(client_admin)]
)


@routes.get("/users")
async def list_users(ctx: ClientCtx, status: Status | None = None) -> list[UserOut]:
    async with ctx.db.connect() as conn:
        return await _users(conn, "u.status = :status" if status else "true", status=status)


@routes.post("/users", status_code=201, responses=NO_STORE)
async def invite_user(body: UserIn, ctx: ClientCtx, response: Response) -> InviteOut:
    """A new invite, or a new link for someone who never accepted (Resend invite). A client admin,
    pending or not, is the owner's to invite: 403."""
    response.headers["Cache-Control"] = "no-store"
    return await invite(ctx, body)


@routes.patch("/users/{user_id}")
async def update_user(
    user_id: UUID, body: UserPatch, ctx: ClientCtx, me: Annotated[UUID, Depends(client_admin)]
) -> UserOut:
    """New memberships and/or status, then the Operaton mirror. If Operaton fails (502) the change
    is kept, and the reconciler finishes the mirror (`users.resync`), as does sending it again."""
    if body.status == "deactivated" and user_id == me:
        raise Conflict("You can't deactivate yourself")
    async with ctx.db.begin() as conn:
        if body.status == "deactivated":
            # every active client admin, locked in one order before the person: of two admins
            # deactivating each other at once, the second waits and then sees the first's change
            admins = await conn.execute(
                text(
                    "select id from app.users where is_client_admin and status = 'active'"
                    " order by id for update"
                )
            )
            if not set(admins.scalars()) - {user_id}:
                raise Conflict("The client must keep an active client admin")
        found = await conn.execute(
            text("update app.users set unsynced_since = now() where id = :id returning 1"),
            {"id": user_id},
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
