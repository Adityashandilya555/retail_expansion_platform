"""People (#22): seeding a client's users, creating owners, and mirroring users into Operaton.

`sync_user` keeps a client user and their department levels in that client's Operaton (D6): the
Operaton user has the same uuid and an unusable random password (nobody signs in to Operaton),
and belongs to exactly the `<dept>__L<n>` groups of its memberships. The compiler lists every
level that may act (`or_above`), so a user needs only their own level's group. S2-23 reuses it.
"""

import logging
import secrets as token_bytes
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import auth
from zm_api.context import ClientContext, PlatformRegistry
from zm_api.errors import NotFound
from zm_api.operaton import OperatonClient
from zm_schema import group_id
from zm_schema.semantics import LEVEL_GROUP

_log = logging.getLogger("zm_api.users")


def _ours(group: str) -> bool:
    """Exactly the `<dept>__L<n>` groups we manage; anything else (operaton-admin, audit__Legacy,
    legal__L0) is left alone."""
    return LEVEL_GROUP.fullmatch(group) is not None


async def mirror(
    op: OperatonClient, user_id: UUID, *, name: str, email: str, groups: set[str]
) -> None:
    """Make Operaton's copy of a user match: exists, and is in exactly `groups` of ours.

    Reads before it writes, because creating a user or group twice is a 400 and adding a
    membership twice is a 500 in Operaton 2.1.5.
    """
    uid = str(user_id)
    try:
        await op.user_profile(uid)
    except NotFound:
        first, _, last = name.partition(" ")
        await op.create_user(
            uid,
            first_name=first,
            last_name=last or first,
            email=email,
            password=token_bytes.token_urlsafe(24),  # 32 chars, unusable: never stored or shown
        )
    current = {g["id"] for g in await op.groups_of(uid) if _ours(g["id"])}
    for group in sorted(groups - current):
        try:
            await op.group(group)
        except NotFound:  # publishing (#23) normally creates the groups; seeding may come first
            dept, _, level = group.partition("__L")
            await op.create_group(group, f"{dept} L{level}")
        await op.add_member(group, uid)
    for group in sorted(current - groups):
        await op.remove_member(group, uid)


async def sync_user(ctx: ClientContext, user_id: UUID, conn: AsyncConnection | None = None) -> None:
    """Mirror one of `ctx`'s users into its Operaton; only an active user keeps groups. With
    `conn`, it mirrors what that open transaction holds, so a failure can roll the change back.
    Once mirrored it clears `unsynced_since`, unless a newer change has set it again since."""
    async with ctx.db.connect() if conn is None else nullcontext(conn) as db:
        user = (
            (
                await db.execute(
                    text(
                        "select id, email, name, status, unsynced_since from app.users"
                        " where id = :id"
                    ),
                    {"id": user_id},
                )
            )
            .mappings()
            .one()
        )
        rows = await db.execute(
            text("select department, level from app.memberships where user_id = :id"),
            {"id": user_id},
        )
        levels = rows.all()
    groups = {group_id(d, n) for d, n in levels} if user["status"] == "active" else set()
    await mirror(ctx.operaton, user["id"], name=user["name"], email=user["email"], groups=groups)
    if user["unsynced_since"] is not None:
        async with ctx.db.begin() if conn is None else nullcontext(conn) as db:
            await db.execute(
                text(
                    "update app.users set unsynced_since = null"
                    " where id = :id and unsynced_since = :seen"
                ),
                {"id": user_id, "seen": user["unsynced_since"]},
            )


async def resync(ctx: ClientContext, older_than: float = 120.0) -> Counter[str]:
    """Mirror again each user whose change didn't reach Operaton (a 502 after `PATCH
    /app/admin/users/{id}`) for more than `older_than` seconds; the reconciler runs it each pass.
    Counts `mirrored` and `mirror_failed` (tried again next pass)."""
    async with ctx.db.connect() as conn:
        ids = (
            (
                await conn.execute(
                    text(
                        "select id from app.users"
                        " where unsynced_since < now() - make_interval(secs => :age)"
                    ),
                    {"age": older_than},
                )
            )
            .scalars()
            .all()
        )
    counts: Counter[str] = Counter()
    for user_id in ids:
        try:
            await sync_user(ctx, user_id)
            counts["mirrored"] += 1
        except Exception:  # engine down: the rest fail too, and the next pass tries again
            _log.exception("mirroring user %s failed", user_id, extra={"client": ctx.client.slug})
            counts["mirror_failed"] += 1
    return counts


@dataclass(frozen=True)
class Seeded:
    email: str
    id: UUID
    password: str | None  # only for users created by this run; existing users keep theirs


async def seed_users(ctx: ClientContext, users: list[dict[str, Any]]) -> list[Seeded]:
    """Demo users until invites exist (S2-23): `[{email, name, department, level}]`. Creates
    missing users as active with a generated password, sets each one's membership, then mirrors
    them into Operaton. Running it again changes nobody's password."""
    out: list[Seeded] = []
    async with ctx.db.begin() as conn:
        for u in users:
            existing = (
                await conn.execute(
                    text("select id from app.users where email = :e"), {"e": u["email"]}
                )
            ).scalar_one_or_none()
            password = None
            if existing is None:
                password = token_bytes.token_urlsafe(12)
                existing = (
                    await conn.execute(
                        text(
                            "insert into app.users (email, name, password_hash, status)"
                            " values (:e, :n, :h, 'active') returning id"
                        ),
                        {"e": u["email"], "n": u["name"], "h": auth.hash_password(password)},
                    )
                ).scalar_one()
            await conn.execute(
                text(
                    "insert into app.memberships (user_id, department, level)"
                    " values (:id, :d, :l) on conflict (user_id, department)"
                    " do update set level = excluded.level"
                ),
                {"id": existing, "d": u["department"], "l": int(u["level"])},
            )
            out.append(Seeded(u["email"], existing, password))
    for s in out:
        await sync_user(ctx, s.id)
    return out


async def create_owner(platform: PlatformRegistry, email: str, name: str, password: str) -> UUID:
    """A platform owner for the owner console (`zm-api owners create`)."""
    async with platform.engine().begin() as conn:
        return (
            await conn.execute(
                text(
                    "insert into platform.owners (email, name, password_hash)"
                    " values (:e, :n, :h) returning id"
                ),
                {"e": email, "n": name, "h": auth.hash_password(password)},
            )
        ).scalar_one()
