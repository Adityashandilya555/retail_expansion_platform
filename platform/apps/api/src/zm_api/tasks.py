"""Cases and tasks (#26, S1-10): start a case of any flow, list and open tasks, submit them.

Who may do what comes from the user's memberships, read on every call, mapped to the level groups
the compiler put on the tasks (`<dept>__L<n>`). Only routing variables reach Operaton (D5); every
value a person enters is stored in the client's database, so a long text never meets Operaton's
4000-character `TEXT_` column.

A submission is recorded `pending` before Operaton is called, `completed` once Operaton took it and
`committed` once its values are written. `failed` means Operaton refused it (the task is still
open, nothing changed there). If the process dies between completing and committing, the worker's
reconciler (`reconcile`) finishes the submission from Operaton's history, without completing the
task a second time.
"""

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import releases
from zm_api.context import ClientContext
from zm_api.errors import (
    ApiError,
    Conflict,
    FieldError,
    Forbidden,
    FormInvalid,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
)
from zm_api.operaton import Json, OperatonError
from zm_api.releases import ReleaseRow
from zm_compiler import level_groups
from zm_schema import group_id, submission_errors

_log = logging.getLogger("zm_api.tasks")

# Operaton keeps a String variable in TEXT_ varchar(4000) (activiti.postgres.create.engine.sql:222)
MAX_ENGINE_TEXT = 4000
_SEQ = re.compile(r"\{seq:([1-9])\}")
UNFINISHED = ("pending", "completed")


# --- who is asking -------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    id: UUID
    levels: dict[str, int]
    """Department → the user's level in it."""

    @property
    def groups(self) -> set[str]:
        return {group_id(d, n) for d, n in self.levels.items()}


async def person(conn: AsyncConnection, user_id: UUID) -> Person:
    """The signed-in user's levels; 401 once they're deactivated or gone."""
    rows = (
        await conn.execute(
            text(
                "select u.status, m.department, m.level from app.users u"
                " left join app.memberships m on m.user_id = u.id where u.id = :id"
            ),
            {"id": user_id},
        )
    ).all()
    if not rows or rows[0].status != "active":
        raise Unauthorized()
    return Person(user_id, {r.department: r.level for r in rows if r.department is not None})


def initiator_groups(groups: Iterable[str]) -> str:
    """`,g1,g2,`: the compiled `initiator_at_least` rules test `contains(',<group>,')`."""
    return "," + ",".join(sorted(groups)) + ","


# --- the release's workflow (as published, plain JSON) -----------------------------------------


def _org_levels(workflow: Mapping[str, Any]) -> dict[str, list[int]]:
    return {d["key"]: sorted(lv["n"] for lv in d["levels"]) for d in workflow["org"]["departments"]}


def starter_groups(workflow: Mapping[str, Any], flow: Mapping[str, Any]) -> set[str]:
    """The groups that may start `flow`: the compiler's own `candidateStarterGroups` rule."""
    levels = _org_levels(workflow)
    out: set[str] = set()
    for p in flow["start"]["performers"]:
        if "department" in p:
            out |= set(level_groups(levels, p["department"], p["level"], p.get("or_above", False)))
    return out


def _flow(workflow: Mapping[str, Any], flow_key: str) -> dict[str, Any] | None:
    return next((f for f in workflow["flows"] if f["key"] == flow_key), None)


