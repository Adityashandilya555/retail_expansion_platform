"""Sign-in (#22): client users (workspace code + email + password) and platform owners.

- Access token: HS256 JWT on `JWT_SECRET`, `{sub, kind, client?, exp}`, `JWT_ACCESS_TTL_MIN`.
  The apps keep it in memory only.
- Refresh token: random, stored as a sha256 hash, sent as an httpOnly, Secure, SameSite=Strict
  cookie scoped to the auth paths, `JWT_REFRESH_TTL_DAYS`. Each refresh rotates it inside its
  family; presenting a rotated or revoked token again revokes the whole family.
- Every failed sign-in (unknown workspace, unknown email, wrong password, not active) is the same
  401, after the same password-hashing work.
"""

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import settings
from zm_api.context import ClientCtx, OwnerCtx, Resolver, UserId, client_router, owner_router
from zm_api.errors import NotFound, TooManyRequests, Unauthorized
from zm_api.registry import Client

_hasher = PasswordHasher()  # argon2id
_SPARE = _hasher.hash(secrets.token_urlsafe(16))  # verified against when there's no user
WRONG_CLIENT = "Wrong workspace code, email or password"
WRONG_OWNER = "Wrong email or password"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify(password_hash: str | None, password: str) -> bool:
    """Always does the hashing work, so an unknown email takes as long as a wrong password."""
    try:
        return _hasher.verify(password_hash or _SPARE, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


class RateLimit:
    """At most `limit` hits per `window` seconds per key (client IP).
    shortcut: in memory, per process; move to the database or Redis with more than one replica."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


# --- API shapes (they become the generated TS client's types) ---------------------------------


class WorkspaceIn(BaseModel):
    workspace_code: str = Field(min_length=1, max_length=64)


class WorkspaceOut(BaseModel):
    client_name: str
    theme: str
    """Theme preset key (D19), e.g. `quartz`."""
    accent: str | None
    """The client's default accent; null = the preset's own."""


class LoginIn(BaseModel):
    workspace_code: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class OwnerLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    """Seconds until the access token expires; refresh before then."""


class Membership(BaseModel):
    department: str
    level: int
    scope: Literal["own", "department", "all"]


class ClientInfo(BaseModel):
    slug: str
    name: str


class Me(BaseModel):
    id: UUID
    email: str
    name: str
    is_client_admin: bool
    client: ClientInfo
    memberships: list[Membership]


class OwnerMe(BaseModel):
    id: UUID
    email: str
    name: str


# --- tokens -------------------------------------------------------------------------------------


def access_token(
    sub: UUID, kind: Literal["client", "owner"], client: str | None = None
) -> TokenOut:
    ttl = settings.jwt_access_ttl_min() * 60
    claims: dict[str, object] = {"sub": str(sub), "kind": kind, "exp": int(time.time()) + ttl}
    if client is not None:
        claims["client"] = client
    return TokenOut(access_token=jwt.encode(claims, settings.jwt_secret(), "HS256"), expires_in=ttl)


@dataclass(frozen=True)
class _Tokens:
    """Where one kind of refresh token lives; table names are constants, never input."""

    table: str
    subject: str  # column pointing at the person
    people: str  # table of the people, with `status`
    cookie: str
    path: str


CLIENT = _Tokens("app.refresh_tokens", "user_id", "app.users", "zm_refresh", "/auth")
OWNER = _Tokens(
    "platform.owner_refresh_tokens",
    "owner_id",
    "platform.owners",
    "zm_owner_refresh",
    "/owner/auth",
)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue(
    conn: AsyncConnection, t: _Tokens, subject: UUID, family: UUID | None = None
) -> str:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days())
    await conn.execute(
        text(
            f"insert into {t.table} ({t.subject}, token_hash, family, expires_at)"
            " values (:s, :h, coalesce(:f, gen_random_uuid()), :e)"
        ),
        {"s": subject, "h": _sha(raw), "f": family, "e": expires},
    )
    return raw


async def _revoke_family(conn: AsyncConnection, t: _Tokens, family: UUID) -> None:
    await conn.execute(
        text(f"update {t.table} set revoked_at = now() where family = :f and revoked_at is null"),
        {"f": family},
    )


async def _rotate(conn: AsyncConnection, t: _Tokens, raw: str) -> tuple[UUID, str] | None:
    """(person, new raw token), or None. Reusing a spent token, or a person no longer active,
    revokes the whole family."""
    row = (
        (
            await conn.execute(
                text(
                    f"select r.id, r.{t.subject} as subject, r.family, r.revoked_at,"
                    f" r.expires_at > now() as live, p.status = 'active' as active"
                    f" from {t.table} r join {t.people} p on p.id = r.{t.subject}"
                    " where r.token_hash = :h for update of r"
                ),
                {"h": _sha(raw)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["revoked_at"] is not None or not row["active"]:
        await _revoke_family(conn, t, row["family"])
        return None
    if not row["live"]:
        return None
    await conn.execute(
        text(f"update {t.table} set revoked_at = now() where id = :id"), {"id": row["id"]}
    )
    return row["subject"], await _issue(conn, t, row["subject"], row["family"])


async def _revoke(conn: AsyncConnection, t: _Tokens, raw: str) -> None:
    family = (
        await conn.execute(
            text(f"select family from {t.table} where token_hash = :h"), {"h": _sha(raw)}
        )
    ).scalar_one_or_none()
    if family is not None:
        await _revoke_family(conn, t, family)


def _set_cookie(response: Response, t: _Tokens, value: str) -> None:
    response.set_cookie(
        t.cookie,
        value,
        max_age=settings.jwt_refresh_ttl_days() * 86400,
        path=t.path,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _clear_cookie(response: Response, t: _Tokens) -> None:
    response.delete_cookie(t.cookie, path=t.path, secure=True, httponly=True, samesite="strict")


def _look(row: Client) -> tuple[str, str | None]:
    """The client's theme preset and default accent for the sign-in screen (D19).
    shortcut: the defaults until #23 stores releases; then read `ui.theme`/`ui.accent` of the
    client's active release in app.releases."""
    return "quartz", None


# --- client users: /auth/* ----------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_cookie(request: Request) -> tuple[str, str]:
    slug, _, raw = (request.cookies.get(CLIENT.cookie) or "").partition(".")
    return slug, raw


@router.post("/workspace")
async def workspace(body: WorkspaceIn, request: Request) -> WorkspaceOut:
    """The client behind a workspace code, so the sign-in screen can show its name and look."""
    if not request.app.state.workspace_limit.allow(request.client.host if request.client else "-"):
        raise TooManyRequests(retry_after=60)
    row = await request.app.state.resolver.find_code(body.workspace_code)
    if row is None or row.status != "ready":
        raise NotFound("Workspace not found")
    theme, accent = _look(row)
    return WorkspaceOut(client_name=row.name, theme=theme, accent=accent)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> TokenOut:
    resolver: Resolver = request.app.state.resolver
    row = await resolver.find_code(body.workspace_code)
    if row is None or row.status != "ready":
        _verify(None, body.password)
        raise Unauthorized(WRONG_CLIENT)
    ctx = await resolver.context(row)
    async with ctx.db.begin() as conn:
        user = (
            (
                await conn.execute(
                    text("select id, password_hash, status from app.users where email = :e"),
                    {"e": body.email},
                )
            )
            .mappings()
            .one_or_none()
        )
        if not _verify(user and user["password_hash"], body.password) or user["status"] != "active":
            raise Unauthorized(WRONG_CLIENT)
        raw = await _issue(conn, CLIENT, user["id"])
    _set_cookie(response, CLIENT, f"{row.slug}.{raw}")
    return access_token(user["id"], "client", row.slug)


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> TokenOut:
    slug, raw = _client_cookie(request)
    ctx = await request.app.state.resolver.resolve(slug) if slug and raw else None
    rotated = None
    if ctx is not None:
        async with ctx.db.begin() as conn:
            rotated = await _rotate(conn, CLIENT, raw)
    if rotated is None:
        raise Unauthorized()
    _set_cookie(response, CLIENT, f"{slug}.{rotated[1]}")
    return access_token(rotated[0], "client", slug)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    slug, raw = _client_cookie(request)
    ctx = await request.app.state.resolver.resolve(slug) if slug and raw else None
    if ctx is not None:
        async with ctx.db.begin() as conn:
            await _revoke(conn, CLIENT, raw)
    _clear_cookie(response, CLIENT)


app_routes = client_router()


@app_routes.get("/me")
async def me(ctx: ClientCtx, uid: UserId) -> Me:
    """The signed-in user and memberships, read on every call so level changes apply at once."""
    async with ctx.db.connect() as conn:
        user = (
            (
                await conn.execute(
                    text(
                        "select id, email, name, status, is_client_admin"
                        " from app.users where id = :id"
                    ),
                    {"id": uid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if user is None or user["status"] != "active":
            raise Unauthorized()
        levels = await conn.execute(
            text(
                "select department, level, scope from app.memberships where user_id = :id"
                " order by department"
            ),
            {"id": uid},
        )
        memberships = [Membership(**m) for m in levels.mappings()]
    return Me(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        is_client_admin=user["is_client_admin"],
        client=ClientInfo(slug=ctx.client.slug, name=ctx.client.name),
        memberships=memberships,
    )


# --- platform owners: /owner/auth/*, /owner/me ----------------------------------------------------

owner_auth = APIRouter(prefix="/owner/auth", tags=["owner"])


@owner_auth.post("/login")
async def owner_login(body: OwnerLoginIn, request: Request, response: Response) -> TokenOut:
    async with request.app.state.platform.engine().begin() as conn:
        owner = (
            (
                await conn.execute(
                    text("select id, password_hash, status from platform.owners where email = :e"),
                    {"e": body.email},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            not _verify(owner and owner["password_hash"], body.password)
            or owner["status"] != "active"
        ):
            raise Unauthorized(WRONG_OWNER)
        raw = await _issue(conn, OWNER, owner["id"])
    _set_cookie(response, OWNER, raw)
    return access_token(owner["id"], "owner")


@owner_auth.post("/refresh")
async def owner_refresh(request: Request, response: Response) -> TokenOut:
    raw = request.cookies.get(OWNER.cookie)
    rotated = None
    if raw:
        async with request.app.state.platform.engine().begin() as conn:
            rotated = await _rotate(conn, OWNER, raw)
    if rotated is None:
        raise Unauthorized()
    _set_cookie(response, OWNER, rotated[1])
    return access_token(rotated[0], "owner")


@owner_auth.post("/logout", status_code=204)
async def owner_logout(request: Request, response: Response) -> None:
    raw = request.cookies.get(OWNER.cookie)
    if raw:
        async with request.app.state.platform.engine().begin() as conn:
            await _revoke(conn, OWNER, raw)
    _clear_cookie(response, OWNER)


owner_routes = owner_router()


@owner_routes.get("/me")
async def owner_me(owner: OwnerCtx, request: Request) -> OwnerMe:
    async with request.app.state.platform.engine().connect() as conn:
        row = (
            (
                await conn.execute(
                    text("select id, email, name, status from platform.owners where id = :id"),
                    {"id": owner.owner_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None or row["status"] != "active":
        raise Unauthorized()
    return OwnerMe(id=row["id"], email=row["email"], name=row["name"])
