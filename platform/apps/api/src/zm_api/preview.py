"""Preview (#42, S2-08): the draft in the client shell, as one role, before anyone publishes it.

`POST /owner/clients/{id}/preview-token` gives an owner a 15-minute read-only token
(`zm_api.context`: kind `preview`). Under it the `/app` reads answer from here instead of the
live release, Operaton and the client's cases:

- `draft_release` compiles the draft exactly as publish would (`publish._compile` and
  `publish.release_values`) into a release row that is never stored, so `GET /app/ui` and the
  forms are what users get after publish. A draft that can't be published can't be previewed (422).
- Cases and tasks are fixtures made from that release: per flow one case per stage (`MX-0001` in
  the first stage, `MX-0002` in the second, ...; flows sharing a code pattern share its
  numbers, so codes stay unique), open in its stage with the stages before it
  done, start values made up from the start form's field types, and the stage's tasks open.
  A task's id is its id in the draft.

No Operaton call and no read of a real case happens in preview.
"""

import copy
import hashlib
import json
from collections import Counter, OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from zm_api import auth, cases, publish, releases, tasks
from zm_api.context import ClientContext, OwnerCtx, Preview, owner_router
from zm_api.drafts import NO_DRAFT, OwnedClient
from zm_api.errors import FieldError, Forbidden, NotFound, UnprocessableContent
from zm_api.releases import ReleaseRow
from zm_compiler import level_groups
from zm_schema import group_id

# --- the draft as a release ------------------------------------------------------------------

_COMPILED: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
_COMPILED_MAX = 8  # drafts kept compiled, per API process


def _compiled(slug: str, draft: dict[str, Any], published: list[dict[str, Any]]) -> tuple:
    """`publish._compile`, remembered by the sha256 of its inputs: a preview reads the draft on
    every request, and it rarely changes between them."""
    key = hashlib.sha256(
        json.dumps([slug, draft, published], sort_keys=True, default=str).encode()
    ).hexdigest()
    found = _COMPILED.get(key)
    if found is None:
        found = _COMPILED[key] = publish._compile(slug, draft, published)
        while len(_COMPILED) > _COMPILED_MAX:
            _COMPILED.popitem(last=False)
    _COMPILED.move_to_end(key)
    return found


def build(
    slug: str,
    draft: dict[str, Any],
    published: list[dict[str, Any]] | None = None,
    active: ReleaseRow | None = None,
    version: int = 1,
) -> ReleaseRow:
    """The release publishing `draft` now would record as `version`; `active` itself when the
    draft hasn't changed since it (publish would make no new version)."""
    wf, compiled = _compiled(slug, draft, published or [])
    if active is not None and active.sha256 == compiled.digest():
        return active
    # a copy: the cached compile's forms and ui must not change with what a reader does to these
    values = copy.deepcopy(publish.release_values(wf, compiled, published or [], version))
    return ReleaseRow(
        version=version,
        deployment_id="preview",
        **values,
        published_by=None,
        published_at=datetime.now(UTC),
        active=False,
    )


async def draft_release(ctx: ClientContext) -> ReleaseRow:
    async with ctx.db.connect() as conn:
        draft = await releases.get_draft(conn)
        if draft is None:
            raise NotFound(NO_DRAFT)
        published = await releases.published_fields(conn)
        active = await releases.active_release(conn)
        version = await releases.next_version(conn)
    return build(ctx.client.slug, draft, published, active, version)


# --- fixtures (pure) -------------------------------------------------------------------------


def sample(field: Mapping[str, Any], n: int) -> Any:
    """A made-up value for a field of case number `n`, by its type; None for the types that need
    real data (a person, a file, ...), which then show as empty."""
    kind = field["type"]
    if kind in {"text", "textarea"}:
        return f"{field['label']} {n}"
    if kind in {"number", "currency", "percent", "rating"}:
        return n
    if kind == "date":
        return datetime.now(UTC).date().isoformat()
    if kind == "yes_no":
        return True
    options = field.get("options") or []
    if kind == "select" and options:
        return options[0]["id"]
    if kind == "multi_select" and options:
        return [options[0]["id"]]
    return None


