"""Reviewing change requests in the owner console (S2-24b, #46; S2-24a's requests in the control
plane). An owner sees every client's requests, opens one with a versioning-impact dry run, and
either rejects it with a note the client admin sees, or applies it with the agent and approves it.
Nothing applies a request by itself: the dry run works on a copy of the draft, and the agent edits
through its tools in a conversation started for the request (`agent.loop`), each edit logged with
the request's id. Publish (S2-09) then ships every approved request whose edits are in the release.

Statuses: `submitted` → `in_review` (an apply conversation started) → `approved` (the owner checked
the agent's edits; at least one must still be in the draft) → `shipped` (in release vN). `rejected`
is open from `submitted` and `in_review`; a rejected request's edits, if any, stay in the draft
for the owner to undo.
"""

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import jsonpatch
from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import drafts, releases
from zm_api.change_requests import Kind, Status
from zm_api.context import ClientContext, OwnerCtx, owner_router
from zm_api.errors import Conflict, FieldError, NotFound
from zm_api.registry import get_client

Doc = dict[str, Any]
Patch = list[Doc]
OPEN = ("submitted", "in_review")  # a request still waiting for a decision
NO_CHANGES = "Apply the request with the agent first: no edit in the draft is linked to it"
DECIDED = "This request is already {status}"
DOCUMENT_RULE = {"accept": ["pdf"], "max_mb": 25, "keep_versions": True}


# --- what a request says and does (no database) ------------------------------------------------


def summary(payload: Mapping[str, Any]) -> str:
    """One line for the queue and the agent, e.g. "Add State, District to the start form"."""
    match payload["kind"]:
        case "add_field":
            labels = ", ".join(f["label"] for f in payload["fields"])
            return f"Add {labels} to the {payload['form']} form"
        case "rename_field_label":
            return f"Rename {payload['field']} to {payload['label']}"
        case "set_required":
            return f"Make {payload['field']} {'required' if payload['required'] else 'optional'}"
        case "add_document_type":
            return f"Add a {payload['label']} upload to the {payload['form']} form"
        case "add_select_option":
            return f"Add the option {payload['label']} to {payload['field']}"
    return str(payload["kind"])


class Missing(Exception):
    """The request names a form or field the draft no longer has."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path


def _slug(label: str, taken: set[str]) -> str:
    """A field or option key from `label`: lowercase, starting with a letter, 41 at most, and
    not one of `taken` (`state`, then `state_2`, …)."""
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:38] or "field"
    if not base[0].isalpha():
        base = f"f_{base}"[:38]
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def _form(doc: Doc, form: str) -> Doc:
    if form not in doc.get("forms", {}):
        raise Missing("/payload/form", f"{form} isn't in the draft any more")
    return doc["forms"][form]


def _field(doc: Doc, key: str) -> Doc:
    if key not in doc.get("fields", {}):
        raise Missing("/payload/field", f"{key} isn't in the draft any more")
    return doc["fields"][key]


def _placed(doc: Doc, form: str, key: str) -> Doc:
    """A row holding `key` at the end of the form's last section, as add_document_slot does."""
    last = len(_form(doc, form)["sections"]) - 1
    return {"op": "add", "path": f"/forms/{form}/sections/{last}/rows/-", "value": [{"field": key}]}


def proposal(doc: Doc, payload: Mapping[str, Any]) -> Patch:
    """The patch that would apply `payload` to `doc`, for the dry run only: the agent applies a
    request with its own tools, where the owner sees each edit."""
    taken = set(doc.get("fields", {}))
    match payload["kind"]:
        case "add_field":
            _form(doc, payload["form"])
            patch: Patch = []
            for new in payload["fields"]:
                key = _slug(new["label"], taken)
                value: Doc = {"type": new["type"], "label": new["label"], "required": False}
                if new.get("options"):
                    ids: set[str] = set()
                    value["options"] = [{"id": _slug(o, ids), "label": o} for o in new["options"]]
                patch += [
                    {"op": "add", "path": f"/fields/{key}", "value": value},
                    _placed(doc, payload["form"], key),
                ]
            return patch
        case "rename_field_label":
            _field(doc, payload["field"])
            return [
                {
                    "op": "add",
                    "path": f"/fields/{payload['field']}/label",
                    "value": payload["label"],
                }
            ]
        case "set_required":
            _field(doc, payload["field"])
            at = f"/fields/{payload['field']}/required"
            return [{"op": "add", "path": at, "value": payload["required"]}]
        case "add_document_type":
            _form(doc, payload["form"])
            key = _slug(payload["label"], taken)
            value = {
                "type": "document",
                "label": payload["label"],
                "required": False,
                "document": DOCUMENT_RULE,
            }
            return [
                {"op": "add", "path": f"/fields/{key}", "value": value},
                _placed(doc, payload["form"], key),
            ]
        case "add_select_option":
            options = _field(doc, payload["field"]).get("options", [])
            option = {
                "id": _slug(payload["label"], {o["id"] for o in options}),
                "label": payload["label"],
            }
            return [{"op": "add", "path": f"/fields/{payload['field']}/options/-", "value": option}]
    raise Missing("/payload/kind", f"{payload['kind']} isn't a request kind")