def _flow_tasks(flow: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    for stage in flow["stages"]:
        for item in stage["tasks"]:
            yield from item.get("parallel", [item])


def flow_routing_names(routing_vars: Mapping[str, list[str]], flow: Mapping[str, Any]) -> set[str]:
    """Every routing variable a step of `flow` passes to Operaton. A case starts with each of
    them set to null, so a rule reading one that no form has filled yet sees null instead of
    failing, and a later form that leaves it out doesn't overwrite it."""
    ids = {t["id"] for t in _flow_tasks(flow)}
    names: set[str] = set()
    for key, found in routing_vars.items():
        if key.split("__appr_", 1)[0] in ids:
            names |= set(found)
    return names


def assignee_fields(flow: Mapping[str, Any]) -> set[str]:
    """`user` fields that assign a later step (`performer: {from_field}`): a form that shows one
    must fill it, or that step would have nobody."""
    return {
        t["performer"]["from_field"] for t in _flow_tasks(flow) if "from_field" in t["performer"]
    }


def stage_of(workflow: Mapping[str, Any], task_def_key: str) -> dict[str, Any] | None:
    """The stage holding a task (or one of its approval steps, `<task>__appr_<dept>_L<n>`)."""
    task_id = task_def_key.split("__appr_", 1)[0]
    for flow in workflow["flows"]:
        for stage in flow["stages"]:
            for item in stage["tasks"]:
                if any(t["id"] == task_id for t in item.get("parallel", [item])):
                    return stage
    return None


def counter_key(pattern: str) -> str:
    """`MX-{seq:4}` → `MX-{seq}`: widening a pattern keeps its sequence, so codes stay unique."""
    return _SEQ.sub("{seq}", pattern)


def case_code(pattern: str, seq: int) -> str:
    """`MX-{seq:4}` and 1 → `MX-0001`."""
    return _SEQ.sub(lambda m: str(seq).zfill(int(m.group(1))), pattern)


def visible_form(form: Mapping[str, Any], levels: Mapping[str, int]) -> dict[str, Any]:
    """The form without the sections hidden below the user's level in their department, removed
    from both the layout and the schema; a value for a removed field is then "Not a field of this
    form" (422). A field also placed in a visible section stays."""
    layout = form["layout"]

    def hidden(section: Mapping[str, Any]) -> bool:
        return _hidden(section, levels)

    shown = [s for s in layout if not hidden(s)]
    if len(shown) == len(layout):
        return {"schema": form["schema"], "layout": layout}

    def fields(sections: list[Mapping[str, Any]]) -> set[str]:
        return {cell["field"] for s in sections for row in s["rows"] for cell in row}

    drop = fields([s for s in layout if hidden(s)]) - fields(shown)
    schema = dict(form["schema"])
    schema["properties"] = {k: v for k, v in schema["properties"].items() if k not in drop}
    schema["required"] = [k for k in schema.get("required", []) if k not in drop]
    schema["x-zm-layout"] = shown
    return {"schema": schema, "layout": shown}


def _hidden(section: Mapping[str, Any], levels: Mapping[str, int]) -> bool:
    rule = section.get("hidden_below_level")
    return bool(rule) and levels.get(rule["department"], 0) < rule["level"]


def hidden_fields(workflow: Mapping[str, Any], levels: Mapping[str, int]) -> set[str]:
    """Fields the user never sees in any form (every section placing them is hidden from their
    level): kept out of a task's `show` too."""
    placed: set[str] = set()
    shown: set[str] = set()
    for form in workflow.get("forms", {}).values():
        for section in form["sections"]:
            fields = {cell["field"] for row in section["rows"] for cell in row}
            placed |= fields
            if not _hidden(section, levels):
                shown |= fields
    return placed - shown


def field_labels(workflow: Mapping[str, Any], fields: Iterable[str]) -> dict[str, str]:
    """Each field's label in the release (a task's `show` panel, #92); its key when it has none."""
    known = workflow.get("fields", {})
    return {f: known.get(f, {}).get("label") or f for f in fields}


def _validate(
    form: Mapping[str, Any], values: Mapping[str, Any], assignees: Iterable[str] = ()
) -> None:
    errors = submission_errors(form["schema"], dict(values))
    paths = {e["path"] for e in errors}
    for field in sorted(assignees):
        empty = values.get(field) in (None, "")
        if field in form["schema"].get("properties", {}) and empty and f"/{field}" not in paths:
            errors.append({"path": f"/{field}", "message": "Required: it assigns a later step"})
    if errors:
        errors.sort(key=lambda e: e["path"])
        raise FormInvalid([FieldError(**e) for e in errors])


def routing_values(names: Iterable[str], values: Mapping[str, Any]) -> dict[str, Any]:
    """The routing variables (D5) present in `values`; a name left out is not sent, so it keeps
    what an earlier form set (every one starts as null with the case). A list or object, and a
    string longer than Operaton's text column, go as null; the full value stays with us."""
    out: dict[str, Any] = {}
    for name in names:
        if name not in values:
            continue
        value = values[name]
        if isinstance(value, dict | list) or (
            isinstance(value, str) and len(value) > MAX_ENGINE_TEXT
        ):
            value = None
        out[name] = value
    return out


# --- cases -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: UUID
    code: str
    flow: str
    release_version: int
    process_instance_id: str
    created_at: datetime


_CASE_COLUMNS = "id, code, flow_key as flow, release_version, process_instance_id, created_at"


async def _next_seq(ctx: ClientContext, pattern: str) -> int:
    """Next number for a case-code pattern; per pattern, so flows sharing one never collide."""
    async with ctx.db.begin() as conn:
        return (
            await conn.execute(
                text(
                    "insert into app.case_counters (pattern, last) values (:p, 1)"
                    " on conflict (pattern) do update set last = app.case_counters.last + 1"
                    " returning last"
                ),
                {"p": pattern},
            )
        ).scalar_one()


async def _active(conn: AsyncConnection) -> ReleaseRow:
    release = await releases.active_release(conn)
    if release is None:
        raise Conflict("Nothing has been published for this workspace yet")
    return release


async def _insert_values(
    conn: AsyncConnection,
    case_id: UUID,
    values: Mapping[str, Any],
    release_version: int,
    submission_id: UUID | None,
) -> None:
    if not values:
        return
    await conn.execute(
        text(
            "insert into app.case_values (case_id, field, value, release_version, submission_id)"
            " values (:c, :f, :v, :r, :s) on conflict (submission_id, field) do nothing"
        ).bindparams(bindparam("v", type_=JSONB)),
        [
            {"c": case_id, "f": f, "v": v, "r": release_version, "s": submission_id}
            for f, v in values.items()
        ],
    )


async def start_case(
    ctx: ClientContext, user_id: UUID, flow_key: str, values: Mapping[str, Any]
) -> Case:
    """Start a case of `flow_key` on the active release, as its initiator."""
    async with ctx.db.connect() as conn:
        me = await person(conn, user_id)
        release = await _active(conn)
    flow = _flow(release.workflow, flow_key)
    if flow is None or flow_key not in release.process_keys:
        raise NotFound("No such flow")
    if not starter_groups(release.workflow, flow) & me.groups:
        raise Forbidden("You can't start this flow")
    key = f"start:{flow_key}"
    _validate(visible_form(release.forms[key], me.levels), values, assignee_fields(flow))

    pattern = flow["case_code"]["pattern"]
    code = case_code(pattern, await _next_seq(ctx, counter_key(pattern)))
    engine_values = {
        **dict.fromkeys(flow_routing_names(release.routing_vars, flow)),
        **routing_values(release.routing_vars.get(key, []), values),
        # set after Operaton's own `initiator` (the service account): ours wins (#26 plan)
        "initiator": str(user_id),
        "initiator_groups": initiator_groups(me.groups),
    }
    # the active release's own definition, not the latest by key: a deploy whose release
    # wasn't recorded (a publish that failed halfway) must not get cases
    definition = await ctx.operaton.definition_in(
        release.deployment_id, release.process_keys[flow_key]
    )
    try:
        instance = await ctx.operaton.start(
            definition["id"], business_key=code, values=engine_values
        )
    except OperatonError as e:
        if refused(e):
            raise
        # no clear answer: the engine may have started it anyway; find it by its code
        found = await ctx.operaton.find_instances(
            {"businessKey": code, "processDefinitionId": definition["id"]}
        )
        if not found:
            raise ServiceUnavailable(
                "The process engine didn't answer; start the case again"
            ) from e
        instance = found[0]
    try:
        async with ctx.db.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "insert into app.cases (flow_key, code, release_version,"
                            " process_instance_id, created_by) values (:f, :c, :r, :p, :u)"
                            f" returning {_CASE_COLUMNS}"
                        ),
                        {
                            "f": flow_key,
                            "c": code,
                            "r": release.version,
                            "p": instance["id"],
                            "u": user_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            await _insert_values(conn, row["id"], values, release.version, None)
    except (SQLAlchemyError, OSError) as e:
        try:  # don't leave a running case nobody can see
            await ctx.operaton.delete_process_instance(instance["id"])
        except ApiError:
            _log.exception(
                "orphan process instance %s for case %s",
                instance["id"],
                code,
                extra={"client": ctx.client.slug},
            )
        raise ServiceUnavailable("Couldn't record the case; start it again") from e
    return Case(**row)


async def _cases_by_code(conn: AsyncConnection, codes: Iterable[str]) -> dict[str, Case]:
    rows = await conn.execute(
        text(f"select {_CASE_COLUMNS} from app.cases where code = any(:codes)"),
        {"codes": sorted(set(codes))},
    )
    return {r["code"]: Case(**r) for r in rows.mappings()}


async def _workflows(conn: AsyncConnection, versions: Iterable[int]) -> dict[int, dict[str, Any]]:
    rows = await conn.execute(
        text("select version, workflow from app.releases where version = any(:v)"),
        {"v": sorted(set(versions))},
    )
    return {r.version: r.workflow for r in rows}


# --- tasks -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskItem:
    id: str
    name: str
    flow: str
    case_code: str
    stage: str | None
    created: str
    assignee: str | None
    form_key: str | None


def _form_key(task: Mapping[str, Any]) -> str:
    """The release's form key: the compiler sets `zm:<task id>` on every user task."""
    return (task.get("formKey") or "").removeprefix("zm:") or task["taskDefinitionKey"]


async def _items(
    ctx: ClientContext, tasks: list[Json], flow: str | None
) -> list[tuple[TaskItem, Case, dict[str, Any] | None]]:
    """Tasks → (item, case, stage), in one Operaton call and two queries whatever the count.
    `TaskDto` has no business key, so it comes from the tasks' process instances (stage processes
    get it from their call activity). Tasks of no case of ours are left out."""
    instances = await ctx.operaton.process_instances(
        sorted({t["processInstanceId"] for t in tasks if t.get("processInstanceId")})
    )
    business = {i["id"]: i.get("businessKey") for i in instances}
    async with ctx.db.connect() as conn:
        cases = await _cases_by_code(conn, [k for k in business.values() if k])
        workflows = await _workflows(conn, [c.release_version for c in cases.values()])
    out = []
    for t in tasks:
        case = cases.get(business.get(t.get("processInstanceId")) or "")
        if case is None or (flow is not None and case.flow != flow):
            continue
        workflow = workflows.get(case.release_version)
        stage = stage_of(workflow, t["taskDefinitionKey"]) if workflow else None
        item = TaskItem(
            id=t["id"],
            name=t.get("name") or t["taskDefinitionKey"],
            flow=case.flow,
            case_code=case.code,
            stage=stage["key"] if stage else None,
            created=t["created"],
            assignee=t.get("assignee"),
            form_key=t.get("formKey"),
        )
        out.append((item, case, stage))
    return out


def team_groups(workflow: Mapping[str, Any] | None, me: Person) -> set[str]:
    """Every level group of each department where the user is level 2 or above."""
    levels = _org_levels(workflow) if workflow else {}
    out: set[str] = set()
    for dept, level in me.levels.items():
        if level >= 2:
            found = level_groups(levels, dept, 1, or_above=True)
            out |= set(found) or {group_id(dept, n) for n in range(1, level + 1)}
    return out


async def list_tasks(
    ctx: ClientContext,
    user_id: UUID,
    scope: Literal["mine", "team"] = "mine",
    flow: str | None = None,
    case: str | None = None,
) -> list[TaskItem]:
    """`mine`: assigned to the user, or unassigned and offered to one of their groups. `team`
    (level 2 and above): every task of their departments' groups, assigned or not."""
    async with ctx.db.connect() as conn:
        me = await person(conn, user_id)
        if scope == "team":
            if not any(n >= 2 for n in me.levels.values()):
                raise Forbidden("Team tasks are for level 2 and above")
            release = await releases.active_release(conn)
    queries: list[dict[str, Any]] = []
    if scope == "team":
        groups = team_groups(release.workflow if release else None, me)
        queries.append({"candidateGroups": sorted(groups), "includeAssignedTasks": True})
    else:
        queries.append({"assignee": str(user_id)})
        if me.groups:  # an empty candidateGroups list is an error in Operaton
            queries.append({"candidateGroups": sorted(me.groups), "unassigned": True})
    for q in queries:
        if case is not None:
            q["processInstanceBusinessKey"] = case
        q["sorting"] = [{"sortBy": "created", "sortOrder": "asc"}]
    found: dict[str, Json] = {}
    for result in await asyncio.gather(*(ctx.operaton.all_tasks(q) for q in queries)):
        for t in result:
            found.setdefault(t["id"], t)
    ordered = sorted(found.values(), key=lambda t: (t["created"], t["id"]))
    return [item for item, _, _ in await _items(ctx, ordered, flow)]


@dataclass(frozen=True)
class TaskView:
    item: TaskItem
    task: Json
    case: Case
    release: ReleaseRow
    me: Person
    editable: bool
    form_key: str
    form: dict[str, Any]
    """The form as this user sees it: `{schema, layout}`."""


def may_open(
    me: Person, task: Mapping[str, Any], candidates: set[str], stage_department: str | None
) -> bool:
    """True if the user can submit the task, False if they can only read it; NotFound (never
    403, so ids don't leak) if they may not see it at all."""
    assignee = task.get("assignee")
    if assignee == str(me.id):
        return True
    if candidates & me.groups:
        return assignee is None
    departments = {g.partition("__L")[0] for g in candidates}
    if stage_department:
        departments.add(stage_department)
    if any(me.levels.get(d, 0) >= 2 for d in departments):
        return False
    raise NotFound("No such task")


async def open_task(ctx: ClientContext, user_id: UUID, task_id: str) -> TaskView:
    task = await ctx.operaton.get_task(task_id)  # another client's id doesn't exist here: 404
    async with ctx.db.connect() as conn:
        me = await person(conn, user_id)
    found = await _items(ctx, [task], None)
    if not found:
        raise NotFound("No such task")
    item, case, stage = found[0]
    candidates: set[str] = set()
    if task.get("assignee") != str(user_id):
        links = await ctx.operaton.identity_links(task_id)
        candidates = {link["groupId"] for link in links if link.get("groupId")}
    editable = may_open(me, task, candidates, stage["department"] if stage else None)
    release = await releases.release_for_task(ctx, task["processDefinitionId"])
    key = _form_key(task)
    if key not in release.forms:
        raise NotFound("This task has no form in its release")
    form = visible_form(release.forms[key], me.levels)
    return TaskView(item, task, case, release, me, editable, key, form)


async def shown_values(ctx: ClientContext, case_id: UUID, fields: list[str]) -> dict[str, Any]:
    """The latest value of each of `fields` recorded for the case (a task's read-only `show`)."""
    if not fields:
        return {}
    async with ctx.db.connect() as conn:
        rows = await conn.execute(
            text(
                "select distinct on (field) field, value from app.case_values"
                " where case_id = :c and field = any(:f) order by field, at desc, id desc"
            ),
            {"c": case_id, "f": fields},
        )
        return {r.field: r.value for r in rows}


# --- submissions -----------------------------------------------------------------------------


Status = Literal["pending", "completed", "committed", "failed"]


@dataclass(frozen=True)
class Submission:
    id: UUID
    case_id: UUID
    operaton_task_id: str
    form_key: str
    release_version: int
    values: dict[str, Any]
    outcome: str | None
    comment: str | None
    status: Status
    attempts: int
    submitted_by: UUID
    idempotency_key: str

    def engine_values(self, names: Iterable[str]) -> dict[str, Any]:
        merged = dict(self.values)
        if self.outcome is not None:
            merged[f"{self.form_key}_outcome"] = self.outcome
        if self.comment is not None:
            merged[f"{self.form_key}_comment"] = self.comment
        return routing_values(names, merged)


_SUB_COLUMNS = (
    "id, case_id, operaton_task_id, form_key, release_version, values, outcome, comment, status,"
    " attempts, submitted_by, idempotency_key"
)


async def _submission(conn: AsyncConnection, where: str, value: Any) -> Submission | None:
    row = (
        (
            await conn.execute(
                text(f"select {_SUB_COLUMNS} from app.submissions where {where}"), {"v": value}
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else Submission(**row)


async def _set_status(
    ctx: ClientContext, submission_id: UUID, status: Status, error: str | None = None
) -> None:
    async with ctx.db.begin() as conn:
        await conn.execute(
            text(
                "update app.submissions set status = :s, last_error = coalesce(:e, last_error),"
                " updated_at = now() where id = :id"
            ),
            {"s": status, "e": error, "id": submission_id},
        )


async def commit(ctx: ClientContext, submission_id: UUID) -> Submission:
    """Write a completed submission's values to the case and mark it committed. Safe to repeat:
    values are unique per (submission, field)."""
    async with ctx.db.begin() as conn:
        sub = await _submission(conn, "id = :v for update", submission_id)
        assert sub is not None
        if sub.status != "committed":
            await _insert_values(conn, sub.case_id, sub.values, sub.release_version, sub.id)
            await conn.execute(
                text(
                    "update app.submissions set status = 'committed', updated_at = now()"
                    " where id = :id"
                ),
                {"id": sub.id},
            )
        return await _submission(conn, "id = :v", submission_id)  # type: ignore[return-value]


def _same_request(prior: Submission, user_id: UUID, task_id: str) -> Submission:
    if prior.submitted_by != user_id or prior.operaton_task_id != task_id:
        raise Conflict("This Idempotency-Key was used for another submission")
    if prior.status == "failed":
        raise Conflict("This submission failed; submit again with a new Idempotency-Key")
    return prior


async def submit(
    ctx: ClientContext,
    user_id: UUID,
    task_id: str,
    values: Mapping[str, Any],
    *,
    outcome: str | None = None,
    comment: str | None = None,
    idempotency_key: str,
) -> Submission:
    """Validate, record `pending`, claim if unassigned, complete with the routing variables
    (`completed`), write the values (`committed`). The same key again returns the first result."""
    async with ctx.db.connect() as conn:
        prior = await _submission(conn, "idempotency_key = :v", idempotency_key)
    if prior is not None:
        return _same_request(prior, user_id, task_id)

    view = await open_task(ctx, user_id, task_id)
    if not view.editable:
        raise NotFound("No such task")
    key = view.form_key
    merged = dict(values)
    if outcome is not None:
        merged[f"{key}_outcome"] = outcome
    if comment is not None:
        merged[f"{key}_comment"] = comment
    flow = _flow(view.release.workflow, view.case.flow)
    _validate(view.form, merged, assignee_fields(flow) if flow else ())
    outcome = merged.pop(f"{key}_outcome", None)
    comment = merged.pop(f"{key}_comment", None)

    try:
        async with ctx.db.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "insert into app.submissions (case_id, task_def_key, operaton_task_id,"
                            " form_key, release_version, values, outcome, comment, submitted_by,"
                            " idempotency_key) values (:case, :tdk, :tid, :fk, :rv, :vals, :o, :c,"
                            f" :by, :key) returning {_SUB_COLUMNS}"
                        ).bindparams(bindparam("vals", type_=JSONB)),
                        {
                            "case": view.case.id,
                            "tdk": view.task["taskDefinitionKey"],
                            "tid": task_id,
                            "fk": key,
                            "rv": view.release.version,
                            "vals": merged,
                            "o": outcome,
                            "c": comment,
                            "by": user_id,
                            "key": idempotency_key,
                        },
                    )
                )
                .mappings()
                .one()
            )
    except IntegrityError:
        async with ctx.db.connect() as conn:
            prior = await _submission(conn, "idempotency_key = :v", idempotency_key)
        if prior is not None:  # the same key raced us in: theirs is the first result
            return _same_request(prior, user_id, task_id)
        # one live submission per task (submissions_one_per_task)
        raise Conflict("This task already has a submission being processed") from None
    sub = Submission(**row)

    claimed = False
    try:
        if view.task.get("assignee") is None:
            await ctx.operaton.claim(task_id, str(user_id))
            claimed = True
        await ctx.operaton.complete(
            task_id, sub.engine_values(view.release.routing_vars.get(key, []))
        )
    except ApiError as e:
        if claimed and refused(e):  # give the task back to its group, as it was
            try:
                await ctx.operaton.unclaim(task_id)
            except ApiError:
                # the submission's own id, not the task id from the URL (no log injection)
                _log.warning(
                    "couldn't unclaim the task of submission %s",
                    sub.id,
                    extra={"client": ctx.client.slug},
                )
        if not refused(e):
            # no clear answer (timeout, engine error): Operaton may have completed it, so the
            # submission stays pending and the reconciler settles it from the task's history
            await _set_status(ctx, sub.id, "pending", _reason(e))
            raise ServiceUnavailable(
                "The process engine didn't answer; your submission is saved and will finish shortly"
            ) from e
        await _set_status(ctx, sub.id, "failed", _reason(e))
        if _claimed(e):
            raise Conflict("Someone else has taken this task") from e
        raise
    await _set_status(ctx, sub.id, "completed")
    return await commit(ctx, sub.id)


