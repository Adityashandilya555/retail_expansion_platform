"""The agent's tools v1 (#36, S2-04a; blueprint §10): read, org, flow, forms and checks.

Each tool is a Pydantic model of its arguments, sent to Claude as a strict JSON schema (no extra
properties, every property required, optional ones nullable), and a handler. An edit's handler is
a pure function from the draft to an RFC 6902 patch: `drafts.apply_change` builds it from the
locked draft, validates the result and logs it under the tool's name, with undo. A read answers
from the draft. No tool publishes, migrates, manages people or takes a client: the caller passes
the ClientContext of the console's client. `call` answers `{ok, summary, errors?}`, plus `data`
for a read; an edit's `summary` is its change chip.
"""

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, get_args
from uuid import UUID

import jsonpatch
from jsonpointer import JsonPointer
from pydantic import BaseModel, ConfigDict, ValidationError

from zm_api import drafts, releases, templates
from zm_api.context import ClientContext
from zm_api.errors import ApiError, Conflict, NotFound, UnprocessableContent
from zm_schema.errors import loc_to_path
from zm_schema.models import FieldType

Doc = dict[str, Any]
Patch = drafts.Patch
Result = dict[str, Any]

# The field types @zm/forms draws (`Field` in packages/zm-forms/src/ZmForm.tsx); the rest of
# zm_schema's FieldType are valid in a draft but show as "unsupported in v1". A test keeps the two
# in step.
V1_RENDERS = frozenset(
    {"text", "textarea", "number", "currency", "date", "select", "yes_no", "document"}
)


# --- arguments: strict schemas (null = "none" or "leave as is", as each says) --------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class NoArgs(_Args):
    pass


class TaskRef(_Args):
    task_id: str


class FormRef(_Args):
    form: str
    """Form key, as a task's `form` names it."""


class TemplateRef(_Args):
    template: str
    """Template key from list_templates."""


class NewDepartment(_Args):
    key: str
    """Short key, e.g. `legal`; it names the Operaton groups (`legal__L1`), so it never changes."""
    name: str
    levels: list[str]
    """Level names, L1 (most junior) first."""


class RenameDepartment(_Args):
    department: str
    name: str


class Levels(_Args):
    department: str
    levels: list[str]
    """Every level's name, L1 (most junior) first; replaces the ladder."""


class LevelPerformer(_Args):
    department: str
    level: int
    or_above: bool | None
    """Higher levels of the department may do it too; null = no."""


class InitiatorPerformer(_Args):
    initiator: Literal[True]
    """The person who started the case."""


class FieldPerformer(_Args):
    from_field: str
    """A user-type field filled earlier in the case."""


Performer = LevelPerformer | InitiatorPerformer | FieldPerformer


class NewTask(_Args):
    id: str
    """Unique in the draft: a letter, then letters, digits or `_`."""
    name: str
    performer: Performer
    form: str | None
    """Key of the form it fills; null = none."""
    show: list[str] | None
    """Fields filled by earlier steps, shown read-only; null = none."""


class NewStage(_Args):
    flow: str | None
    """Flow key; null = the first flow."""
    key: str
    name: str
    department: str
    after: list[str]
    """Stage keys that must finish first; empty = it starts with the case."""
    tasks: list[NewTask]
    """At least one, in order."""


class StageAfter(_Args):
    flow: str | None
    """Flow key; null = the first flow."""
    stage: str
    after: list[str]
    """Stage keys that must all finish first (several = they run in parallel); empty = none."""


class TaskIn(_Args):
    flow: str | None
    """Flow key; null = the first flow."""
    stage: str
    task: NewTask
    before: str | None
    """Insert before this task of the stage; null = at its end."""


class TaskChanges(_Args):
    task_id: str
    name: str | None
    performer: Performer | None
    form: str | None
    show: list[str] | None


class TaskMove(_Args):
    task_id: str
    flow: str | None
    """Flow key; null = the first flow."""
    stage: str
    before: str | None
    """Put it before this task of the stage; null = at its end."""