def dry_run(doc: Doc, payload: Mapping[str, Any]) -> Doc:
    """`doc` as it would be with the request applied; `doc` itself is left alone."""
    return jsonpatch.apply_patch(copy.deepcopy(doc), proposal(doc, payload))


def _notes(payload: Mapping[str, Any]) -> list[str]:
    """What the request does to sites, by S2-25's rules (additive changes only)."""
    match payload["kind"]:
        case "add_field" | "add_document_type":
            return [
                f"{summary(payload)}. New sites fill it in; running sites keep their release's"
                " form, and their records show it empty."
            ]
        case "rename_field_label":
            return [f"{summary(payload)}. The stored key and values stay; only the label changes."]
        case "set_required" if payload["required"]:
            return [
                f"{summary(payload)}. It binds only sites started on the next release or later;"
                " running sites aren't asked for it."
            ]
        case "set_required":
            return [f"{summary(payload)}. Sites started on the next release may leave it empty."]
        case "add_select_option":
            return [f"{summary(payload)}. Values already stored keep their option."]
    return []


@dataclass
class Impact:
    problems: list[FieldError] = field(default_factory=list)
    """What would stop the draft from publishing with the request applied: the draft's schema
    problems and S2-25's versioning rules, or a form or field the draft no longer has."""
    notes: list[str] = field(default_factory=list)


def impact(draft: Doc, published: Sequence[Doc], payload: Mapping[str, Any]) -> Impact:
    """The versioning-impact dry run: the request applied to a copy of the draft, then checked
    against every published release's fields."""
    try:
        after = dry_run(draft, payload)
    except Missing as e:
        return Impact([FieldError(path=e.path, message=str(e))], [])
    return Impact(drafts._errors(drafts.problems(after, published)), _notes(payload))


def describe(request: Mapping[str, Any]) -> str:
    """The request as the agent hears it, in each turn's system message (`agent.loop`)."""
    note = request.get("note") or "none"
    return (
        f"Change request {request['id']} from the client's admin: {request['summary']}."
        f" Their note: {note}. Payload: {json.dumps(request['payload'], sort_keys=True)}."
        " The owner reviews it with you: apply it with your tools when they ask, with field keys"
        " and placement that fit the draft. Every edit in this conversation is linked to it."
    )


# --- the control plane and the vault ---------------------------------------------------------


_COLUMNS = (
    "r.id, r.client_id, c.slug as client_slug, c.name as client_name, r.kind, r.payload, r.note,"
    " r.status, r.review_note, r.shipped_in_version, r.created_at, r.updated_at"
)
_FROM = "from platform.change_requests r join platform.clients c on c.id = r.client_id"


class ReviewOut(BaseModel):
    """A request as the owner console lists it, with its client."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    id: UUID
    client_id: UUID
    client_slug: str
    client_name: str
    kind: Kind
    payload: dict[str, Any]
    summary: str
    note: str | None
    status: Status
    review_note: str | None
    shipped_in_version: int | None
    created_at: datetime
    updated_at: datetime


class ImpactOut(BaseModel):
    problems: list[FieldError]
    notes: list[str]


class ReviewDetail(ReviewOut):
    impact: ImpactOut | None
    """The dry run on the client's draft; null while the client has no draft."""
    linked_changes: int
    """Edits in the draft linked to this request (undone ones don't count)."""


Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Note
    """Why, for the client admin."""


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Note | None = None
    """For the client admin, e.g. what was applied."""


def _out(row: Mapping[str, Any]) -> ReviewOut:
    return ReviewOut(**row, summary=summary(row["payload"]))


async def _one(conn: AsyncConnection, cr: UUID, lock: bool = False) -> ReviewOut:
    found = await conn.execute(
        text(f"select {_COLUMNS} {_FROM} where r.id = :id" + (" for update of r" if lock else "")),
        {"id": cr},
    )
    row = found.mappings().one_or_none()
    if row is None:
        raise NotFound("No such change request")
    return _out(row)


LINKED = (
    "select count(*) from app.draft_changes c where c.change_request_id = :cr"
    " and not exists (select 1 from app.draft_changes u where u.undoes = c.id)"
)


async def linked_changes(ctx: ClientContext, cr: UUID) -> int:
    async with ctx.db.connect() as conn:
        return (await conn.execute(text(LINKED), {"cr": cr})).scalar_one()


async def shipped_by(conn: AsyncConnection) -> list[UUID]:
    """The requests whose edits are in the client's draft now (not undone): what a publish of it
    ships. Run in the publish's vault transaction."""
    found = await conn.execute(
        text(
            "select distinct c.change_request_id from app.draft_changes c"
            " where c.change_request_id is not null"
            " and not exists (select 1 from app.draft_changes u where u.undoes = c.id)"
        )
    )
    return list(found.scalars())


