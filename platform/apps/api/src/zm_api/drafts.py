"""A client's draft and its change log (#33, S2-03): every edit is a logged JSON patch with undo.

`apply_change` is how a draft is edited (the agent's tools, #36, and the theme picker): it applies
an RFC 6902 patch to a copy, validates the result with `zm_schema.validate` (S1-07, including the
`schema_version` check of D14), and writes the draft and its `app.draft_changes` row in one
transaction holding the draft's row lock, so changes apply one at a time. A change that doesn't
apply, or brings in problems the draft didn't have, writes nothing and comes back as a 422 listing
them (problems it already had don't block it). A caller that passes the sha256 of the draft it
read gets a 409 when the draft has changed since. A change is also checked against every
published release's fields (S2-25, `zm_api.versioning`): a type change or a deleted field is a
problem like any other, and `/validate` and publish report it too.

`undo` applies the inverse of the newest change not yet undone, as a new row pointing at it
(`undoes`), so the log only grows: 0005's trigger refuses updates and deletes, and zm_api has no
grant for them. Each row keeps the sha256 of the draft it left. Undo refuses (409) when the draft
no longer matches the change it would undo, i.e. something wrote it outside the log since
(`zm-api seed-draft`, SQL), because the inverse was made for another document. It restores that
exact earlier state without validating it again, so a state today's rules reject stays reachable;
`/validate` and publish report it. The one exception is a release published since: an undo that
would delete or retype one of its fields is a 409 too. `diff_vs_live` compares the draft with the
active release.
Owners reach all of it under `/owner/clients/{id}/draft`.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import jsonpatch
import jsonpointer
from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import releases, versioning
from zm_api.context import ClientContext, OwnerCtx, owner_router
from zm_api.errors import Conflict, FieldError, NotFound, UnprocessableContent
from zm_api.registry import get_client
from zm_schema import Error, SchemaVersionError, WorkflowV1, validate
from zm_schema.semantics import stage_tasks
from zm_schema.versioning import upgrade

Patch = list[dict[str, Any]]
NO_DRAFT = "This client has no draft yet"


class ChangeOut(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    id: int
    at: datetime
    author_owner_id: UUID
    conversation_id: UUID | None
    change_request_id: UUID | None
    tool: str
    """What made the change: an agent tool's name, `set_theme` or `undo`."""
    patch: Patch
    """The RFC 6902 operations applied to the draft."""
    summary: str
    undoes: int | None
    """On an undo's row, the change it undid."""
    after_sha256: str
    """The sha256 of the draft it left, over jsonb's text form."""