class OutcomeIn(_Args):
    id: str
    label: str
    to: str | None
    """A task of the same stage, `@close` (ends the case) or `@end`; null = the next step."""
    comment_required: bool | None


class Outcomes(_Args):
    task_id: str
    outcomes: list[OutcomeIn]
    """The task's decision buttons, replacing any it has; empty = a plain submit."""


class DeptLevel(_Args):
    department: str
    level: int


class ApprovalChain(_Args):
    task_id: str
    chain: list[DeptLevel]
    """Approvers in order; empty removes the task's approval."""
    send_back_to: str | None
    """A task of the same stage an approver can send it back to; null = none."""
    allow_reject: bool | None
    """Null = yes."""


class DocumentSlot(_Args):
    task_id: str
    """The task whose form gets the slot (the task needs a form)."""
    key: str
    """New field key: lowercase letters, digits and `_`, 2 to 41 characters."""
    label: str
    accept: list[str]
    """File extensions, e.g. `["pdf", "jpg"]`."""
    max_mb: int | None
    """1 to 100; null = 25."""
    required: bool | None
    keep_versions: bool | None


class OptionIn(_Args):
    id: str
    label: str


class NewField(_Args):
    key: str
    """Lowercase letters, digits and `_`, 2 to 41 characters; it never changes."""
    type: FieldType
    """From list_field_types; a document field comes from add_document_slot."""
    label: str
    required: bool | None
    options: list[OptionIn] | None
    """For select and multi_select; null = none."""


class FieldChanges(_Args):
    key: str
    """The field to change; its key itself never changes."""
    label: str | None
    options: list[OptionIn] | None
    required: bool | None


class FieldRef(_Args):
    key: str


class CellIn(_Args):
    field: str
    span: int | None
    """Columns out of 12; null = the whole row."""


class SectionIn(_Args):
    key: str
    title: str
    rows: list[list[CellIn]]
    """Rows of cells; a row's spans add up to 12 at most."""
    hidden_below_level: DeptLevel | None
    """Hide the section from levels below this one; null = shown to all."""


class FormLayout(_Args):
    form: str
    """Form key; a new key makes a new form (point a task at it with update_task)."""
    sections: list[SectionIn]


# --- where things are in the draft --------------------------------------------------------------


def _ptr(*parts: str | int) -> str:
    return JsonPointer.from_parts(parts).path


def _index(items: list[Doc], key: str, what: str) -> int:
    keys = [x["key"] for x in items]
    if key not in keys:
        raise NotFound(f"No {what} '{key}'")
    return keys.index(key)


def _flow(doc: Doc, flow: str | None) -> int:
    return 0 if flow is None else _index(doc["flows"], flow, "flow")


def _stage(doc: Doc, flow: str | None, stage: str) -> str:
    fi = _flow(doc, flow)
    return _ptr("flows", fi, "stages", _index(doc["flows"][fi]["stages"], stage, "stage"))


def _tasks(doc: Doc) -> Iterator[tuple[str, Doc]]:
    """(pointer, task) for every task of every flow."""
    for fi, flow in enumerate(doc["flows"]):
        for si, stage in enumerate(flow["stages"]):
            for i, item in enumerate(stage["tasks"]):
                at = _ptr("flows", fi, "stages", si, "tasks", i)
                if "parallel" in item:
                    yield from ((f"{at}/parallel/{j}", t) for j, t in enumerate(item["parallel"]))
                else:
                    yield at, item


def _task(doc: Doc, task_id: str) -> tuple[str, Doc]:
    found = next(((at, t) for at, t in _tasks(doc) if t["id"] == task_id), None)
    if found is None:
        raise NotFound(f"No task '{task_id}'")
    return found


def _department(doc: Doc, key: str) -> str:
    return _ptr("org", "departments", _index(doc["org"]["departments"], key, "department"))


def _field(doc: Doc, key: str) -> str:
    if key not in doc.get("fields", {}):
        raise NotFound(f"No field '{key}'")
    return _ptr("fields", key)


