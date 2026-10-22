"""Publish (#23): the client's draft → a release on that client's own Operaton.

1. Compile the draft (`zm_compiler.compile`, #19).
2. Make sure its level groups (`<dept>__L<n>`) exist in the client's Operaton.
3. Deploy every BPMN resource plus `zm-release.json` as one deployment named `<slug>-release`,
   with duplicate filtering and `deploy-changed-only=false`: an unchanged draft gives back the
   existing deployment (no new version); any change, a form label included (it changes
   `zm-release.json`), redeploys every resource, which the stage call activities bound with
   `calledElementBinding="deployment"` need (DeployCmd.java:207-263, CallableElementUtil:94-96).
4. Record the release as the next active version, unless that deployment is already recorded.
   Steps 1-4 run in one client-database transaction under a per-client advisory lock, so two
   publishes at once deploy and number their versions in the same order.
5. Index it in `platform.releases` (idempotent, so a retried publish repairs a missing row).

Operaton failing in steps 2-3 raises before anything is written (502); a database failure after
the deploy is a 503, and publishing again completes it (the deploy is then a duplicate).
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from zm_api import releases
from zm_api.context import ClientContext, OwnerCtx, owner_router
from zm_api.errors import Conflict, NotFound, ServiceUnavailable, UnprocessableContent
from zm_api.operaton import OperatonError
from zm_api.registry import get_client
from zm_compiler import CompileError, Release, compile
from zm_schema import SchemaVersionError, migrate

RELEASE_RESOURCE = "zm-release.json"
SOURCE = "zm-publish"


@dataclass(frozen=True)
class PublishResult:
    version: int
    deployment_id: str
    created: bool
    """False when the draft was unchanged and the existing release came back."""
    sha256: str


def release_json(release: Release) -> bytes:
    """What the deployment carries besides BPMN: deterministic, so an unchanged release gives the
    same bytes and Operaton's duplicate filter keeps the existing deployment."""
    body = {
        "digest": release.digest(),
        "forms": {k: {"schema": f.schema, "layout": f.layout} for k, f in release.forms.items()},
        "ui": release.ui,
        "routing_vars": release.routing_vars,
        "process_keys": release.process_keys,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def blocks_used(ui: dict[str, Any]) -> dict[str, None]:
    """Block name → manifest version; versions arrive with block manifests (#30, #92)."""
    names = {
        block["block"]
        for page in ui.get("pages", [])
        for region in page.get("regions", {}).values()
        for block in region
    }
    return dict.fromkeys(sorted(names))


def _compile(slug: str, draft: dict[str, Any]) -> tuple[Any, Release]:
    try:
        wf = migrate(draft)
        return wf, compile(wf, slug)
    except (SchemaVersionError, ValueError, CompileError) as e:  # ValidationError is a ValueError
        raise UnprocessableContent(f"The draft can't be published: {e}") from None


async def _ensure_groups(ctx: ClientContext, release: Release) -> None:
    """Reads before it writes: creating a group twice is refused by Operaton (400 "The group
    already exists"; a true insert race can surface as another error). If our create is refused,
    the group must exist now; a renamed level updates the group's name."""
    for group in release.groups:
        try:
            current = await ctx.operaton.group(group.id)
        except NotFound:
            try:
                await ctx.operaton.create_group(group.id, group.name)
            except OperatonError:
                await ctx.operaton.group(group.id)  # created meanwhile, else the real error
            continue
        if current.get("name") != group.name:
            await ctx.operaton.update_group(group.id, group.name)


def _index_conflict_sql() -> str:
    # a reset vault restarts at v1: the index follows the vault rather than keeping a stale row
    return (
        "insert into platform.releases (client_id, version, deployment_id, published_by,"
        " workflow_sha256) values (:c, :v, :d, :by, :sha)"
        " on conflict (client_id, version) do update set deployment_id = excluded.deployment_id,"
        " published_by = excluded.published_by, workflow_sha256 = excluded.workflow_sha256,"
        " published_at = now() where platform.releases.deployment_id <> excluded.deployment_id"
    )


async def publish(
    ctx: ClientContext, platform: AsyncEngine, published_by: UUID | None
) -> PublishResult:
    slug = ctx.client.slug
    try:
        # One transaction holds the per-client lock from reading the draft to recording the
        # release, so concurrent publishes deploy and number their versions in the same order.
        async with ctx.db.begin() as conn:
            await conn.execute(
                text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"zm_publish:{slug}"}
            )
            draft = await releases.get_draft(conn)
            if draft is None:
                raise Conflict("This client has no draft to publish yet")
            wf, release = _compile(slug, draft)

            await _ensure_groups(ctx, release)
            files = {**release.bpmn, RELEASE_RESOURCE: release_json(release)}
            deployment = await ctx.operaton.deploy(f"{slug}-release", files, source=SOURCE)
            deployment_id = deployment["id"]

            row = await releases.release_by_deployment(conn, deployment_id)
            created = row is None
            if row is None:
                row = await releases.insert_release(
                    conn,
                    {
                        "deployment_id": deployment_id,
                        # the document as published, after any schema migration (D14)
                        "workflow": wf.model_dump(mode="json", by_alias=True, exclude_unset=True),
                        "forms": {
                            k: {"schema": f.schema, "layout": f.layout}
                            for k, f in release.forms.items()
                        },
                        "ui": release.ui,
                        "routing_vars": release.routing_vars,
                        "process_keys": release.process_keys,
                        "blocks_used": blocks_used(release.ui),
                        "sha256": release.digest(),
                        "published_by": published_by,
                    },
                )
            elif not row.active:  # Operaton's latest is an older release: make it current again
                row = await releases.activate(conn, row.version)

        async with platform.begin() as conn:
            await conn.execute(
                text(_index_conflict_sql()),
                {
                    "c": ctx.client.id,
                    "v": row.version,
                    "d": row.deployment_id,
                    "by": row.published_by,
                    "sha": row.sha256,
                },
            )
    except (SQLAlchemyError, OSError) as e:
        # Operaton may already hold the deployment; publishing again is safe and completes it.
        raise ServiceUnavailable("Couldn't record the release; publish again to retry") from e
    return PublishResult(row.version, row.deployment_id, created, row.sha256)


# --- API: POST /owner/clients/{client_id}/publish ---------------------------------------------


class PublishOut(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    version: int
    deployment_id: str
    created: bool
    """False when nothing changed since the last publish (no new version)."""
    sha256: str


routes = owner_router()


@routes.post("/clients/{client_id}/publish")
async def publish_client(client_id: UUID, owner: OwnerCtx, request: Request) -> PublishOut:
    """Publish the client's draft as its next release (a person clicks Publish, never the agent)."""
    platform = request.app.state.platform.engine()
    async with platform.connect() as conn:
        row = await get_client(conn, "id", client_id)
    if row is None:
        raise NotFound("No such client")
    ctx = await request.app.state.resolver.context(row)
    result = await publish(ctx, platform, owner.owner_id)
    return PublishOut(**result.__dict__)