class DraftOut(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    workflow: dict[str, Any]
    template_key: str | None
    base_version: int | None
    """The release this draft was published as; null until publish records it."""
    updated_at: datetime
    updated_by: UUID | None


class KeyChanges(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[str]


class ValueChange(BaseModel):
    live: str | None
    draft: str | None


class DraftDiff(BaseModel):
    """What publishing the draft would change; every list empty and both looks null = nothing."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    stages: KeyChanges
    """By `<flow>.<stage>`: its name, department, `after` and the order of its tasks."""
    tasks: KeyChanges
    """By task id, across every flow; moving a task to another stage changes it."""
    fields: KeyChanges
    """By field key."""
    blocks: list[str]
    """Keys of pages whose blocks changed, added and removed pages included."""
    theme: ValueChange | None
    """Null when unchanged."""
    accent: ValueChange | None
    """Null when unchanged."""


# --- patches and diffs (no database) ---------------------------------------------------------


def _canonical(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True)


def _errors(errors: list[Error]) -> list[FieldError]:
    """Paths are `workflow.json` paths, e.g. `flows[0].stages[1].after[0]`."""
    return [FieldError(path=e.path, message=e.message) for e in errors]


def inverse(before: dict[str, Any], after: dict[str, Any]) -> Patch:
    """The patch that turns `after` back into `before`. jsonpatch's diff can get list moves
    wrong, so one that doesn't give back `before` exactly becomes a whole-document replace."""
    patch = jsonpatch.make_patch(after, before).patch
    with suppress(jsonpatch.JsonPatchException, jsonpointer.JsonPointerException):
        if _canonical(jsonpatch.apply_patch(after, patch)) == _canonical(before):
            return patch
    return [{"op": "replace", "path": "", "value": before}]


def _invalid(errors: list[Error]) -> UnprocessableContent:
    return UnprocessableContent("The change would make the draft invalid", errors=_errors(errors))


def _apply(doc: dict[str, Any], patch: Patch) -> dict[str, Any]:
    """`doc` with `patch` applied; `doc` itself is left alone. A patch that doesn't apply is a
    422, malformed ones too (jsonpatch raises TypeError for e.g. `"from": 3`)."""
    try:
        return jsonpatch.apply_patch(doc, patch)
    except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException, TypeError) as e:
        raise _invalid([Error("$", f"the patch doesn't apply: {e}")]) from None


def problems(doc: dict[str, Any], published: Sequence[dict[str, Any]]) -> list[Error]:
    """The draft's schema problems, then what it does to the published fields (S2-25)."""
    return validate(doc) + versioning.errors(published, doc)


def patched(
    doc: dict[str, Any], patch: Patch, published: Sequence[dict[str, Any]] = ()
) -> tuple[dict[str, Any], Patch]:
    """`doc` with `patch` applied, and its inverse. A patch that doesn't apply, or raises the
    number of any kind of problem `doc` had (against `published`, every release's fields), is a
    422 listing the extra ones: a draft already invalid (an undo restores without validating)
    still takes a change that adds none. (A change may swap one problem for another of the same
    kind; validate and publish still report it.)"""
    after = _apply(doc, patch)
    errors = problems(after, published)
    if errors:
        # problems are matched without list positions, counted: a change that moves stages or
        # tasks moves the old problems' paths too (stages[1] → stages[2]), and still adds none
        old = Counter(_problem(e) for e in problems(doc, published))
        new = []
        for e in errors:
            if old[_problem(e)] > 0:
                old[_problem(e)] -= 1
            else:
                new.append(e)
        if new:
            raise _invalid(new)
    return after, inverse(doc, after)


_POSITION = re.compile(r"\[\d+\]")


def _problem(error: Any) -> tuple[str, str]:
    """An error as a problem of the draft, independent of where lists put it right now: in its
    path and in its message (e.g. "task id 'x' already used at flows[0].stages[0].tasks[1].id")."""
    return _POSITION.sub("[]", error.path), _POSITION.sub("[]", error.message)


def _outline(doc: dict[str, Any] | None) -> dict[str, Any]:
    """What the diff compares, with defaults filled in, so an unset default equals a set one."""
    if doc is None:  # nothing published yet
        return {
            "stages": {},
            "tasks": {},
            "fields": {},
            "blocks": {},
            "theme": None,
            "accent": None,
        }
    wf = WorkflowV1.model_validate(upgrade(dict(doc)), context={"structural_only": True})
    stages: dict[str, Any] = {}
    tasks: dict[str, Any] = {}
    for flow in wf.flows:
        for stage in flow.stages:
            where = f"{flow.key}.{stage.key}"
            placed = list(stage_tasks(stage, ""))
            # the stage without its tasks' bodies: their paths give their order and parallel groups
            stages[where] = stage.model_dump(exclude={"tasks"})
            stages[where]["tasks"] = {t.id: path for t, path, _ in placed}
            for t, _, parallel in placed:
                tasks[t.id] = {**t.model_dump(), "stage": where, "parallel": parallel}
    return {
        "stages": stages,
        "tasks": tasks,
        # publish stamps `since` and `required_from_version` (S2-25): not the draft's doing
        "fields": {
            k: f.model_dump(exclude={"since", "required_from_version"})
            for k, f in wf.fields.items()
        },
        "blocks": {p.key: p.model_dump()["regions"] for p in wf.apps.pages},
        "theme": wf.apps.theme,
        "accent": wf.apps.accent,
    }


def _by_key(live: dict[str, Any], draft: dict[str, Any]) -> KeyChanges:
    return KeyChanges(
        added=sorted(draft.keys() - live.keys()),
        removed=sorted(live.keys() - draft.keys()),
        changed=sorted(k for k in draft.keys() & live.keys() if draft[k] != live[k]),
    )


def _changed(live: str | None, draft: str | None) -> ValueChange | None:
    return None if live == draft else ValueChange(live=live, draft=draft)


def diff(live: dict[str, Any] | None, draft: dict[str, Any]) -> DraftDiff:
    """The draft against the live release's workflow (None before the first publish)."""
    old, new = _outline(live), _outline(draft)
    blocks = _by_key(old["blocks"], new["blocks"])
    return DraftDiff(
        stages=_by_key(old["stages"], new["stages"]),
        tasks=_by_key(old["tasks"], new["tasks"]),
        fields=_by_key(old["fields"], new["fields"]),
        blocks=sorted(blocks.added + blocks.removed + blocks.changed),
        theme=_changed(old["theme"], new["theme"]),
        accent=_changed(old["accent"], new["accent"]),
    )


# --- the client's vault ----------------------------------------------------------------------

_CHANGE = (
    "id, at, author_owner_id, conversation_id, change_request_id, tool, patch, summary, undoes,"
    " after_sha256"
)
# The stored draft's sha256 over jsonb's own text form, which is the same for the same document.
_SHA = "encode(sha256(convert_to(workflow::text, 'UTF8')), 'hex')"
_DRAFT_SHA = f"(select {_SHA} from app.drafts where id = 1)"
STALE = "The draft changed since you read it; read it again"


async def _draft(conn: AsyncConnection, *, lock: bool = False) -> dict[str, Any]:
    sql = "select workflow from app.drafts where id = 1" + (" for update" if lock else "")
    doc = (await conn.execute(text(sql))).scalar_one_or_none()
    if doc is None:
        raise NotFound(NO_DRAFT)
    return doc


async def read(conn: AsyncConnection) -> tuple[dict[str, Any], str]:
    """The draft and its sha256 (as `after_sha256` logs it), from one statement."""
    row = (
        await conn.execute(text(f"select workflow, {_SHA} from app.drafts where id = 1"))
    ).one_or_none()
    if row is None:
        raise NotFound(NO_DRAFT)
    return row[0], row[1]


async def _log(conn: AsyncConnection, after: dict[str, Any], change: dict[str, Any]) -> ChangeOut:
    """Write the patched draft and its change row (inside the caller's locked transaction)."""
    await conn.execute(
        text(
            "update app.drafts set workflow = cast(:w as jsonb), updated_at = now(),"
            " updated_by = :by where id = 1"
        ),
        {"w": json.dumps(after), "by": change["author_owner_id"]},
    )
    params = {"conversation_id": None, "change_request_id": None, "undoes": None, **change}
    params["patch"], params["inverse"] = json.dumps(params["patch"]), json.dumps(params["inverse"])
    result = await conn.execute(
        text(
            "insert into app.draft_changes (author_owner_id, conversation_id, change_request_id,"
            " tool, patch, inverse, summary, undoes, after_sha256) values (:author_owner_id,"
            " :conversation_id, :change_request_id, :tool, cast(:patch as jsonb),"
            f" cast(:inverse as jsonb), :summary, :undoes, {_DRAFT_SHA}) returning {_CHANGE}"
        ),
        params,
    )
    return ChangeOut(**result.mappings().one())


async def _change(
    conn: AsyncConnection, doc: dict[str, Any], change: dict[str, Any]
) -> ChangeOut | None:
    """Validate and log one change to `doc`, the draft as locked by the caller; None when the
    patch changes nothing (nothing is logged)."""
    after, undo_patch = patched(doc, change["patch"], await releases.published_fields(conn))
    if _canonical(after) == _canonical(doc):
        return None
    return await _log(conn, after, {**change, "inverse": undo_patch})


async def apply_change(
    ctx: ClientContext,
    author: UUID,
    tool: str,
    patch: Patch | Callable[[dict[str, Any]], Patch],
    summary: str,
    conversation_id: UUID | None = None,
    change_request_id: UUID | None = None,
    draft_sha256: str | None = None,
) -> ChangeOut | None:
    """Apply and log one change; None when the patch changes nothing (nothing is logged). A patch
    that points into lists (the agent's tools, #36) is a function of the draft, built from the
    locked one, so a change landing just before can't shift what it points at. With
    `draft_sha256`, the sha256 of the draft the caller read, a draft changed since is a 409: the
    change would overwrite an edit the caller hasn't seen."""
    async with ctx.db.begin() as conn:
        doc = await _draft(conn, lock=True)
        if draft_sha256 is not None and (await read(conn))[1] != draft_sha256:
            raise Conflict(STALE)
        return await _change(
            conn,
            doc,
            {
                "author_owner_id": author,
                "conversation_id": conversation_id,
                "change_request_id": change_request_id,
                "tool": tool,
                "patch": patch(doc) if callable(patch) else patch,
                "summary": summary,
            },
        )


async def undo(ctx: ClientContext, author: UUID) -> ChangeOut:
    """Undo the newest change not yet undone (undos themselves aren't undone). 409 if there is
    none, if the draft was written outside the log since that change, or if undoing it would
    delete or retype a published field."""
    async with ctx.db.begin() as conn:
        doc = await _draft(conn, lock=True)
        result = await conn.execute(
            text(
                f"select id, inverse, summary, after_sha256 = {_DRAFT_SHA} as current"
                " from app.draft_changes c where undoes is null"
                " and not exists (select from app.draft_changes u where u.undoes = c.id)"
                " order by id desc limit 1"
            )
        )
        last = result.mappings().one_or_none()
        if last is None:
            raise Conflict("Nothing to undo")
        if not last["current"]:
            raise Conflict(
                "The draft was changed outside the change log after this change (reseeded or"
                " edited directly), so it can't be undone"
            )
        after = _apply(doc, last["inverse"])  # exactly the earlier state: not validated again
        # except against a release published since: every later publish would refuse the draft
        published = await releases.published_fields(conn)
        had = versioning.errors(published, doc)
        brought = [e.message for e in versioning.errors(published, after) if e not in had]
        if brought:
            raise Conflict(f"Can't undo {last['summary']!r}: " + "; ".join(brought))
        return await _log(
            conn,
            after,
            {
                "author_owner_id": author,
                "tool": "undo",
                "patch": last["inverse"],
                "inverse": inverse(doc, after),
                "summary": f"Undo: {last['summary']}",
                "undoes": last["id"],
            },
        )


async def validate_draft(ctx: ClientContext) -> list[FieldError]:
    async with ctx.db.connect() as conn:
        doc = await _draft(conn)
        published = await releases.published_fields(conn)
    return _errors(problems(doc, published))


async def diff_vs_live(ctx: ClientContext) -> DraftDiff:
    async with ctx.db.connect() as conn:
        draft = await _draft(conn)
        live = await releases.active_release(conn)
    try:
        return diff(live.workflow if live else None, draft)
    except (SchemaVersionError, ValueError) as e:  # ValidationError is a ValueError
        raise UnprocessableContent(
            f"The draft can't be compared with the live release: {e}"
        ) from None


# --- API: /owner/clients/{client_id}/draft ---------------------------------------------------


async def owner_client(client_id: UUID, request: Request) -> ClientContext:
    """The path's client (the owner router has checked the token)."""
    async with request.app.state.platform.engine().connect() as conn:
        row = await get_client(conn, "id", client_id)
    if row is None:
        raise NotFound("No such client")
    return await request.app.state.resolver.context(row)


OwnedClient = Annotated[ClientContext, Depends(owner_client)]


class ThemeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    theme: str | None = None
    """Theme preset key (D19), checked as the draft's `apps.theme`."""
    accent: str | None = None
    """The client's default accent; null = the preset's own."""


routes = owner_router()
DRAFT = "/clients/{client_id}/draft"


async def _draft_out(ctx: ClientContext) -> DraftOut:
    async with ctx.db.connect() as conn:
        result = await conn.execute(
            text(
                "select workflow, template_key, base_version, updated_at, updated_by"
                " from app.drafts where id = 1"
            )
        )
        row = result.mappings().one_or_none()
    if row is None:
        raise NotFound(NO_DRAFT)
    return DraftOut(**row)


@routes.get(DRAFT)
async def get_draft(ctx: OwnedClient) -> DraftOut:
    """The client's draft `workflow.json`."""
    return await _draft_out(ctx)


@routes.get(DRAFT + "/changes")
async def list_changes(
    ctx: OwnedClient, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[ChangeOut]:
    """The change log, newest first."""
    async with ctx.db.connect() as conn:
        result = await conn.execute(
            text(f"select {_CHANGE} from app.draft_changes order by id desc limit :n"),
            {"n": limit},
        )
        return [ChangeOut(**r) for r in result.mappings()]


@routes.post(DRAFT + "/undo")
async def undo_change(ctx: OwnedClient, owner: OwnerCtx) -> DraftOut:
    """Undo the newest change not yet undone; 409 when there is none or it can't be undone (the
    draft was written outside the log since, or a published field would be deleted or retyped)."""
    await undo(ctx, owner.owner_id)
    return await _draft_out(ctx)


@routes.get(DRAFT + "/diff")
async def get_diff(ctx: OwnedClient) -> DraftDiff:
    """The draft against the active release (everything is added before the first publish)."""
    return await diff_vs_live(ctx)


@routes.get(DRAFT + "/validate")
async def get_validation(ctx: OwnedClient) -> list[FieldError]:
    """Every problem in the draft; empty when it is valid."""
    return await validate_draft(ctx)


@routes.patch(DRAFT + "/theme")
async def set_theme(body: ThemeIn, ctx: OwnedClient, owner: OwnerCtx) -> DraftOut:
    """The client's theme preset and default accent (D19); only the keys sent change."""
    look = body.model_dump(exclude_unset=True)
    summary = "; ".join(f"{k.title()}: {v or 'preset default'}" for k, v in look.items())
    async with ctx.db.begin() as conn:
        # the patch is built from the locked draft (`apps` may be missing), never a stale copy
        before = await _draft(conn, lock=True)
        after = {**before, "apps": {**before.get("apps", {}), **look}}
        patch = jsonpatch.make_patch(before, after).patch
        change = {"author_owner_id": owner.owner_id, "tool": "set_theme", "patch": patch}
        await _change(conn, before, {**change, "summary": summary})
    return await _draft_out(ctx)