def _form(doc: Doc, key: str) -> Doc:
    if key not in doc.get("forms", {}):
        raise NotFound(f"No form '{key}'")
    return doc["forms"][key]


def _insert_at(doc: Doc, flow: str | None, stage: str, before: str | None) -> str:
    """Where a task goes: before `before`, a task of the stage, else at the stage's end."""
    at = _stage(doc, flow, stage)
    if before is None:
        return f"{at}/tasks/-"
    path, _ = _task(doc, before)
    if not path.startswith(f"{at}/tasks/"):
        raise NotFound(f"No task '{before}' in stage '{stage}'")
    return path


def _take(doc: Doc, task_id: str) -> Patch:
    """Takes a task out of its stage; a parallel group left with one task becomes that task."""
    path, _ = _task(doc, task_id)
    group, _, j = path.rpartition("/parallel/")
    siblings = JsonPointer(group).resolve(doc)["parallel"] if group else []
    if len(siblings) == 2:
        return [{"op": "replace", "path": group, "value": siblings[1 - int(j)]}]
    return [{"op": "remove", "path": path}]


def _new_field(doc: Doc, key: str, field: Doc) -> Patch:
    if key in doc.get("fields", {}):
        raise Conflict(f"Field '{key}' already exists (a key is never reused, even when hidden)")
    return [{"op": "add", "path": _ptr("fields", key), "value": field}]


def _levels(names: list[str]) -> list[Doc]:
    return [{"n": n, "name": name} for n, name in enumerate(names, 1)]


def _set(at: str, changes: BaseModel, exclude: set[str]) -> Patch:
    """`add` (sets a member, whether there or not) for each argument that isn't null."""
    values = changes.model_dump(exclude=exclude, exclude_none=True)
    return [{"op": "add", "path": f"{at}/{k}", "value": v} for k, v in values.items()]


# --- edits: draft -> patch (pure) ----------------------------------------------------------------


def load_template(doc: Doc, a: TemplateRef) -> Patch:
    found = {t.key: t.workflow for t in templates.load_templates()}
    if a.template not in found:
        raise NotFound(f"No template '{a.template}'")
    if doc not in found.values():
        raise Conflict("The draft has work in it: a template only replaces an unchanged template")
    return [{"op": "replace", "path": "", "value": found[a.template]}]


def add_department(doc: Doc, a: NewDepartment) -> Patch:
    value = {"key": a.key, "name": a.name, "levels": _levels(a.levels)}
    return [{"op": "add", "path": "/org/departments/-", "value": value}]


def update_department(doc: Doc, a: RenameDepartment) -> Patch:
    return [{"op": "replace", "path": f"{_department(doc, a.department)}/name", "value": a.name}]


def set_levels(doc: Doc, a: Levels) -> Patch:
    at = _department(doc, a.department)
    return [{"op": "replace", "path": f"{at}/levels", "value": _levels(a.levels)}]


def add_stage(doc: Doc, a: NewStage) -> Patch:
    value = a.model_dump(exclude={"flow"}, exclude_none=True)
    return [{"op": "add", "path": _ptr("flows", _flow(doc, a.flow), "stages", "-"), "value": value}]


def set_after(doc: Doc, a: StageAfter) -> Patch:
    return [{"op": "add", "path": f"{_stage(doc, a.flow, a.stage)}/after", "value": a.after}]


def add_task(doc: Doc, a: TaskIn) -> Patch:
    value = a.task.model_dump(exclude_none=True)
    return [{"op": "add", "path": _insert_at(doc, a.flow, a.stage, a.before), "value": value}]


def update_task(doc: Doc, a: TaskChanges) -> Patch:
    return _set(_task(doc, a.task_id)[0], a, {"task_id"})


def move_task(doc: Doc, a: TaskMove) -> Patch:
    _, task = _task(doc, a.task_id)
    out = _take(doc, a.task_id)
    at = _insert_at(jsonpatch.apply_patch(doc, out), a.flow, a.stage, a.before)
    return [*out, {"op": "add", "path": at, "value": task}]