def _claimed(e: ApiError) -> bool:
    return isinstance(e, OperatonError) and e.upstream_type == "TaskAlreadyClaimedException"


def refused(e: ApiError) -> bool:
    """True when Operaton clearly didn't take the call: the task is gone, or Operaton answered
    with its own error, which it sends after rolling the command back (TaskResourceImpl.java:
    130-132): rejected values, a failing rule, someone else's claim. A timeout, an unreachable
    engine or a non-JSON answer (a proxy's 502) is no clear answer: it may have happened."""
    if isinstance(e, OperatonError):
        return e.upstream_type is not None or e.upstream_status == 400
    return True


def _reason(e: ApiError) -> str:
    kind = getattr(e, "upstream_type", None)
    return f"{type(e).__name__}{f' ({kind})' if kind else ''}: {e.detail}"


# --- reconciler (run by the worker) ----------------------------------------------------------


async def _routing_names(ctx: ClientContext, sub: Submission) -> list[str]:
    async with ctx.db.connect() as conn:
        names = (
            await conn.execute(
                text("select routing_vars -> :k from app.releases where version = :v"),
                {"k": sub.form_key, "v": sub.release_version},
            )
        ).scalar_one_or_none()
    return list(names or [])


async def _bump_attempts(ctx: ClientContext, submission_id: UUID) -> None:
    async with ctx.db.begin() as conn:
        await conn.execute(
            text(
                "update app.submissions set attempts = attempts + 1, updated_at = now()"
                " where id = :id"
            ),
            {"id": submission_id},
        )


