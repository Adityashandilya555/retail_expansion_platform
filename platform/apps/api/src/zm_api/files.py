"""Files (#51, S2-10): document slots with their own rules, uploaded straight into the client's
private bucket.

1. `POST /app/files/upload` checks that the user may fill the slot (submit the task, as
   `POST /app/tasks/{id}/submit` does, or start the flow) and the slot's rule (`x-zm-document`:
   the extension in `accept`, the size within `max_mb`), records a `pending` row and answers with
   a signed upload URL;
2. the browser PUTs the file to Storage;
3. `POST /app/files/{id}/confirm` streams the object back, measures and hashes it, and checks the
   magic bytes and the stored type of pdf / jpg / png (S3-D2; other types by name only). A file
   that fails is deleted and `rejected` (422 with the reason); one that passes is `confirmed` and
   gives the form its value `{key, sha256, name}`.

Submit and start then take a document value only for a confirmed upload of that slot
(`tasks._validate`). Committing the submission, or the start claiming its uploads (S3-D3), makes
the file the next version of its (case, field), so an upload nobody submits never becomes the
case's document. `GET /app/files/{id}/download` gives a 60 s signed URL to whoever may see the
case and the field, inline only for pdf / png / jpg; `GET /app/cases/{code}/files` lists a case's
versions (`cases.case_files`).

Keys (§09, S3-D3): `cases/<case code>/<task>/<field>/<uuid>-<safe name>`, and for a start form
`start/<flow>/<user id>/<field>/<uuid>-<safe name>`. The database holds keys and checksums, never
URLs. A workspace without Storage (qsr-local, D11) answers 503; another client's id is 404.
"""

import hashlib
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text

from zm_api import cases, tasks
from zm_api.context import ClientContext, ClientCtx, UserId, client_router
from zm_api.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    ServiceUnavailable,
    UnprocessableContent,
)
from zm_api.storage import Storage

MB = 1024 * 1024
START = "start"  # the task_def_key of a start form's uploads
# S3-D2: the types the API checks by their first bytes, each with the one Content-Type Storage may
# keep for it (it serves what the uploader's PUT said). Only these are ever served inline, as a
# preview; every other type only as a download. Other extensions are checked by name only.
INLINE = {
    "pdf": ("application/pdf", b"%PDF-"),
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}
HEAD = max(len(magic) for _, magic in INLINE.values())
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


# --- rules (pure) ----------------------------------------------------------------------------


def extension(name: str) -> str:
    return name.rpartition(".")[2].lower() if "." in name else ""


def safe_name(name: str) -> str:
    """A file name as the last part of a key: ASCII letters, digits, `.`, `_` and `-`, at most
    100 characters, the extension kept."""
    return _UNSAFE.sub("-", name)[-100:].strip("-.") or "file"


def rule_error(rule: Mapping[str, Any], name: str, size: int) -> str | None:
    """Why a file breaks its slot's rule (`x-zm-document`), or None; @zm/forms says the same."""
    accept = [a.lower().lstrip(".") for a in rule["accept"]]
    if extension(name) not in accept:
        return f"Only {', '.join(a.upper() for a in accept)} files go here"
    if size > rule["max_mb"] * MB:
        return f"The file is larger than {rule['max_mb']} MB"
    return None


@dataclass(frozen=True)
class Measured:
    size: int
    sha256: str
    head: bytes
    """The first HEAD bytes."""


async def measure(chunks: AsyncIterator[bytes], limit: int) -> Measured | None:
    """Size, sha256 and first bytes of a stream, one chunk in memory at a time; None as soon as
    it passes `limit` bytes."""
    digest, size, head = hashlib.sha256(), 0, b""
    async for chunk in chunks:
        size += len(chunk)
        if size > limit:
            return None
        digest.update(chunk)
        if len(head) < HEAD:
            head = (head + chunk)[:HEAD]
    return Measured(size, digest.hexdigest(), head)


def content_error(name: str, declared: int, found: Measured | None, stored_type: str) -> str | None:
    """Why a stored file isn't the one announced at upload, or None. Its size must be the
    declared one, which passed the slot's `max_mb`; a pdf / png / jpg must start like one and be
    kept as its own type, so Storage never serves it as anything a browser would run."""
    if found is None or found.size != declared:
        return "The file isn't the size announced at upload; upload it again"
    ext = extension(name)
    if ext in INLINE:
        mime, magic = INLINE[ext]
        if not found.head.startswith(magic):
            return f"This isn't a real {ext.upper()} file"
        if stored_type != mime:
            return f"The file wasn't sent as a {ext.upper()} file; upload it again"
    return None


# --- uploads ---------------------------------------------------------------------------------


def storage_of(ctx: ClientContext) -> Storage:
    if ctx.storage is None:
        raise ServiceUnavailable("Files aren't set up for this workspace")
    return ctx.storage


