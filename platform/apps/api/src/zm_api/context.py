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
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Request
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


def _asyncpg(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


class Resolver:
    """Slug → ClientContext. Registry rows are cached for `ttl` seconds; each client's engine and
    Operaton client are built lazily, rebuilt when its row changes, and closed when evicted."""

    def __init__(
        self,
        lookup: Lookup,
        *,
        ttl: float = 60.0,
        max_clients: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup, self._ttl, self._max, self._clock = lookup, ttl, max_clients, clock
        self._rows: dict[str, tuple[float, Client | None]] = {}
        self._live: OrderedDict[str, ClientContext] = OrderedDict()
        # ponytail: one lock for all clients; per-slug locks if it ever shows up in latency
        self._lock = asyncio.Lock()

    async def resolve(self, slug: str) -> ClientContext | None:
        async with self._lock:
            cached = self._rows.get(slug)
            if cached is None or self._clock() - cached[0] >= self._ttl:
                cached = (self._clock(), await self._lookup(slug))
                self._rows[slug] = cached
            row = cached[1]
            if row is None:
                return None
            ctx = self._live.get(slug)
            if ctx is not None and ctx.client == row:
                self._live.move_to_end(slug)
                return ctx
            if ctx is not None:
                await self._close(self._live.pop(slug))
            if row.status != "ready" or not row.operaton_url or not row.secrets:
                raise ServiceUnavailable("This workspace isn't available yet")
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

    # ponytail: closing on eviction or a row change can cut off a request still using the old
    # resources; fine while max_clients is far above the client count, refcount them if not.
    @staticmethod
    async def _close(ctx: ClientContext) -> None:
        await ctx.operaton.aclose()
        await ctx.db.dispose()

    async def aclose(self) -> None:
        async with self._lock:
            while self._live:
                await self._close(self._live.popitem()[1])


def platform_lookup() -> Lookup:
    """The registry in the control-plane database (`PLATFORM_DATABASE_URL`)."""
    engine: AsyncEngine | None = None

    async def lookup(slug: str) -> Client | None:
        nonlocal engine
        engine = engine or create_async_engine(settings.platform_database_url(), pool_size=5)
        async with engine.connect() as conn:
            return await get_client(conn, "slug", slug)

    return lookup


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