async def _fail(ctx: ClientContext, sub: Submission, reason: str) -> str:
    await _set_status(ctx, sub.id, "failed", reason)
    _log.error("submission %s failed: %s", sub.id, reason, extra={"client": ctx.client.slug})
    return "failed"


async def _reconcile_one(ctx: ClientContext, sub: Submission) -> str:
    """Settle one stuck submission. Only one live submission exists per task
    (submissions_one_per_task), so a completed task with this one still live was completed by
    it."""
    if sub.status == "completed":  # Operaton took it: only the values are missing
        await commit(ctx, sub.id)
        return "committed"
    try:
        task: Json | None = await ctx.operaton.get_task(sub.operaton_task_id)
    except NotFound:
        task = None
    if task is None:
        history = await ctx.operaton.history_task(sub.operaton_task_id)
        if history and history[0].get("endTime") and history[0].get("deleteReason") == "completed":
            await commit(ctx, sub.id)  # Operaton has it: write the values, don't complete again
            return "committed"
        return await _fail(ctx, sub, "the task ended without this submission")
    if sub.attempts >= 1:
        return await _fail(ctx, sub, "the task is still open after a retried completion")
    assignee = task.get("assignee")
    if assignee is not None and assignee != str(sub.submitted_by):
        return await _fail(ctx, sub, "someone else holds the task now")
    await _bump_attempts(ctx, sub.id)
    try:
        if assignee is None:
            await ctx.operaton.claim(sub.operaton_task_id, str(sub.submitted_by))
        await ctx.operaton.complete(
            sub.operaton_task_id, sub.engine_values(await _routing_names(ctx, sub))
        )
    except ApiError as e:
        if refused(e):
            return await _fail(ctx, sub, f"retry refused: {_reason(e)}")
        return "retried"  # no clear answer again: the next pass reads the task's history
    await _set_status(ctx, sub.id, "completed")
    await commit(ctx, sub.id)
    return "committed"