@dataclass(frozen=True)
class Slot:
    """Where an upload goes: the form's flow and task, the field, its rule and the key prefix."""

    flow: str
    task_def_key: str
    field: str
    rule: dict[str, Any]
    case_id: UUID | None
    prefix: str


def _rule(form: Mapping[str, Any], field: str) -> dict[str, Any]:
    """The rule of a document field on the form as the user sees it (sections hidden from their
    level are already gone); 404 for any other field."""
    prop = form["schema"].get("properties", {}).get(field) or {}
    if prop.get("x-zm-type") != "document" or "x-zm-document" not in prop:
        raise NotFound("No such document field on this form")
    return prop["x-zm-document"]


async def task_slot(ctx: ClientContext, user_id: UUID, task_id: str, field: str) -> Slot:
    """A document field of a task the user may submit (the submit route's own rule)."""
    view = await tasks.open_task(ctx, user_id, task_id)
    if not view.editable:
        raise NotFound("No such task")
    task = view.task["taskDefinitionKey"]
    return Slot(
        view.case.flow,
        task,
        field,
        _rule(view.form, field),
        view.case.id,
        f"cases/{view.case.code}/{task}/{field}",
    )


async def start_slot(ctx: ClientContext, user_id: UUID, flow_key: str, field: str) -> Slot:
    """A document field of the start form of a flow the user may start (`tasks.start_case`'s
    rule)."""
    async with ctx.db.connect() as conn:
        me = await tasks.person(conn, user_id)
        release = await tasks._active(conn)
    flow = tasks._flow(release.workflow, flow_key)
    if flow is None or flow_key not in release.process_keys:
        raise NotFound("No such flow")
    if not tasks.starter_groups(release.workflow, flow) & me.groups:
        raise Forbidden("You can't start this flow")
    form = tasks.visible_form(release.forms[f"start:{flow_key}"], me.levels)
    prefix = f"start/{flow_key}/{user_id}/{field}"
    return Slot(flow_key, START, field, _rule(form, field), None, prefix)


@dataclass(frozen=True)
class Upload:
    file_id: UUID
    upload_url: str
    key: str
    expires_at: datetime


async def upload(
    ctx: ClientContext, storage: Storage, user_id: UUID, slot: Slot, name: str, size: int
) -> Upload:
    """A `pending` row and a signed URL to PUT the file to, if it fits the slot's rule."""
    if reason := rule_error(slot.rule, name, size):
        raise UnprocessableContent(reason)
    key = f"{slot.prefix}/{uuid.uuid4()}-{safe_name(name)}"
    async with ctx.db.begin() as conn:
        file_id = (
            await conn.execute(
                text(
                    "insert into app.files (case_id, flow_key, task_def_key, field, key, name,"
                    " size_bytes, uploaded_by) values (:c, :f, :t, :field, :k, :n, :s, :u)"
                    " returning id"
                ),
                {
                    "c": slot.case_id,
                    "f": slot.flow,
                    "t": slot.task_def_key,
                    "field": slot.field,
                    "k": key,
                    "n": name,
                    "s": size,
                    "u": user_id,
                },
            )
        ).scalar_one()
    url, expires = await storage.signed_upload(key)
    return Upload(file_id, url, key, expires)


_FILE_COLUMNS = "id, case_id, field, key, name, size_bytes, sha256, status, uploaded_by"


def _value(row: Any) -> dict[str, str]:
    """A confirmed file as the form holds it (the compiled `document` schema)."""
    return {"key": row.key, "sha256": row.sha256, "name": row.name}


async def _own(ctx: ClientContext, user_id: UUID, file_id: UUID) -> Any:
    async with ctx.db.connect() as conn:
        row = (
            await conn.execute(
                text(f"select {_FILE_COLUMNS} from app.files where id = :id and uploaded_by = :u"),
                {"id": file_id, "u": user_id},
            )
        ).one_or_none()
    if row is None:  # someone else's upload, or another client's id
        raise NotFound("No such upload")
    return row


async def confirm(ctx: ClientContext, user_id: UUID, file_id: UUID) -> dict[str, str]:
    """Check the stored object and confirm it; a file that fails is deleted and rejected (422).
    One not stored yet (a confirm sent before its upload finished) is 409 and stays pending.
    Repeating a confirmed one answers the same value."""
    row = await _own(ctx, user_id, file_id)
    if row.status == "confirmed":
        return _value(row)
    if row.status == "rejected":
        raise Conflict("This upload was refused; upload the file again")
    storage = storage_of(ctx)
    async with storage.read(row.key) as stored:
        found = None if stored is None else await measure(stored.chunks, row.size_bytes)
    if stored is None:
        raise Conflict("The file hasn't arrived yet; confirm it once its upload has finished")
    if reason := content_error(row.name, row.size_bytes, found, stored.content_type):
        await storage.delete(row.key)
        async with ctx.db.begin() as conn:
            await conn.execute(
                text(
                    "update app.files set status = 'rejected' where id = :id and status = 'pending'"
                ),
                {"id": file_id},
            )
        raise UnprocessableContent(reason)
    assert found is not None
    async with ctx.db.begin() as conn:
        done = (
            await conn.execute(
                text(
                    "update app.files set status = 'confirmed', sha256 = :sha,"
                    " content_type = :type, confirmed_at = now()"
                    f" where id = :id and status = 'pending' returning {_FILE_COLUMNS}"
                ),
                {"id": file_id, "sha": found.sha256, "type": stored.content_type or None},
            )
        ).one_or_none()
    if done is None:  # a confirm running alongside settled it first
        return await confirm(ctx, user_id, file_id)
    return _value(done)


