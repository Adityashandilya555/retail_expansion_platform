"""`/app/cases` and `/app/tasks` (#26): the HTTP side of `zm_api.tasks`."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field

from zm_api import tasks
from zm_api.context import ClientCtx, UserId, client_router
from zm_api.errors import BadRequest

routes = client_router()


class _Model(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, populate_by_name=True)


class CaseIn(_Model):
    flow: str
    """Key of one of the workflow's flows."""
    values: dict[str, Any] = {}
    """The flow's start form, field key → value."""


class CaseOut(_Model):
    id: UUID
    code: str
    """e.g. `MX-0001`; also the Operaton business key."""
    flow: str
    release_version: int
    process_instance_id: str
    created_at: datetime


class TaskItem(_Model):
    id: str
    name: str
    flow: str
    case_code: str
    stage: str | None
    """Key of the stage the task belongs to."""
    created: str
    assignee: str | None
    form_key: str | None
    """Operaton's form key, `zm:<task id>`."""


class FormOut(_Model):
    form_schema: dict[str, Any] = Field(alias="schema")
    """JSON Schema 2020-12 for the submission, without sections hidden from this user."""
    layout: list[dict[str, Any]]


class TaskDetail(_Model):
    task: TaskItem
    case: CaseOut
    editable: bool
    """False when the user may only read the task (level 2+ in its department)."""
    form: FormOut
    show: dict[str, Any]
    """Read-only values from earlier steps, by field key."""


class SubmitIn(_Model):
    values: dict[str, Any] = {}
    outcome: str | None = None
    """One of the task's outcome ids, when it has outcomes."""
    comment: str | None = None


class SubmitOut(_Model):
    submission_id: UUID
    status: Literal["pending", "completed", "committed", "failed"]
    """`committed` normally; `pending`/`completed` while a repeated request's first is running."""
    case_id: UUID
    release_version: int


def _task(item: tasks.TaskItem) -> TaskItem:
    return TaskItem(**item.__dict__)


@routes.post("/cases", status_code=201)
async def start_case(body: CaseIn, ctx: ClientCtx, uid: UserId) -> CaseOut:
    """Start a case of one of the workflow's flows, with its start form's values."""
    case = await tasks.start_case(ctx, uid, body.flow, body.values)
    return CaseOut(**case.__dict__)


@routes.get("/tasks")
async def list_tasks(
    ctx: ClientCtx,
    uid: UserId,
    scope: Literal["mine", "team"] = "mine",
    flow: str | None = None,
    case: str | None = None,
) -> list[TaskItem]:
    """`mine`: the user's tasks and their groups' unclaimed ones. `team` (level 2+): every task
    of their departments. `flow` and `case` (a case code) narrow the list."""
    return [_task(t) for t in await tasks.list_tasks(ctx, uid, scope, flow, case)]


@routes.get("/tasks/{task_id}")
async def get_task(task_id: str, ctx: ClientCtx, uid: UserId) -> TaskDetail:
    """One task with its case, its form as this user sees it, and the values it shows."""
    view = await tasks.open_task(ctx, uid, task_id)
    show = await tasks.shown_values(ctx, view.case.id, view.form["schema"].get("x-zm-show", []))
    return TaskDetail(
        task=_task(view.item),
        case=CaseOut(**view.case.__dict__),
        editable=view.editable,
        form=FormOut(**view.form),
        show=show,
    )


@routes.post("/tasks/{task_id}/submit")
async def submit_task(
    task_id: str,
    body: SubmitIn,
    ctx: ClientCtx,
    uid: UserId,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SubmitOut:
    """Submit a task. Send a fresh `Idempotency-Key` per submission; repeating a key returns the
    first result instead of submitting again."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise BadRequest("Send an Idempotency-Key header (up to 200 characters)")
    sub = await tasks.submit(
        ctx,
        uid,
        task_id,
        body.values,
        outcome=body.outcome,
        comment=body.comment,
        idempotency_key=idempotency_key,
    )
    return SubmitOut(
        submission_id=sub.id,
        status=sub.status,
        case_id=sub.case_id,
        release_version=sub.release_version,
    )
