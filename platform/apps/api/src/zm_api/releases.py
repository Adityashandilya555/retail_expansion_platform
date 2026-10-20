"""A client's draft and releases, in that client's own database (#23).

`app.drafts` holds the one `workflow.json` being edited; `app.releases` holds every published
version, each tied to the Operaton deployment that runs it. Releases are immutable, so a task
resolves its forms through its process definition's deployment, whichever version is active now.
"""

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import templates
from zm_api.context import ClientContext
from zm_api.errors import NotFound

_COLUMNS = (
    "version, deployment_id, workflow, forms, ui, routing_vars, process_keys, blocks_used,"
    " sha256, published_by, published_at, active"
)


@dataclass(frozen=True)
class ReleaseRow:
    version: int
    deployment_id: str
    workflow: dict[str, Any]
    forms: dict[str, Any]
    ui: dict[str, Any]
    routing_vars: dict[str, list[str]]
    process_keys: dict[str, str]
    blocks_used: dict[str, Any]
    sha256: str
    published_by: UUID | None
    published_at: datetime
    active: bool


def _row(mapping: Any) -> ReleaseRow:
    return ReleaseRow(**dict(mapping))


# --- draft -----------------------------------------------------------------------------------


async def seed_draft(ctx: ClientContext, template_key: str, by: UUID | None = None) -> None:
    """Copy a starter template into the client's draft (provisioning, S2-02b, reuses this).
    The key must name one of `packages/templates`; it is never treated as a path."""
    found = {t.key: t for t in templates.load_templates()}
    if template_key not in found:
        raise NotFound(f"No template {template_key!r}")
    async with ctx.db.begin() as conn:
        await conn.execute(
            text(
                "insert into app.drafts (id, workflow, template_key, updated_by)"
                " values (1, cast(:w as jsonb), :key, :by)"
                " on conflict (id) do update set workflow = excluded.workflow,"
                " template_key = excluded.template_key, updated_at = now(),"
                " updated_by = excluded.updated_by"
            ),
            {"w": json.dumps(found[template_key].workflow), "key": template_key, "by": by},
        )


async def get_draft(conn: AsyncConnection) -> dict[str, Any] | None:
    result = await conn.execute(text("select workflow from app.drafts where id = 1"))
    return result.scalar_one_or_none()


# --- releases --------------------------------------------------------------------------------


async def active_release(conn: AsyncConnection) -> ReleaseRow | None:
    result = await conn.execute(text(f"select {_COLUMNS} from app.releases where active"))
    mapping = result.mappings().one_or_none()
    return None if mapping is None else _row(mapping)


async def release_by_deployment(conn: AsyncConnection, deployment_id: str) -> ReleaseRow | None:
    result = await conn.execute(
        text(f"select {_COLUMNS} from app.releases where deployment_id = :d"), {"d": deployment_id}
    )
    mapping = result.mappings().one_or_none()
    return None if mapping is None else _row(mapping)


async def insert_release(conn: AsyncConnection, values: dict[str, Any]) -> ReleaseRow:
    """Next version, made the only active one. Run inside the publish lock and transaction."""
    version = (
        await conn.execute(text("select coalesce(max(version), 0) + 1 from app.releases"))
    ).scalar_one()
    await conn.execute(text("update app.releases set active = false where active"))
    params = {k: json.dumps(v) if isinstance(v, dict) else v for k, v in values.items()}
    result = await conn.execute(
        text(
            "insert into app.releases (version, deployment_id, workflow, forms, ui, routing_vars,"
            " process_keys, blocks_used, sha256, published_by, active) values (:version,"
            " :deployment_id, cast(:workflow as jsonb), cast(:forms as jsonb), cast(:ui as jsonb),"
            " cast(:routing_vars as jsonb), cast(:process_keys as jsonb),"
            f" cast(:blocks_used as jsonb), :sha256, :published_by, true) returning {_COLUMNS}"
        ),
        {"version": version, **params},
    )
    return _row(result.mappings().one())


async def activate(conn: AsyncConnection, version: int) -> ReleaseRow:
    """Make an existing release the active one again. Run inside the publish lock."""
    await conn.execute(text("update app.releases set active = false where active"))
    result = await conn.execute(
        text(f"update app.releases set active = true where version = :v returning {_COLUMNS}"),
        {"v": version},
    )
    return _row(result.mappings().one())


# --- task -> release (S1-10) -----------------------------------------------------------------

_CACHE_SIZE = 4096
# process definition → deployment never changes; the release row is read fresh (its `active`
# flag does change with later publishes), so callers never share a cached, mutable row.
_deployment_of: OrderedDict[tuple[str, str], str] = OrderedDict()


def cache_clear() -> None:
    """Forget every cached definition → deployment (tests, a re-provisioned client)."""
    _deployment_of.clear()


async def release_for_task(ctx: ClientContext, process_definition_id: str) -> ReleaseRow:
    """The release a task belongs to: its process definition's deployment → `app.releases`.
    A case started on v1 keeps resolving v1's forms after v2 is published."""
    key = (ctx.client.slug, process_definition_id)
    deployment_id = _deployment_of.get(key)
    if deployment_id is None:
        definition = await ctx.operaton.process_definition_by_id(process_definition_id)
        deployment_id = definition["deploymentId"]
        _deployment_of[key] = deployment_id
        while len(_deployment_of) > _CACHE_SIZE:
            _deployment_of.popitem(last=False)
    else:
        _deployment_of.move_to_end(key)
    async with ctx.db.connect() as conn:
        release = await release_by_deployment(conn, deployment_id)
    if release is None:
        raise NotFound("No release for this process")
    return release
