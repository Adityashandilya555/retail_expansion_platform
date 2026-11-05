"""Change requests (S2-24a): the client admin asks for a field, a document type, a select option, a
new label or a field made required or optional. We review each one in the owner console (S2-24b),
apply it with the agent and publish a new version; nothing a client sends goes live without that.

Requests live in the control plane (`platform.change_requests`) next to every other client's, so
each query here is scoped to the JWT's client: another client's request is a 404. A payload names
forms and fields of the client's active release (else 422), and new fields take a type from
`zm_schema`'s field catalogue.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from sqlalchemy import text

from zm_api import releases
from zm_api.context import ClientContext, ClientCtx, UserId, client_context
from zm_api.errors import Conflict, FieldError, NotFound, UnprocessableContent
from zm_api.people import client_admin
from zm_api.ui import UiField, form_fields
from zm_schema import WorkflowV1
from zm_schema.models import FieldType

Key = Annotated[str, StringConstraints(min_length=1, max_length=64)]  # a release's form or field
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Kind = Literal[
    "add_field", "rename_field_label", "add_document_type", "set_required", "add_select_option"
]
Status = Literal["submitted", "in_review", "approved", "rejected", "shipped"]
LISTS = ("select", "multi_select")  # the field types with options
MAX_PAYLOAD = 16_000  # characters of JSON: one request in the table every client shares


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class NewField(_In):
    label: Label
    type: FieldType
    options: list[Label] = Field(default=[], max_length=100, validate_default=True)
    """The choices of a `select` or `multi_select` field, and only of those."""

    @field_validator("options")
    @classmethod
    def _options_for_lists(cls, options: list[str], info: ValidationInfo) -> list[str]:
        listed = info.data.get("type") in LISTS
        if listed and not options:
            raise ValueError("A select or multi_select field needs options")
        if options and not listed:
            raise ValueError("Only a select or multi_select field takes options")
        return options


class AddField(_In):
    kind: Literal["add_field"]
    form: Key
    fields: list[NewField] = Field(min_length=1, max_length=20)


class RenameFieldLabel(_In):
    kind: Literal["rename_field_label"]
    field: Key
    label: Label
    """The new label; the stored key stays."""


class AddDocumentType(_In):
    kind: Literal["add_document_type"]
    form: Key
    label: Label


class SetRequired(_In):
    kind: Literal["set_required"]
    field: Key
    required: bool


class AddSelectOption(_In):
    kind: Literal["add_select_option"]
    field: Key
    """A `select` or `multi_select` field."""
    label: Label


Payload = Annotated[
    AddField | RenameFieldLabel | AddDocumentType | SetRequired | AddSelectOption,
    Field(discriminator="kind"),
]


class ChangeRequestIn(_In):
    payload: Payload
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    """Why, for the reviewer."""

    @field_validator("payload")
    @classmethod
    def _bounded(cls, payload: Payload) -> Payload:
        if len(payload.model_dump_json()) > MAX_PAYLOAD:
            raise ValueError(f"The request is over {MAX_PAYLOAD} characters; split it up")
        return payload


class ChangeRequestOut(BaseModel):
    """A stored request as stored: a payload sent under older rules still reads, so one old row
    can't fail the list."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    id: UUID
    kind: Kind
    payload: dict[str, Any]
    """The `Payload` as sent, `kind` included."""
    note: str | None
    status: Status
    review_note: str | None
    """Our answer, once reviewed."""
    shipped_in_version: int | None
    """The release that carries it, once shipped."""
    created_at: datetime
    updated_at: datetime


class TargetForm(BaseModel):
    id: str
    title: str
    """The form's first section title, e.g. "Site"."""
    fields: list[UiField]


class ChangeTargets(BaseModel):
    forms: list[TargetForm]
    """The active release's forms (none before the first publish)."""
    field_types: list[FieldType]


async def _released(ctx: ClientContext) -> WorkflowV1 | None:
    async with ctx.db.connect() as conn:
        release = await releases.active_release(conn)
    if release is None:
        return None
    return WorkflowV1.model_validate(release.workflow, context={"structural_only": True})


def target_error(wf: WorkflowV1 | None, payload: Payload) -> FieldError | None:
    """The form or field the payload names that the active release doesn't have."""
    forms, fields = (wf.forms, wf.fields) if wf else ({}, {})
    form, field = getattr(payload, "form", None), getattr(payload, "field", None)
    if form is not None and form not in forms:
        message = f"{form} isn't a form in the published release"
        return FieldError(path="/payload/form", message=message)
    if field is not None and field not in fields:
        message = f"{field} isn't a field in the published release"
        return FieldError(path="/payload/field", message=message)
    if isinstance(payload, AddSelectOption) and fields[payload.field].type not in LISTS:
        return FieldError(path="/payload/field", message=f"{payload.field} isn't a select field")
    return None