def remove_task(doc: Doc, a: TaskRef) -> Patch:
    return _take(doc, a.task_id)


def set_outcomes(doc: Doc, a: Outcomes) -> Patch:
    value = a.model_dump(exclude_none=True)["outcomes"]
    return [{"op": "add", "path": f"{_task(doc, a.task_id)[0]}/outcomes", "value": value}]


def set_approval_chain(doc: Doc, a: ApprovalChain) -> Patch:
    at, task = _task(doc, a.task_id)
    if a.chain:
        value = a.model_dump(exclude={"task_id"}, exclude_none=True)
        return [{"op": "add", "path": f"{at}/approval", "value": value}]
    return [{"op": "remove", "path": f"{at}/approval"}] if "approval" in task else []


def add_document_slot(doc: Doc, a: DocumentSlot) -> Patch:
    _, task = _task(doc, a.task_id)
    if task.get("form") is None:
        raise UnprocessableContent(
            f"Task '{a.task_id}' has no form: arrange one, then set it with update_task"
        )
    rule = a.model_dump(include={"accept", "max_mb", "keep_versions"}, exclude_none=True)
    field = {"type": "document", "label": a.label, "required": bool(a.required), "document": rule}
    last = len(_form(doc, task["form"])["sections"]) - 1
    row = _ptr("forms", task["form"], "sections", last, "rows", "-")
    return [*_new_field(doc, a.key, field), {"op": "add", "path": row, "value": [{"field": a.key}]}]


def add_field(doc: Doc, a: NewField) -> Patch:
    return _new_field(doc, a.key, a.model_dump(exclude={"key"}, exclude_none=True))


def update_field(doc: Doc, a: FieldChanges) -> Patch:
    return _set(_field(doc, a.key), a, {"key"})


def remove_field(doc: Doc, a: FieldRef) -> Patch:
    """Hides it from new forms (S2-25): the key and stored values stay."""
    return [{"op": "add", "path": f"{_field(doc, a.key)}/hidden", "value": True}]


def arrange_form(doc: Doc, a: FormLayout) -> Patch:
    value = a.model_dump(include={"sections"}, exclude_none=True)
    return [{"op": "add", "path": _ptr("forms", a.form), "value": value}]


# --- reads ---------------------------------------------------------------------------------------


async def _draft(ctx: ClientContext) -> Doc:
    async with ctx.db.connect() as conn:
        doc = await releases.get_draft(conn)
    if doc is None:
        raise NotFound(drafts.NO_DRAFT)
    return doc


def overview(doc: Doc) -> Doc:
    """Departments, every flow's stages and tasks, and the fields: what get_task and get_form
    then open."""

    def brief(t: Doc) -> Doc:
        return {"id": t["id"], "name": t["name"]}

    return {
        "departments": [
            {"key": d["key"], "name": d["name"], "levels": [lv["name"] for lv in d["levels"]]}
            for d in doc["org"]["departments"]
        ],
        "flows": [
            {
                "key": f["key"],
                "name": f["name"],
                "stages": [
                    {
                        "key": s["key"],
                        "name": s["name"],
                        "department": s["department"],
                        "after": s.get("after", []),
                        "tasks": [
                            {"parallel": [brief(t) for t in i["parallel"]]}
                            if "parallel" in i
                            else brief(i)
                            for i in s["tasks"]
                        ],
                    }
                    for s in f["stages"]
                ],
            }
            for f in doc["flows"]
        ],
        "fields": {
            k: {x: f[x] for x in ("type", "label", "hidden") if x in f}
            for k, f in doc.get("fields", {}).items()
        },
        "forms": sorted(doc.get("forms", {})),
    }


def _ok(summary: str, data: Any) -> Result:
    return {"ok": True, "summary": summary, "data": data}


async def get_overview(ctx: ClientContext, _: NoArgs) -> Result:
    return _ok("The draft", overview(await _draft(ctx)))


async def get_task(ctx: ClientContext, a: TaskRef) -> Result:
    return _ok(f"Task {a.task_id}", _task(await _draft(ctx), a.task_id)[1])


