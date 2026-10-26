"""Cases, read side (#91, S1-26): the Sites list and a Site's page (D19).

Where a case is comes from Operaton's history of its main process, where the compiler sets
`case_status` (`open`, `closed:<stage>`, `completed`) and one `m_<stage>` per stage (`in progress`
when the stage's call activity starts, `done` when it ends; compile.py). The list reads those
variables for every case in one request. Operaton's terminate end also cancels any stage still
running in parallel, and that fires its end listener too (`done`), so a closed case reads its call
activities' history as well (`canceled`), one request per closed case; the detail always does,
for the times.

Visibility v1: every user of the client sees every case's header, status and stages; field
values hide what the user's level can't see (scope rules: #56).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import tasks
from zm_api.context import ClientContext
from zm_api.errors import NotFound
from zm_api.operaton import Json

StageState = Literal["queued", "open", "done", "closed"]
CaseStatus = Literal["open", "completed", "closed"]
MAX_CASES = 500  # shortcut: the newest 500; paging arrives with the Sites list block (#92)


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    department: str
    state: StageState
    started_at: str | None = None
    ended_at: str | None = None


# --- derivation (pure) -----------------------------------------------------------------------


def case_status(variables: Mapping[str, Any]) -> CaseStatus:
    status = str(variables.get("case_status") or "open")
    if status == "completed":
        return "completed"
    return "closed" if status.startswith("closed") else "open"


def _after(stages: list[Mapping[str, Any]], key: str) -> set[str]:
    """Every stage that runs after `key` (transitively through `after`)."""
    found: set[str] = set()
    todo = [key]
    while todo:
        k = todo.pop()
        for s in stages:
            if k in s.get("after", []) and s["key"] not in found:
                found.add(s["key"])
                todo.append(s["key"])
    return found


def stage_states(
    stages: list[Mapping[str, Any]],
    variables: Mapping[str, Any],
    activities: list[Json] | None = None,
) -> list[Stage]:
    """Each stage's state from the main process's history.

    From `m_<stage>`: none → queued, `in progress` → open, `done` → done. `case_status =
    closed:<k>` makes `k` closed and the stages after it queued. With the call activities'
    history (`activities`), cancelled ones are closed (a parallel stage the close ended) and the
    times are filled in."""
    status = str(variables.get("case_status") or "")
    closing = status.removeprefix("closed:") if status.startswith("closed:") else None
    later = _after(stages, closing) if closing else set()
    calls: dict[str, Json] = {}
    for a in activities or []:  # sorted by start: the last run of a stage wins
        calls[str(a.get("activityId", "")).removeprefix("call_")] = a
    out = []
    for s in stages:
        k = s["key"]
        a = calls.get(k)
        if activities is not None:
            if a is None:
                state: StageState = "queued"
            elif a.get("canceled"):
                state = "closed"
            else:
                state = "done" if a.get("endTime") else "open"
        else:
            m = variables.get(f"m_{k}")
            state = "queued" if m is None else "open" if m == "in progress" else "done"
        if k == closing:
            state = "closed"
        elif k in later:
            state = "queued"
        out.append(
            Stage(
                key=k,
                name=s["name"],
                department=s["department"],
                state=state,
                started_at=a.get("startTime") if a else None,
                ended_at=a.get("endTime") if a else None,
            )
        )
    return out


def start_fields(workflow: Mapping[str, Any], flow: Mapping[str, Any]) -> list[str]:
    """The start form's fields, in layout order."""
    form = workflow.get("forms", {}).get(flow["start"]["form"], {})
    out: list[str] = []
    for section in form.get("sections", []):
        for row in section["rows"]:
            out += [c["field"] for c in row if c["field"] not in out]
    return out


def title(workflow: Mapping[str, Any], flow: Mapping[str, Any], values: Mapping[str, Any]) -> str:
    """The case's title: its first text start field (e.g. `site_name`), else its code."""
    defs = workflow.get("fields", {})
    for name in start_fields(workflow, flow):
        if defs.get(name, {}).get("type") == "text" and values.get(name):
            return str(values[name])
    return ""