async def download(
    ctx: ClientContext, user_id: UUID, file_id: UUID, attachment: bool
) -> tuple[str, datetime]:
    """A signed URL for a confirmed file, if the user may see its case and its field: anyone in
    the client sees every case (visibility v1), and a field every section of which is hidden
    below the user's level is 404. A file nothing has committed yet (no version) is only its
    uploader's. Only a pdf / png / jpg is served inline; anything else always downloads."""
    async with ctx.db.connect() as conn:
        me = await tasks.person(conn, user_id)
        row = (
            await conn.execute(
                text(
                    "select f.key, f.name, f.field, f.case_id, f.version, f.uploaded_by,"
                    " r.workflow from app.files f left join app.cases c on c.id = f.case_id"
                    " left join app.releases r on r.version = c.release_version"
                    " where f.id = :id and f.status = 'confirmed'"
                ),
                {"id": file_id},
            )
        ).one_or_none()
    if (
        row is None
        or (row.version is None and row.uploaded_by != user_id)
        or (row.case_id is not None and row.field in tasks.hidden_fields(row.workflow, me.levels))
    ):
        raise NotFound("No such file")
    inline = not attachment and extension(row.name) in INLINE
    return await storage_of(ctx).signed_download(row.key, None if inline else row.name)


# --- routes ----------------------------------------------------------------------------------

routes = client_router()


class _Model(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)


class UploadIn(_Model):
    task_id: str | None = None
    """The task whose form holds the field; or `flow`, for its start form."""
    flow: str | None = None
    field: str
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    """The file's name; its extension must be one the field accepts."""
    size_bytes: Annotated[int, Field(gt=0)]


class UploadOut(_Model):
    file_id: UUID
    upload_url: str
    """PUT the file here, with its Content-Type, before `expires_at`."""
    key: str
    expires_at: datetime


class DocumentValue(_Model):
    key: str
    sha256: str
    name: str


class SignedUrl(_Model):
    url: str
    expires_at: datetime
    """60 seconds after it was made; then Storage refuses it."""


class FileOut(_Model):
    id: UUID
    field: str
    version: int
    name: str
    size_bytes: int
    content_type: str | None
    uploaded_by: UUID
    uploaded_by_name: str | None
    uploaded_at: datetime


@routes.post("/files/upload", status_code=201)
async def upload_file(body: UploadIn, ctx: ClientCtx, uid: UserId) -> UploadOut:
    """Start an upload to a document field of a task the user may submit, or of the start form
    of a flow they may start: the type and size must fit the field's rule. PUT the file to
    `upload_url`, then confirm it."""
    storage = storage_of(ctx)
    if (body.task_id is None) == (body.flow is None):
        raise BadRequest("Send either task_id or flow")
    if body.task_id is not None:
        slot = await task_slot(ctx, uid, body.task_id, body.field)
    else:
        slot = await start_slot(ctx, uid, body.flow or "", body.field)
    found = await upload(ctx, storage, uid, slot, body.name, body.size_bytes)
    return UploadOut(**found.__dict__)


@routes.post("/files/{file_id}/confirm")
async def confirm_file(file_id: UUID, ctx: ClientCtx, uid: UserId) -> DocumentValue:
    """Check the uploaded file (size, sha256, and the first bytes of a PDF, JPG or PNG) and
    confirm it: the answer is the field's value for the form. A file that fails is deleted (422,
    with the reason)."""
    return DocumentValue(**await confirm(ctx, uid, file_id))


@routes.get("/files/{file_id}/download")
async def download_file(
    file_id: UUID, ctx: ClientCtx, uid: UserId, attachment: bool = False
) -> SignedUrl:
    """A signed URL for the file that works for 60 seconds: inline (a preview) or, with
    `attachment=true`, as a download under the file's name."""
    url, expires = await download(ctx, uid, file_id, attachment)
    return SignedUrl(url=url, expires_at=expires)


@routes.get("/cases/{code}/files")
async def list_case_files(
    code: str, ctx: ClientCtx, uid: UserId, field: str | None = None
) -> list[FileOut]:
    """The case's confirmed files by field, newest version first; a field whose rule doesn't
    keep versions lists only its latest. Fields hidden from the user's level are left out."""
    storage_of(ctx)
    return [FileOut(**f.__dict__) for f in await cases.case_files(ctx, uid, code, field)]