async def reconcile(
    ctx: ClientContext, older_than: float = 120.0, limit: int = 100
) -> Counter[str]:
    """Finish submissions stuck in `pending`/`completed` for more than `older_than` seconds.
    Each is handled under an advisory lock, so overlapping runs skip it."""
    counts: Counter[str] = Counter()
    async with ctx.db.connect() as conn:
        ids = (
            (
                await conn.execute(
                    text(
                        "select id from app.submissions where status in ('pending', 'completed')"
                        " and updated_at < now() - make_interval(secs => :age)"
                        " order by updated_at limit :n"
                    ),
                    {"age": older_than, "n": limit},
                )
            )
            .scalars()
            .all()
        )
    for submission_id in ids:
        async with ctx.db.connect() as lock:
            k = f"zm_reconcile:{submission_id}"
            got = (
                await lock.execute(text("select pg_try_advisory_lock(hashtext(:k))"), {"k": k})
            ).scalar_one()
            await lock.commit()
            if not got:
                counts["skipped"] += 1
                continue
            try:
                sub = await _submission(lock, "id = :v", submission_id)
                await lock.commit()
                if sub is None or sub.status not in UNFINISHED:
                    counts["skipped"] += 1
                else:
                    counts[await _reconcile_one(ctx, sub)] += 1
            except Exception:  # one bad row (or engine hiccup) mustn't stall the rest
                _log.exception(
                    "reconciling submission %s failed",
                    submission_id,
                    extra={"client": ctx.client.slug},
                )
                counts["error"] += 1
            finally:
                await lock.rollback()  # the advisory lock is per session: it survives this
                await lock.execute(text("select pg_advisory_unlock(hashtext(:k))"), {"k": k})
                await lock.commit()
    return counts