def task_name(workflow: Mapping[str, Any], task_def_key: str) -> str:
    """A task's display name; approval steps are named as the compiler names them."""
    base, _, approval = task_def_key.partition("__appr_")
    for flow in workflow["flows"]:
        for t in tasks._flow_tasks(flow):
            if t["id"] == base:
                if not approval:
                    return t["name"]
                dept, _, level = approval.rpartition("_L")
                level = level.split("_")[0]
                names = {d["key"]: d["name"] for d in workflow["org"]["departments"]}
                return f"{t['name']}: {names.get(dept, dept)} L{level} approval"
    return task_def_key


# --- reads -----------------------------------------------------------------------------------

_COLUMNS = "id, code, flow_key as flow, release_version, process_instance_id, created_at"


@dataclass
class CaseRow:
    code: str
    flow: str
    title: str
    status: CaseStatus
    stages: list[Stage]
    fields: dict[str, Any]
    open_tasks: int
    my_tasks: int
    created_at: datetime
    release_version: int


async def _start_values(conn: AsyncConnection, case_ids: Iterable[UUID]) -> dict[UUID, dict]:
    rows = await conn.execute(
        text(
            "select case_id, field, value from app.case_values"
            " where case_id = any(:ids) and submission_id is null order by id"
        ),
        {"ids": list(case_ids)},
    )
    out: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r.case_id, {})[r.field] = r.value
    return out


