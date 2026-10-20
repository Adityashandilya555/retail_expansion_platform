"""Request context (#21): which client a request is for, and that client's resources.

The client comes only from the JWT claim `client` (its registry slug), never from the path, query
or body, so a token can only reach its own client's database and Operaton: another client's ids
simply don't exist there (404). Tokens are issued by S1-06 (#22): HS256 with `JWT_SECRET`,
claims `{sub, kind: "client" | "owner", client, exp}`.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Request
from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zm_api import secrets, settings
from zm_api.errors import ServiceUnavailable, Unauthorized
from zm_api.logs import client as client_var
from zm_api.operaton import OperatonClient
from zm_api.registry import Client, get_client

SERVICE_ACCOUNT = "zm-service"  # ZM_ADMIN_USER_ID on every client's Operaton (#25)

Lookup = Callable[[str], Awaitable[Client | None]]


@dataclass(frozen=True)
class ClientContext:
    client: Client
    db: AsyncEngine  # the client's database as role zm_api
    operaton: OperatonClient  # the client's own engine as its service account
    bucket: str | None


@dataclass(frozen=True)
class OwnerContext:
    """A platform owner (owner console); deliberately has no client resources."""

    owner_id: UUID


def _asyncpg(dsn: str) -> URL:
    """Any Postgres DSN (`postgres://`, `postgresql://`, any driver) on asyncpg."""
    return make_url(dsn).set(drivername="postgresql+asyncpg")


def _resources(row: Client) -> tuple[str | None, bytes | None, str | None]:
    """What a ClientContext is built from; other row changes (name, updated_at) don't rebuild it."""
    return row.operaton_url, row.secrets, row.bucket


class Resolver:
    """Slug → ClientContext. Registry rows are cached for `ttl` seconds; each client's engine and
    Operaton client are built lazily, rebuilt when the row's resources change, and closed when
    evicted. The registry query runs outside the lock with a timeout, so a slow control-plane
    database answers 503 instead of holding up every other client."""

    def __init__(
        self,
        lookup: Lookup,
        *,
        ttl: float = 60.0,
        max_clients: int = 32,
        lookup_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup, self._ttl, self._max, self._clock = lookup, ttl, max_clients, clock
        self._lookup_timeout = lookup_timeout
        self._rows: dict[str, tuple[float, Client | None]] = {}
        self._live: OrderedDict[str, ClientContext] = OrderedDict()
        # shortcut: one lock for all clients' resources; per-slug locks if it shows up in latency
        self._lock = asyncio.Lock()

    async def _row(self, slug: str) -> Client | None:
        cached = self._rows.get(slug)
        if cached is not None and self._clock() - cached[0] < self._ttl:
            return cached[1]
        try:
            async with asyncio.timeout(self._lookup_timeout):
                row = await self._lookup(slug)
        except TimeoutError:
            raise ServiceUnavailable("The workspace registry isn't answering") from None
        self._rows[slug] = (self._clock(), row)
        return row

    async def resolve(self, slug: str) -> ClientContext | None:
        row = await self._row(slug)
        if row is None:
            return None
        if row.status != "ready" or not row.operaton_url or not row.secrets:
            raise ServiceUnavailable("This workspace isn't available yet")
        async with self._lock:
            ctx = self._live.get(slug)
            if ctx is not None and _resources(ctx.client) == _resources(row):
                if ctx.client != row:
                    ctx = self._live[slug] = replace(ctx, client=row)
                self._live.move_to_end(slug)
                return ctx
            if ctx is not None:
                await self._close(self._live.pop(slug))
            sealed = secrets.unseal(row.secrets)
            ctx = ClientContext(
                client=row,
                db=create_async_engine(_asyncpg(sealed["zm_api_dsn"]), pool_size=5, max_overflow=0),
                operaton=OperatonClient(
                    row.operaton_url,
                    auth=(SERVICE_ACCOUNT, sealed["service_account_password"]),
                    client=row.slug,
                ),
                bucket=row.bucket,
            )
            self._live[slug] = ctx
            while len(self._live) > self._max:
                await self._close(self._live.popitem(last=False)[1])
            return ctx

    # shortcut: closing on eviction or a resource change can cut off a request still using the old
    # resources; fine while max_clients is far above the client count, refcount them if not.
    @staticmethod
    async def _close(ctx: ClientContext) -> None:
        await ctx.operaton.aclose()
        await ctx.db.dispose()

    async def aclose(self) -> None:
        async with self._lock:
            while self._live:
                await self._close(self._live.popitem()[1])
        close = getattr(self._lookup, "aclose", None)
        if close is not None:
            await close()


class PlatformRegistry:
    """Lookup in the control-plane database (`PLATFORM_DATABASE_URL`); its engine is created on
    first use and disposed by `aclose` (called from `Resolver.aclose` at shutdown)."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None

    async def __call__(self, slug: str) -> Client | None:
        if self._engine is None:
            self._engine = create_async_engine(settings.platform_database_url(), pool_size=5)
        async with self._engine.connect() as conn:
            return await get_client(conn, "slug", slug)

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None


def _claims(request: Request, kind: str) -> dict:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise Unauthorized()
    try:
        claims = jwt.decode(
            value, settings.jwt_secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError:
        raise Unauthorized() from None
    if claims.get("kind") != kind:
        raise Unauthorized()
    return claims


async def client_context(request: Request) -> ClientContext:
    slug = _claims(request, "client").get("client")
    if not isinstance(slug, str):
        raise Unauthorized()
    resolver: Resolver = request.app.state.resolver
    ctx = await resolver.resolve(slug)
    if ctx is None:  # the client was removed after the token was issued
        raise Unauthorized()
    request.state.client = slug  # for the request log line
    client_var.set(slug)  # for anything else this request logs
    return ctx


def owner_context(request: Request) -> OwnerContext:
    try:
        return OwnerContext(owner_id=UUID(_claims(request, "owner")["sub"]))
    except ValueError:
        raise Unauthorized() from None


ClientCtx = Annotated[ClientContext, Depends(client_context)]
OwnerCtx = Annotated[OwnerContext, Depends(owner_context)]


# Route namespaces (#21): `/auth/*` is sign-in (S1-06, plain APIRouter); `/app/*` and `/owner/*`
# routers come from these, so every route on them needs the right token even without a Ctx param.
def client_router() -> APIRouter:
    return APIRouter(prefix="/app", tags=["app"], dependencies=[Depends(client_context)])


def owner_router() -> APIRouter:
    return APIRouter(prefix="/owner", tags=["owner"], dependencies=[Depends(owner_context)])