async def get_form(ctx: ClientContext, a: FormRef) -> Result:
    doc = await _draft(ctx)
    form = _form(doc, a.form)
    used = {c["field"] for s in form["sections"] for row in s["rows"] for c in row}
    fields = {k: f for k, f in doc.get("fields", {}).items() if k in used}
    return _ok(f"Form {a.form}", {"form": form, "fields": fields})


async def list_field_types(_ctx: ClientContext, _: NoArgs) -> Result:
    types = [{"type": t, "v1_renders": t in V1_RENDERS} for t in get_args(FieldType)]
    return _ok("Field types", types)


async def list_templates(_ctx: ClientContext, _: NoArgs) -> Result:
    found = [{"key": t.key, "name": t.name} for t in templates.load_templates()]
    return _ok("Templates", found)


async def validate_draft(ctx: ClientContext, _: NoArgs) -> Result:
    errors = await drafts.validate_draft(ctx)
    if not errors:
        return {"ok": True, "summary": "The draft is valid"}
    found = [e.model_dump() for e in errors]
    return {"ok": False, "summary": f"{len(errors)} problems in the draft", "errors": found}


async def diff_vs_live(ctx: ClientContext, _: NoArgs) -> Result:
    found = await drafts.diff_vs_live(ctx)
    return _ok("The draft against the live release", found.model_dump())


# --- the registry --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    description: str
    args: type[BaseModel]
    edit: Callable[[Doc, Any], Patch] | None = None
    """Builds the patch from the locked draft."""
    summary: Callable[[Any], str] = lambda _: ""
    """The change chip."""
    read: Callable[[ClientContext, Any], Awaitable[Result]] | None = None