def _by_instance(found: list[Json]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for v in found:
        out.setdefault(v["processInstanceId"], {})[v["name"]] = v.get("value")
    return out


async def _task_counts(ctx: ClientContext, codes: list[str]) -> dict[str, int]:
    """Open tasks per case code: one task query for every case, mapped to cases through their
    process instances (TaskDto has no business key)."""
    if not codes:
        return {}
    found = await ctx.operaton.list_tasks({"processInstanceBusinessKeyIn": codes}, max_results=5000)
    counts: dict[str, int] = {}
    for item, _, _ in await tasks._items(ctx, found, None):
        counts[item.case_code] = counts.get(item.case_code, 0) + 1
    return counts


async def list_cases(
    ctx: ClientContext,
    user_id: UUID,
    flow: str | None = None,
    status: CaseStatus | None = None,
) -> list[CaseRow]:
    """Every case (newest first), with its stages, start values (masked by level) and tasks."""
    async with ctx.db.connect() as conn:
        me = await tasks.person(conn, user_id)
        rows = (
            (
                await conn.execute(
                    text(
                        f"select {_COLUMNS} from app.cases where (cast(:f as text) is null"
                        " or flow_key = :f) order by created_at desc, code desc limit :n"
                    ),
                    {"f": flow, "n": MAX_CASES},
                )
            )
            .mappings()
            .all()
        )
        cases = [tasks.Case(**r) for r in rows]
        workflows = await tasks._workflows(conn, {c.release_version for c in cases})
        values = await _start_values(conn, [c.id for c in cases])
    if not cases:
        return []
    names = {"case_status"} | {
        f"m_{s['key']}" for wf in workflows.values() for fl in wf["flows"] for s in fl["stages"]
    }
    history = _by_instance(
        await ctx.operaton.history_variables([c.process_instance_id for c in cases], sorted(names))
    )
    open_counts = await _task_counts(ctx, [c.code for c in cases])
    mine: dict[str, int] = {}
    for t in await tasks.list_tasks(ctx, user_id, "mine"):
        mine[t.case_code] = mine.get(t.case_code, 0) + 1

    out = []
    for c in cases:
        workflow = workflows[c.release_version]
        flow_def = tasks._flow(workflow, c.flow)
        if flow_def is None:
            continue
        variables = history.get(c.process_instance_id, {})
        st = case_status(variables)
        if status is not None and st != status:
            continue
        activities = (
            await ctx.operaton.history_activities(c.process_instance_id) if st == "closed" else None
        )
        hidden = tasks.hidden_fields(workflow, me.levels)
        shown = {k: v for k, v in values.get(c.id, {}).items() if k not in hidden}
        out.append(
            CaseRow(
                code=c.code,
                flow=c.flow,
                title=title(workflow, flow_def, shown) or c.code,
                status=st,
                stages=stage_states(flow_def["stages"], variables, activities),
                fields=shown,
                open_tasks=open_counts.get(c.code, 0),
                my_tasks=mine.get(c.code, 0),
                created_at=c.created_at,
                release_version=c.release_version,
            )
        )
    return out


@dataclass
class Section:
    key: str
    title: str
    fields: list[dict[str, Any]]
    """`{key, label, type, value}` per field, in layout order."""


@dataclass
class OpenTask:
    id: str
    name: str
    stage: str | None
    assignee: str | None
    can_act: bool


@dataclass
class Decision:
    task: str
    task_name: str
    by: UUID
    by_name: str | None
    outcome: str | None
    comment: str | None
    at: datetime


@dataclass
class CaseDetail:
    row: CaseRow
    sections: list[Section] = field(default_factory=list)
    open_tasks: list[OpenTask] = field(default_factory=list)
    history: list[Decision] = field(default_factory=list)


def sections(form: Mapping[str, Any], values: Mapping[str, Any]) -> list[Section]:
    """A form as the user sees it (`tasks.visible_form`), filled with the case's values."""
    props = form["schema"].get("properties", {})
    out = []
    for s in form["layout"]:
        cells = [cell["field"] for row in s["rows"] for cell in row if cell["field"] in props]
        out.append(
            Section(
                key=s["key"],
                title=s["title"],
                fields=[
                    {
                        "key": f,
                        "label": props[f].get("title", f),
                        "type": props[f].get("x-zm-type"),
                        "value": values.get(f),
                    }
                    for f in cells
                ],
            )
        )
    return out


async def get_case(ctx: ClientContext, user_id: UUID, code: str) -> CaseDetail:
    """One case: values by start-form section, stages with times, the open tasks the user can
    see, and its decisions. Another client's code doesn't exist here: 404, like an unknown one."""
    async with ctx.db.connect() as conn:
        me = await tasks.person(conn, user_id)
        case = (await tasks._cases_by_code(conn, [code])).get(code)
        if case is None:
            raise NotFound("No such case")
        release = (
            await conn.execute(
                text("select workflow, forms from app.releases where version = :v"),
                {"v": case.release_version},
            )
        ).one()
        values = (await _start_values(conn, [case.id])).get(case.id, {})
        decisions = (
            await conn.execute(
                text(
                    "select s.task_def_key, s.submitted_by, u.name, s.outcome, s.comment,"
                    " s.updated_at from app.submissions s left join app.users u"
                    " on u.id = s.submitted_by where s.case_id = :c and s.status = 'committed'"
                    " order by s.created_at, s.id"
                ),
                {"c": case.id},
            )
        ).all()
    workflow, forms = release.workflow, release.forms
    flow_def = tasks._flow(workflow, case.flow)
    if flow_def is None:
        raise NotFound("No such case")
    variables = _by_instance(
        await ctx.operaton.history_variables([case.process_instance_id], ["case_status"])
    ).get(case.process_instance_id, {})
    activities = await ctx.operaton.history_activities(case.process_instance_id)
    hidden = tasks.hidden_fields(workflow, me.levels)
    shown = {k: v for k, v in values.items() if k not in hidden}
    row = CaseRow(
        code=case.code,
        flow=case.flow,
        title=title(workflow, flow_def, shown) or case.code,
        status=case_status(variables),
        stages=stage_states(flow_def["stages"], variables, activities),
        fields=shown,
        open_tasks=0,
        my_tasks=0,
        created_at=case.created_at,
        release_version=case.release_version,
    )

    mine = await tasks.list_tasks(ctx, user_id, "mine", case=code)
    seen = {t.id: t for t in mine}
    if any(n >= 2 for n in me.levels.values()):
        for t in await tasks.list_tasks(ctx, user_id, "team", case=code):
            seen.setdefault(t.id, t)
    mine_ids = {t.id for t in mine}
    open_tasks = [
        OpenTask(t.id, t.name, t.stage, t.assignee, t.id in mine_ids) for t in seen.values()
    ]
    row.open_tasks = len(open_tasks)
    row.my_tasks = len(mine_ids)

    start = forms.get(f"start:{case.flow}")
    return CaseDetail(
        row=row,
        sections=sections(tasks.visible_form(start, me.levels), shown) if start else [],
        open_tasks=open_tasks,
        history=[
            Decision(
                task=d.task_def_key,
                task_name=task_name(workflow, d.task_def_key),
                by=d.submitted_by,
                by_name=d.name,
                outcome=d.outcome,
                comment=d.comment,
                at=d.updated_at,
            )
            for d in decisions
        ],
    )