_COLUMNS = (
    "id, kind, payload, note, status, review_note, shipped_in_version, created_at, updated_at"
)


async def _requests(
    request: Request,
    ctx: ClientContext,
    where: str = "true",
    limit: int | None = None,
    offset: int = 0,
    **params: Any,
) -> list[ChangeRequestOut]:
    """`where` is one of this module's constant conditions, never input. No `limit` is all."""
    async with request.app.state.platform.engine().connect() as conn:
        rows = await conn.execute(
            text(
                f"select {_COLUMNS} from platform.change_requests where client_id = :c and {where}"
                " order by created_at desc, id desc limit :limit offset :offset"
            ),
            {"c": ctx.client.id, "limit": limit, "offset": offset, **params},
        )
    return [ChangeRequestOut(**r) for r in rows.mappings()]


routes = APIRouter(
    prefix="/app/admin", tags=["app"], dependencies=[Depends(client_context), Depends(client_admin)]
)


@routes.get("/change-requests")
async def list_change_requests(
    ctx: ClientCtx,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChangeRequestOut]:
    """The client's requests, newest first, `limit` at a time; fewer than `limit` is the end."""
    return await _requests(request, ctx, limit=limit, offset=offset)


@routes.post("/change-requests", status_code=201)
async def submit_change_request(
    body: ChangeRequestIn,
    ctx: ClientCtx,
    uid: UserId,
    request: Request,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ] = None,
) -> ChangeRequestOut:
    """With an `Idempotency-Key`, sending the same request again returns the first one instead
    of storing it twice."""
    note = body.note or None
    # a repeat is answered before validation: the first one was stored, even if a release
    # published since no longer has what it names
    if idempotency_key is not None:
        first = await _first(request, ctx, idempotency_key)
        if first is not None:
            return _same(first, body, note)
    error = target_error(await _released(ctx), body.payload)
    if error:
        raise UnprocessableContent(
            "The request names something the published release doesn't have", errors=[error]
        )
    async with request.app.state.platform.engine().begin() as conn:
        row = await conn.execute(
            text(
                "insert into platform.change_requests (client_id, submitted_by, kind, payload,"
                " note, idempotency_key) values (:c, :by, :kind, cast(:payload as jsonb), :note,"
                f" :key) on conflict (client_id, idempotency_key) do nothing returning {_COLUMNS}"
            ),
            {
                "c": ctx.client.id,
                "by": uid,
                "kind": body.payload.kind,
                "payload": body.payload.model_dump_json(),
                "note": note,
                "key": idempotency_key,
            },
        )
        stored = row.mappings().one_or_none()
    if stored is not None:
        return ChangeRequestOut(**stored)
    # the same key raced us in (no key never conflicts): theirs is the first request
    first = await _first(request, ctx, str(idempotency_key))
    if first is None:  # stored and then removed in between
        raise Conflict("This Idempotency-Key's request is gone; send it with a new key")
    return _same(first, body, note)


async def _first(request: Request, ctx: ClientContext, key: str) -> ChangeRequestOut | None:
    """The request this client already sent with `key`, if any."""
    found = await _requests(request, ctx, "idempotency_key = :key", key=key)
    return found[0] if found else None


def _same(first: ChangeRequestOut, body: ChangeRequestIn, note: str | None) -> ChangeRequestOut:
    """The first request, when this one repeats it; a key reused for something else is 409."""
    if (first.payload, first.note) != (body.payload.model_dump(mode="json"), note):
        raise Conflict("This Idempotency-Key was used for another request")
    return first


@routes.get("/change-requests/targets")
async def change_targets(ctx: ClientCtx) -> ChangeTargets:
    """What a request may name: the active release's forms with their fields, and field types."""
    wf = await _released(ctx)
    forms = [
        TargetForm(id=key, title=form.sections[0].title, fields=form_fields(wf, key))
        for key, form in (wf.forms.items() if wf else [])
    ]
    return ChangeTargets(forms=forms, field_types=list(get_args(FieldType)))


@routes.get("/change-requests/{change_request_id}")
async def get_change_request(
    change_request_id: UUID, ctx: ClientCtx, request: Request
) -> ChangeRequestOut:
    """Another client's request is a 404, like one that doesn't exist."""
    found = await _requests(request, ctx, "id = :id", id=change_request_id)
    if not found:
        raise NotFound("No such change request")
    return found[0]