TOOLS: dict[str, Tool] = {
    # read
    "get_overview": Tool(
        "The draft at a glance: departments and levels, each flow's stages (department, `after`)"
        " with their tasks, every field's type and label, and the form keys.",
        NoArgs,
        read=get_overview,
    ),
    "get_task": Tool("One task as the draft holds it.", TaskRef, read=get_task),
    "get_form": Tool(
        "One form: its sections, rows and cells, and the definitions of the fields on it.",
        FormRef,
        read=get_form,
    ),
    "list_field_types": Tool(
        "The field types a field can have (the §09 catalogue; no other type exists), each with"
        " whether the v1 app draws it.",
        NoArgs,
        read=list_field_types,
    ),
    "list_templates": Tool(
        "The starter templates load_template takes.", NoArgs, read=list_templates
    ),
    "load_template": Tool(
        "Replace the draft with a starter template. Only while the draft is still an unchanged"
        " template.",
        TemplateRef,
        edit=load_template,
        summary=lambda a: f"Loaded template: {a.template}",
    ),
    # org
    "add_department": Tool(
        "Add a department with its level ladder.",
        NewDepartment,
        edit=add_department,
        summary=lambda a: f"Added department: {a.name}",
    ),
    "update_department": Tool(
        "Rename a department (its key stays).",
        RenameDepartment,
        edit=update_department,
        summary=lambda a: f"Renamed department {a.department}: {a.name}",
    ),
    "set_levels": Tool(
        "Replace a department's level ladder.",
        Levels,
        edit=set_levels,
        summary=lambda a: f"Levels of {a.department}: {', '.join(a.levels)}",
    ),
    # flow
    "add_stage": Tool(
        "Add a stage to a flow, with its first tasks.",
        NewStage,
        edit=add_stage,
        summary=lambda a: f"Added stage: {a.name}",
    ),
    "set_after": Tool(
        "Set which stages must finish before a stage starts.",
        StageAfter,
        edit=set_after,
        summary=lambda a: f"{a.stage} starts after: {', '.join(a.after) or 'the start'}",
    ),
    "add_task": Tool(
        "Add a task to a stage.",
        TaskIn,
        edit=add_task,
        summary=lambda a: f"Added task: {a.task.name}",
    ),
    "update_task": Tool(
        "Change a task's name, performer, form or shown fields; null leaves one as it is, and"
        " the id never changes.",
        TaskChanges,
        edit=update_task,
        summary=lambda a: f"Updated task: {a.task_id}",
    ),
    "move_task": Tool(
        "Move a task within its stage or to another one.",
        TaskMove,
        edit=move_task,
        summary=lambda a: f"Moved {a.task_id} to {a.stage}",
    ),
    "remove_task": Tool(
        "Remove a task from its stage.",
        TaskRef,
        edit=remove_task,
        summary=lambda a: f"Removed task: {a.task_id}",
    ),
    "set_outcomes": Tool(
        "Set a task's decision buttons and where each one leads.",
        Outcomes,
        edit=set_outcomes,
        summary=lambda a: (
            f"Outcomes of {a.task_id}: " + (", ".join(o.label for o in a.outcomes) or "submit")
        ),
    ),
    "set_approval_chain": Tool(
        "Set the levels that approve a task, in order; each can approve, reject or send back.",
        ApprovalChain,
        edit=set_approval_chain,
        summary=lambda a: (
            f"Approval of {a.task_id}: "
            + (" → ".join(f"{c.department} L{c.level}" for c in a.chain) or "none")
        ),
    ),
    "add_document_slot": Tool(
        "Ask for a file at a task: a new document field with its rule, added as a row at the end"
        " of the task's form.",
        DocumentSlot,
        edit=add_document_slot,
        summary=lambda a: f"Added: {a.label}",
    ),
    # forms
    "add_field": Tool(
        "Define a new field; place it on a form with arrange_form.",
        NewField,
        edit=add_field,
        summary=lambda a: f"Added: {a.label}",
    ),
    "update_field": Tool(
        "Change a field's label, options or whether it is required; null leaves one as it is."
        " Its key and type never change.",
        FieldChanges,
        edit=update_field,
        summary=lambda a: f"Updated field: {a.key}",
    ),
    "remove_field": Tool(
        "Hide a field from new forms; its key and the values already stored stay.",
        FieldRef,
        edit=remove_field,
        summary=lambda a: f"Hid field: {a.key}",
    ),
    "arrange_form": Tool(
        "Lay out a form on the 12-column grid: its sections, rows and cells, replacing the old"
        " layout.",
        FormLayout,
        edit=arrange_form,
        summary=lambda a: f"Arranged form: {a.form}",
    ),
    # checks
    "validate_draft": Tool(
        "Every problem in the draft, each with its path; ok when there is none.",
        NoArgs,
        read=validate_draft,
    ),
    "diff_vs_live": Tool(
        "What publishing the draft would change against the live release.",
        NoArgs,
        read=diff_vs_live,
    ),
}


def definitions() -> list[Doc]:
    """The tools as the Messages API takes them."""
    return [
        {
            "name": name,
            "description": t.description,
            "input_schema": t.args.model_json_schema(),
            "strict": True,
        }
        for name, t in TOOLS.items()
    ]


async def call(
    ctx: ClientContext,
    author: UUID,
    name: str,
    args: Any,
    conversation_id: UUID | None = None,
) -> Result:
    """Run one tool call for `ctx`'s client; every refusal comes back as `ok: false`."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "summary": f"No tool '{name}'"}
    try:
        a = tool.args.model_validate(args)
        if tool.read is not None:
            return await tool.read(ctx, a)
        change = await drafts.apply_change(
            ctx, author, name, lambda doc: tool.edit(doc, a), tool.summary(a), conversation_id
        )
    except ValidationError as e:
        errors = [{"path": loc_to_path(x["loc"]), "message": x["msg"]} for x in e.errors()]
        return {"ok": False, "summary": "The arguments don't fit the tool", "errors": errors}
    except ApiError as e:
        found = {"errors": [x.model_dump() for x in e.errors]} if e.errors else {}
        return {"ok": False, "summary": e.detail or "Refused", **found}
    return {"ok": True, "summary": tool.summary(a) if change else "Nothing changed"}
