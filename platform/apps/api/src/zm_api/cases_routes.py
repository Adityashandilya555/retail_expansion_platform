"""`GET /app/cases` and `GET /app/cases/{code}` (#91): the HTTP side of `zm_api.cases`."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from zm_api import cases
from zm_api.context import ClientCtx, UserId, client_router

routes = client_router()


class _Model(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)


class StageOut(_Model):
    key: str
    name: str
    department: str
    state: Literal["queued", "open", "done", "closed"]
    """`closed`: the case was closed in this stage, or the close cancelled it while it ran."""
    started_at: str | None
    ended_at: str | None


class CaseRow(_Model):
    code: str
    flow: str
    title: str
    """The case's first text start field (e.g. the site's name), else its code."""
    status: Literal["open", "completed", "closed"]
    stages: list[StageOut]
    fields: dict[str, Any]
    """Start-form values, without fields hidden from the user's level."""
    open_tasks: int
    my_tasks: int
    created_at: datetime
    release_version: int


class FieldOut(_Model):
    key: str
    label: str
    type: str | None
    value: Any


class SectionOut(_Model):
    key: str
    title: str
    fields: list[FieldOut]


class OpenTaskOut(_Model):
    id: str
    name: str
    stage: str | None
    assignee: str | None
    can_act: bool
    """The user may submit it (else they see it as their department's lead)."""


class DecisionOut(_Model):
    task: str
    task_name: str
    by: UUID
    by_name: str | None
    outcome: str | None
    comment: str | None
    at: datetime


class CaseDetail(CaseRow):
    sections: list[SectionOut]
    """The start form as the user sees it, filled with the case's values."""
    tasks: list[OpenTaskOut]
    history: list[DecisionOut]
    """Committed submissions, oldest first."""


def _row(row: cases.CaseRow) -> dict[str, Any]:
    return {**row.__dict__, "stages": [s.__dict__ for s in row.stages]}


@routes.get("/cases")
async def list_cases(
    ctx: ClientCtx,
    uid: UserId,
    flow: str | None = None,
    status: Literal["open", "completed", "closed"] | None = None,
) -> list[CaseRow]:
    """Every case of the client (newest first, up to 500) with its stage states."""
    return [CaseRow(**_row(r)) for r in await cases.list_cases(ctx, uid, flow, status)]


@routes.get("/cases/{code}")
async def get_case(code: str, ctx: ClientCtx, uid: UserId) -> CaseDetail:
    """One case: its stages with times, values by section, open tasks and decisions."""
    detail = await cases.get_case(ctx, uid, code)
    return CaseDetail(
        **_row(detail.row),
        sections=[s.__dict__ for s in detail.sections],
        tasks=[t.__dict__ for t in detail.open_tasks],
        history=[d.__dict__ for d in detail.history],
    )