def _values(workflow: Mapping[str, Any], fields: list[str], n: int) -> dict[str, Any]:
    defs = workflow.get("fields", {})
    found = {f: sample(defs[f], n) for f in fields if f in defs}
    return {f: v for f, v in found.items() if v is not None}


def _levels(who: Preview) -> dict[str, int]:
    return {who.department: who.level}


def _sees(
    workflow: Mapping[str, Any], flow: Mapping[str, Any], task: Mapping[str, Any], who: Preview
) -> Literal["mine", "team"] | None:
    """How the role meets a task: `mine` (they can do it), `team` (level 2+ reads their
    department's), or not at all. The candidate-group rule is the compiler's (`level_groups`);
    a step assigned from a field is `mine` for the department its picker lists
    (`tasks.pickable_department`), since any of them could be the one picked (#171).
    # ponytail: approval-chain steps aren't generated; add them when a preview has to show an
    # approver's inbox."""
    performer = task["performer"]
    me = group_id(who.department, who.level)
    if performer.get("initiator"):  # the role is the one who started the fixture case
        return "mine" if me in tasks.starter_groups(workflow, flow) else None
    if "from_field" in performer:
        picked = tasks.pickable_department(flow, performer["from_field"]) == who.department
        return "mine" if picked else None
    if performer.get("department") != who.department:
        return None
    groups = level_groups(
        tasks._org_levels(workflow),
        performer["department"],
        performer["level"],
        performer.get("or_above", False),
    )
    return "mine" if me in groups else "team" if who.level >= 2 else None