async def ship(conn: AsyncConnection, client_id: UUID, ids: Sequence[UUID], version: int) -> None:
    """Mark the client's approved requests among `ids` shipped in `version` (S2-09's hook), in
    the publish's control-plane transaction."""
    if not ids:
        return
    await conn.execute(
        text(
            "update platform.change_requests set status = 'shipped', shipped_in_version = :v,"
            " updated_at = now() where client_id = :c and status = 'approved'"
            " and id = any(:ids)"
        ),
        {"c": client_id, "v": version, "ids": list(ids)},
    )


async def start_review(conn: AsyncConnection, client_id: UUID, cr: UUID) -> Doc:
    """For an apply conversation (`agent.loop`): the client's open request, now `in_review`, as
    the conversation keeps it. Another client's request is a 404; a decided one a 409."""
    found = await _one(conn, cr, lock=True)
    if found.client_id != client_id:
        raise NotFound("No such change request")
    if found.status not in OPEN:
        raise Conflict(DECIDED.format(status=found.status))
    await conn.execute(
        text(
            "update platform.change_requests set status = 'in_review', updated_at = now()"
            " where id = :id and status = 'submitted'"
        ),
        {"id": cr},
    )
    return {
        "id": str(cr),
        "kind": found.kind,
        "summary": found.summary,
        "payload": found.payload,
        "note": found.note,
    }


# --- API: /owner/change-requests -------------------------------------------------------------

routes = owner_router()


def _platform(request: Request) -> Any:
    return request.app.state.platform.engine()


async def _context(request: Request, client_id: UUID) -> ClientContext | None:
    """The client's resources, or None while it isn't ready (no vault to read yet)."""
    async with _platform(request).connect() as conn:
        row = await get_client(conn, "id", client_id)
    if row is None or row.status != "ready":
        return None
    return await request.app.state.resolver.context(row)


@routes.get("/change-requests")
async def list_reviews(
    request: Request,
    status: Status | None = None,
    client_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReviewOut]:
    """Every client's requests, newest first, filtered by status and client; fewer than `limit`
    is the end."""
    where = ["true"]
    if status is not None:
        where.append("r.status = cast(:status as platform.change_request_status)")
    if client_id is not None:
        where.append("r.client_id = :client")
    async with _platform(request).connect() as conn:
        rows = await conn.execute(
            text(
                f"select {_COLUMNS} {_FROM} where {' and '.join(where)}"
                " order by r.created_at desc, r.id desc limit :limit offset :offset"
            ),
            {"status": status, "client": client_id, "limit": limit, "offset": offset},
        )
    return [_out(r) for r in rows.mappings()]


@routes.get("/change-requests/{change_request_id}")
async def get_review(change_request_id: UUID, request: Request) -> ReviewDetail:
    """The request with its dry run on the client's draft and how many draft edits it has."""
    async with _platform(request).connect() as conn:
        found = await _one(conn, change_request_id)
    ctx = await _context(request, found.client_id)
    if ctx is None:
        return ReviewDetail(**found.model_dump(), impact=None, linked_changes=0)
    async with ctx.db.connect() as conn:
        draft = await releases.get_draft(conn)
        published = await releases.published_fields(conn)
        linked = (await conn.execute(text(LINKED), {"cr": change_request_id})).scalar_one()
    result = None
    if draft is not None:
        found_impact = impact(draft, published, found.payload)
        result = ImpactOut(problems=found_impact.problems, notes=found_impact.notes)
    return ReviewDetail(**found.model_dump(), impact=result, linked_changes=linked)


async def _decide(
    request: Request,
    change_request_id: UUID,
    owner: OwnerCtx,
    to: Literal["approved", "rejected"],
    note: str | None,
    allowed: tuple[str, ...],
) -> ReviewOut:
    async with _platform(request).begin() as conn:
        found = await _one(conn, change_request_id, lock=True)
        if found.status not in allowed:
            raise Conflict(DECIDED.format(status=found.status))
        if to == "approved":
            ctx = await _context(request, found.client_id)
            if ctx is None or await linked_changes(ctx, change_request_id) == 0:
                raise Conflict(NO_CHANGES)
        await conn.execute(
            text(
                "update platform.change_requests set status = cast(:to as"
                " platform.change_request_status), review_note = coalesce(:note, review_note),"
                " reviewed_by = :by, updated_at = now() where id = :id"
            ),
            {"to": to, "note": note, "by": owner.owner_id, "id": change_request_id},
        )
        return await _one(conn, change_request_id)


@routes.post("/change-requests/{change_request_id}/reject")
async def reject(
    change_request_id: UUID, body: RejectIn, owner: OwnerCtx, request: Request
) -> ReviewOut:
    """With a note the client admin sees. The draft is left as it is: undo any edits already
    applied for it in the dock."""
    return await _decide(request, change_request_id, owner, "rejected", body.note, OPEN)


@routes.post("/change-requests/{change_request_id}/approve")
async def approve(
    change_request_id: UUID, owner: OwnerCtx, request: Request, body: ApproveIn | None = None
) -> ReviewOut:
    """Once the agent's edits for it are in the draft (409 before); the next publish ships it."""
    note = body.note if body else None
    return await _decide(request, change_request_id, owner, "approved", note, ("in_review",))