def _stage_tasks(stage: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [t for item in stage["tasks"] for t in item.get("parallel", [item])]


def _fixtures(
    release: ReleaseRow, who: Preview
) -> list[tuple[cases.CaseRow, list[cases.OpenTask], int]]:
    """Every fixture case with the tasks of its open stage that the role sees, and its sequence
    number (what its made-up values are numbered by)."""
    workflow, at = release.workflow, release.published_at
    out = []
    seqs: Counter[str] = Counter()  # as live: flows sharing a code pattern share its sequence
    for flow in workflow["flows"]:
        stages = flow["stages"]
        start = release.forms.get(f"start:{flow['key']}")
        pattern = flow["case_code"]["pattern"]
        for stage in stages:
            seqs[tasks.counter_key(pattern)] += 1
            n = seqs[tasks.counter_key(pattern)]
            code = tasks.case_code(pattern, n)
            done = {s["key"] for s in stages if stage["key"] in cases._after(stages, s["key"])}
            shown = cases.masked_start_values(
                start, _values(workflow, cases.start_fields(workflow, flow), n), _levels(who)
            )
            found = _stage_tasks(stage)
            seen = [(t, _sees(workflow, flow, t, who)) for t in found]
            row = cases.CaseRow(
                code=code,
                flow=flow["key"],
                title=cases.title(workflow, flow, shown) or code,
                status="open",
                stages=[
                    cases.Stage(
                        key=s["key"],
                        name=s["name"],
                        department=s["department"],
                        state="done" if s["key"] in done else "open" if s is stage else "queued",
                        started_at=at if s["key"] in done or s is stage else None,
                        ended_at=at if s["key"] in done else None,
                    )
                    for s in stages
                ],
                fields=shown,
                open_tasks=len(found),
                my_tasks=sum(how == "mine" for _, how in seen),
                created_at=at,
                release_version=release.version,
            )
            open_tasks = [
                cases.OpenTask(t["id"], t["name"], stage["key"], None, how == "mine")
                for t, how in seen
                if how
            ]
            out.append((row, open_tasks, n))
    return out


def list_cases(release: ReleaseRow, who: Preview, flow: str | None, status: str | None) -> list:
    if status not in (None, "open"):  # every fixture case is open
        return []
    return [row for row, _, _ in _fixtures(release, who) if flow in (None, row.flow)]


def get_case(release: ReleaseRow, who: Preview, code: str) -> cases.CaseDetail:
    found = next((f for f in _fixtures(release, who) if f[0].code == code), None)
    if found is None:
        raise NotFound("No such case")
    row, open_tasks, _ = found
    start = release.forms.get(f"start:{row.flow}")
    defs = release.workflow.get("fields", {})
    return cases.CaseDetail(
        row=row,
        sections=cases.sections(tasks.visible_form(start, _levels(who)), row.fields, defs, defs)
        if start
        else [],
        open_tasks=open_tasks,
        events=[cases.Event("created", row.created_at)],
    )


def _item(row: cases.CaseRow, task: cases.OpenTask) -> tasks.TaskItem:
    return tasks.TaskItem(
        id=task.id,
        name=task.name,
        flow=row.flow,
        case_code=row.code,
        stage=task.stage,
        created=row.created_at,
        assignee=None,
        form_key=f"zm:{task.id}",
    )


def list_tasks(
    release: ReleaseRow,
    who: Preview,
    scope: Literal["mine", "team"],
    flow: str | None,
    case: str | None,
) -> list[tasks.TaskItem]:
    if scope == "team" and who.level < 2:
        raise Forbidden("Team tasks are for level 2 and above")
    return [
        _item(row, t)
        for row, open_tasks, _ in _fixtures(release, who)
        if flow in (None, row.flow) and case in (None, row.code)
        for t in open_tasks
        if scope == "team" or t.can_act
    ]


def open_task(release: ReleaseRow, who: Preview, task_id: str) -> tuple[tasks.TaskView, dict]:
    """A fixture task as `tasks.open_task` gives a real one, and the values it shows (those of
    its own case). Never editable: a preview can't submit."""
    for row, open_tasks, n in _fixtures(release, who):
        task = next((t for t in open_tasks if t.id == task_id), None)
        if task is None:
            continue
        if task_id not in release.forms:
            raise NotFound("This task has no form in its release")
        form = tasks.visible_form(release.forms[task_id], _levels(who))
        case = tasks.Case(
            id=uuid5(NAMESPACE_URL, f"zm-preview:{row.code}"),
            code=row.code,
            flow=row.flow,
            release_version=release.version,
            process_instance_id="preview",
            created_at=row.created_at,
        )
        me = tasks.Person(UUID(int=0), _levels(who))
        hidden = tasks.hidden_fields(release.workflow, me.levels)
        shown = [f for f in form["schema"].get("x-zm-show", []) if f not in hidden]
        view = tasks.TaskView(_item(row, task), {}, case, release, me, False, task_id, form)
        return view, _values(release.workflow, shown, n)
    raise NotFound("No such task")


# --- API: POST /owner/clients/{client_id}/preview-token ----------------------------------------


class PreviewAs(BaseModel):
    department: str
    level: int


class PreviewIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: PreviewAs = Field(alias="as")
    """The role to see the draft as: one of the draft's departments and one of its levels."""


routes = owner_router()


@routes.post("/clients/{client_id}/preview-token")
async def preview_token(
    client_id: UUID, body: PreviewIn, ctx: OwnedClient, owner: OwnerCtx
) -> auth.TokenOut:
    """A 15-minute read-only token for the client app, showing the client's draft as one role
    with made-up cases; 422 when the draft can't be published yet or has no such role."""
    release = await draft_release(ctx)
    departments = {d["key"]: d for d in release.workflow["org"]["departments"]}
    department = departments.get(body.role.department)
    if department is None:
        error = FieldError(path="/as/department", message="The draft has no such department")
        raise UnprocessableContent("The draft has no such role", errors=[error])
    if body.role.level not in {lv["n"] for lv in department["levels"]}:
        error = FieldError(path="/as/level", message="The department has no such level")
        raise UnprocessableContent("The draft has no such role", errors=[error])
    label = f"{department['name']} L{body.role.level}"
    role = Preview(body.role.department, body.role.level, label)
    return auth.preview_token(owner.owner_id, ctx.client.slug, role)
