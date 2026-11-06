"""The agent's tools v1 (#36, S2-04a; blueprint §10): read, org, flow, forms and checks; and
the UI tools (#39, S2-04b): pages, their blocks and the menu, which the draft's validation checks
against the layout and block manifests (catalogue.json, #37). The look (theme preset, accent) is
picked in the owner console, so no tool sets it (D19).

Each tool is a Pydantic model of its arguments, sent to Claude as a JSON schema with no extra
properties and every property required (null only where it means "none"), and a handler. The
edits' schemas are strict, but for the two taking a block's free-form props, which a strict schema
can't hold; the reads' aren't, to stay inside the API's limits. An edit's handler
is a pure function from the draft to an RFC 6902 patch: `drafts.apply_change` builds it from the
locked draft, validates the result and logs it under the tool's name, with undo. An edit names the
`draft_sha256` its last read or edit returned and is refused when the draft has changed since, so
it never overwrites a person's edit it hasn't seen. No tool publishes, migrates, manages people or
takes a client: the caller passes the ClientContext of the console's client. `call` answers
`{ok, summary, errors?}`, plus `data` for a read and `draft_sha256`, cut to a size (`truncated`);
an edit's `summary` is its change chip, and a done edit adds its `change_id` (the log row).
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, get_args
from uuid import UUID

import jsonpatch
from jsonpointer import JsonPointer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from zm_api import drafts, templates
from zm_api.context import ClientContext
from zm_api.errors import ApiError, Conflict, NotFound, UnprocessableContent
from zm_schema import catalogue
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


# --- arguments: strict schemas, every value given (null only where it means "none") -------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class _Edit(_Args):
    draft_sha256: str = Field(exclude=True)
    """The `draft_sha256` your last read or edit returned; if the draft has changed since, the
    edit is refused and you read it again."""


class NoArgs(_Args):
    pass


class TaskRef(_Args):
    task_id: str


class TaskToRemove(_Edit):
    task_id: str


class FormRef(_Args):
    form: str
    """Form key, as a task's `form` names it."""


class TemplateRef(_Edit):
    template: str
    """Template key from list_templates."""


class NewDepartment(_Edit):
    key: str
    """Short key, e.g. `legal`; it names the Operaton groups (`legal__L1`), so it never changes."""
    name: str
    levels: list[str]
    """Level names, L1 (most junior) first."""


class RenameDepartment(_Edit):
    department: str
    name: str


class Levels(_Edit):
    department: str
    levels: list[str]
    """Every level's name, L1 (most junior) first; replaces the ladder."""


class LevelPerformer(_Args):
    department: str
    level: int
    or_above: bool
    """Higher levels of the department may do it too."""


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
    show: list[str]
    """Fields filled by earlier steps, shown read-only; empty = none."""


class NewStage(_Edit):
    flow: str
    """Flow key, from get_overview."""
    key: str
    name: str
    department: str
    after: list[str]
    """Stage keys that must finish first; empty = it starts with the case."""
    tasks: list[NewTask]
    """At least one, in order."""


class StageAfter(_Edit):
    flow: str
    """Flow key, from get_overview."""
    stage: str
    after: list[str]
    """Stage keys that must all finish first (several = they run in parallel); empty = none."""


class TaskIn(_Edit):
    flow: str
    """Flow key, from get_overview."""
    stage: str
    task: NewTask
    before: str | None
    """Insert before this task of the stage; null = at its end."""


class TaskChanges(_Edit):
    task_id: str
    name: str
    performer: Performer
    form: str | None
    """Null = no form."""
    show: list[str]
    """Empty = none."""


class TaskMove(_Edit):
    task_id: str
    flow: str
    """Flow key, from get_overview."""
    stage: str
    before: str | None
    """Put it before this task of the stage; null = at its end."""


class OutcomeIn(_Args):
    id: str
    label: str
    to: str | None
    """A task of the same stage, `@close` (ends the case) or `@end`; null = the next step."""
    comment_required: bool


class Outcomes(_Edit):
    task_id: str
    outcomes: list[OutcomeIn]
    """The task's decision buttons, replacing any it has; empty = a plain submit."""


class DeptLevel(_Args):
    department: str
    level: int


class ApprovalChain(_Edit):
    task_id: str
    chain: list[DeptLevel]
    """Approvers in order; empty removes the task's approval."""
    send_back_to: str | None
    """A task of the same stage an approver can send it back to; null = none."""
    allow_reject: bool
    """Usually true."""


class DocumentSlot(_Edit):
    task_id: str
    """The task whose form gets the slot (the task needs a form)."""
    key: str
    """New field key: lowercase letters, digits and `_`, 2 to 41 characters."""
    label: str
    accept: list[str]
    """File extensions, e.g. `["pdf", "jpg"]`."""
    max_mb: int
    """1 to 100; usually 25."""
    required: bool
    keep_versions: bool


class OptionIn(_Args):
    id: str
    label: str


class NewField(_Edit):
    key: str
    """Lowercase letters, digits and `_`, 2 to 41 characters; it never changes."""
    type: FieldType
    """From list_field_types; a document field comes from add_document_slot."""
    label: str
    required: bool
    options: list[OptionIn]
    """For select and multi_select; empty for the other types."""


class FieldChanges(_Edit):
    key: str
    """The field to change; its key itself never changes."""
    label: str
    options: list[OptionIn]
    """Empty = none."""
    required: bool


class FieldRef(_Edit):
    key: str


class CellIn(_Args):
    field: str
    span: int
    """Columns out of 12; 12 = the whole row."""


class SectionIn(_Args):
    key: str
    title: str
    rows: list[list[CellIn]]
    """Rows of cells; a row's spans add up to 12 at most."""
    hidden_below_level: DeptLevel | None
    """Hide the section from levels below this one; null = shown to all."""


class FormLayout(_Edit):
    form: str
    """Form key; a new key makes a new form (point a task at it with update_task)."""
    sections: list[SectionIn]


class BlockName(_Args):
    block: str
    """From list_blocks."""


class PageRef(_Args):
    page: str
    """Page key, from get_overview."""


class PageIn(_Edit):
    key: str
    """A letter, then letters, digits or `_`; an existing page's key changes that page."""
    layout: str
    """From list_layouts."""
    department: str | None
    """The department the page is for; null = none."""
    flow: str | None
    """Flow key, from get_overview; a `case` page needs one. Null = none."""


class NewBlock(_Edit):
    page: str
    region: str
    """A region of the page's layout that takes the block (list_layouts)."""
    block: str
    """From list_blocks."""
    props: dict[str, Any]
    """As get_block_manifest describes them; {} = the block's defaults."""
    index: int | None
    """Its place in the region, 0 first; null = at the end."""


class BlockAt(_Edit):
    page: str
    region: str
    index: int
    """The block's place in the region, 0 first, as get_page lists them."""


class BlockProps(BlockAt):
    props: dict[str, Any]
    """Every prop, as get_block_manifest describes them; replaces the old ones."""


class NavItem(_Args):
    page: str
    """Page key, from get_overview."""
    label: str
    visible_to: list[str]
    """Department keys, or `["*"]` for everyone."""


class Nav(_Edit):
    items: list[NavItem]
    """The whole menu, in order."""


# --- where things are in the draft --------------------------------------------------------------


def _ptr(*parts: str | int) -> str:
    return JsonPointer.from_parts(parts).path


def _index(items: list[Doc], key: str, what: str) -> int:
    keys = [x["key"] for x in items]
    if key not in keys:
        raise NotFound(f"No {what} '{key}'")
    return keys.index(key)


def _flow(doc: Doc, flow: str) -> int:
    return _index(doc["flows"], flow, "flow")


def _stage(doc: Doc, flow: str, stage: str) -> str:
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


def _page(doc: Doc, key: str) -> tuple[int, Doc]:
    pages = doc.get("apps", {}).get("pages", [])
    i = _index(pages, key, "page")
    return i, pages[i]


def _block(doc: Doc, a: BlockAt) -> str:
    i, page = _page(doc, a.page)
    if not 0 <= a.index < len(page.get("regions", {}).get(a.region, [])):
        raise NotFound(f"No block {a.index} in region '{a.region}' of page '{a.page}'")
    return _ptr("apps", "pages", i, "regions", a.region, a.index)


def _insert_at(doc: Doc, flow: str, stage: str, before: str | None) -> str:
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


def _set(at: str, current: Doc, values: Doc) -> Patch:
    """`add` (sets a member, whether there or not) for each value; a null or empty one removes
    the member, if it is there."""
    return [
        {"op": "add", "path": f"{at}/{k}", "value": v}
        if v not in (None, [])
        else {"op": "remove", "path": f"{at}/{k}"}
        for k, v in values.items()
        if v not in (None, []) or k in current
    ]


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
    at, task = _task(doc, a.task_id)
    return _set(at, task, a.model_dump(exclude={"task_id"}))


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
    rule = a.model_dump(include={"accept", "max_mb", "keep_versions"})
    field = {"type": "document", "label": a.label, "required": a.required, "document": rule}
    last = len(_form(doc, task["form"])["sections"]) - 1
    row = _ptr("forms", task["form"], "sections", last, "rows", "-")
    return [*_new_field(doc, a.key, field), {"op": "add", "path": row, "value": [{"field": a.key}]}]


def add_field(doc: Doc, a: NewField) -> Patch:
    field = a.model_dump(exclude={"key"})
    if not a.options:
        del field["options"]
    return _new_field(doc, a.key, field)


def update_field(doc: Doc, a: FieldChanges) -> Patch:
    return _set(_field(doc, a.key), doc["fields"][a.key], a.model_dump(exclude={"key"}))


def remove_field(doc: Doc, a: FieldRef) -> Patch:
    """Hides it from new forms (S2-25): the key and stored values stay."""
    return [{"op": "add", "path": f"{_field(doc, a.key)}/hidden", "value": True}]


def arrange_form(doc: Doc, a: FormLayout) -> Patch:
    value = a.model_dump(include={"sections"}, exclude_none=True)
    return [{"op": "add", "path": _ptr("forms", a.form), "value": value}]


# ponytail: these take a draft's `apps.pages`/`nav` and a page's `regions` as there (every
# template has them); without them a change is refused as "the patch doesn't apply".
def set_page(doc: Doc, a: PageIn) -> Patch:
    if a.key not in [p["key"] for p in doc.get("apps", {}).get("pages", [])]:
        page = {**a.model_dump(exclude_none=True), "regions": {}}
        return [{"op": "add", "path": "/apps/pages/-", "value": page}]
    i, page = _page(doc, a.key)
    return _set(_ptr("apps", "pages", i), page, a.model_dump(exclude={"key"}))


def place_block(doc: Doc, a: NewBlock) -> Patch:
    i, page = _page(doc, a.page)
    placed = [*page.get("regions", {}).get(a.region, [])]
    at = len(placed) if a.index is None else a.index
    if not 0 <= at <= len(placed):
        raise NotFound(f"No position {a.index} in region '{a.region}' of page '{a.page}'")
    placed.insert(at, {"block": a.block, "props": a.props})
    return [{"op": "add", "path": _ptr("apps", "pages", i, "regions", a.region), "value": placed}]


def update_block(doc: Doc, a: BlockProps) -> Patch:
    return [{"op": "add", "path": f"{_block(doc, a)}/props", "value": a.props}]


def remove_block(doc: Doc, a: BlockAt) -> Patch:
    return [{"op": "remove", "path": _block(doc, a)}]


def set_nav(doc: Doc, a: Nav) -> Patch:
    return [{"op": "add", "path": "/apps/nav", "value": a.model_dump()["items"]}]


# --- reads ---------------------------------------------------------------------------------------


async def _draft(ctx: ClientContext) -> tuple[Doc, str]:
    """The draft and the sha256 an edit names to say it read this one."""
    async with ctx.db.connect() as conn:
        return await drafts.read(conn)


def overview(doc: Doc) -> Doc:
    """The pages without their blocks and the menu, first so that a cut overview still names
    them; departments, every flow's stages and tasks, and the fields. get_page, get_task and
    get_form then open them."""

    def brief(t: Doc) -> Doc:
        return {"id": t["id"], "name": t["name"]}

    return {
        "pages": [
            {k: v for k, v in p.items() if k != "regions"}
            for p in doc.get("apps", {}).get("pages", [])
        ],
        "nav": doc.get("apps", {}).get("nav", []),
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


def _ok(summary: str, data: Any, **more: Any) -> Result:
    return {"ok": True, "summary": summary, "data": data, **more}


async def get_overview(ctx: ClientContext, _: NoArgs) -> Result:
    doc, sha = await _draft(ctx)
    return _ok("The draft", overview(doc), draft_sha256=sha)


async def get_task(ctx: ClientContext, a: TaskRef) -> Result:
    doc, sha = await _draft(ctx)
    return _ok(f"Task {a.task_id}", _task(doc, a.task_id)[1], draft_sha256=sha)


async def get_form(ctx: ClientContext, a: FormRef) -> Result:
    doc, sha = await _draft(ctx)
    form = _form(doc, a.form)
    used = {c["field"] for s in form["sections"] for row in s["rows"] for c in row}
    fields = {k: f for k, f in doc.get("fields", {}).items() if k in used}
    return _ok(f"Form {a.form}", {"form": form, "fields": fields}, draft_sha256=sha)


async def get_page(ctx: ClientContext, a: PageRef) -> Result:
    doc, sha = await _draft(ctx)
    return _ok(f"Page {a.page}", _page(doc, a.page)[1], draft_sha256=sha)


async def list_field_types(_ctx: ClientContext, _: NoArgs) -> Result:
    types = [{"type": t, "v1_renders": t in V1_RENDERS} for t in get_args(FieldType)]
    return _ok("Field types", types)


async def list_templates(_ctx: ClientContext, _: NoArgs) -> Result:
    found = [{"key": t.key, "name": t.name} for t in templates.load_templates()]
    return _ok("Templates", found)


def _newest(versions: Doc) -> Doc:
    """A block's newest manifest: a page names no version, and the shell draws the newest."""
    return max(versions.values(), key=lambda m: m["version"])


def _goes_in(block: str) -> Doc:
    """`{layout: [region]}`: where a page takes the block, by each layout's `allowed_blocks`, as
    validation reads them (a manifest's own `regions` name no layout, and only its version's)."""
    return {
        name: found
        for name, layout in catalogue.load()["layouts"].items()
        if (found := [r["key"] for r in layout["regions"] if block in r["allowed_blocks"]])
    }


async def list_layouts(_ctx: ClientContext, _: NoArgs) -> Result:
    return _ok("Layouts", list(catalogue.load()["layouts"].values()))


async def list_blocks(_ctx: ClientContext, _: NoArgs) -> Result:
    found = [
        {
            "name": name,
            "versions": sorted(m["version"] for m in versions.values()),
            "description": _newest(versions)["description"],
            "goes_in": _goes_in(name),
        }
        for name, versions in catalogue.load()["blocks"].items()
    ]
    return _ok("Blocks", found)


async def get_block_manifest(_ctx: ClientContext, a: BlockName) -> Result:
    versions = catalogue.load()["blocks"].get(a.block)
    if not versions:
        raise NotFound(f"No block '{a.block}'")
    manifest = {k: v for k, v in _newest(versions).items() if k != "regions"}
    return _ok(f"Block {a.block}", {**manifest, "goes_in": _goes_in(a.block)})


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
    strict: bool = True
    """False for an edit taking a block's props: free-form, which a strict schema can't hold, so
    the draft's validation checks them against the block's manifest."""


TOOLS: dict[str, Tool] = {
    # read
    "get_overview": Tool(
        "The draft at a glance: the pages (layout, department, flow; get_page has their blocks),"
        " the menu, departments and levels, each flow's stages (department, `after`) with their"
        " tasks, every field's type and label, and the form keys.",
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
        "Set a task's name, performer, form and shown fields, all of them (start from get_task):"
        " a null form or empty show clears it. The id never changes.",
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
        TaskToRemove,
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
        "Set a field's label, options and whether it is required, all of them (start from"
        " get_form): empty options clears them. Its key and type never change.",
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
    # pages, blocks and the menu, from the layout and block manifests
    "list_layouts": Tool(
        "The page layouts: their regions, the blocks each region takes (and at most how many),"
        " and what a page of the layout needs (`requires`).",
        NoArgs,
        read=list_layouts,
    ),
    "list_blocks": Tool(
        "The blocks a page can show: what each is and where it goes (`goes_in`: each layout's"
        " regions that take it); get_block_manifest gives its props.",
        NoArgs,
        read=list_blocks,
    ),
    "get_block_manifest": Tool(
        "One block's manifest: what it shows, where it goes (`goes_in`, as list_blocks) and the"
        " JSON Schema of its props.",
        BlockName,
        read=get_block_manifest,
    ),
    "get_page": Tool(
        "One page as the draft holds it: its layout, department and flow, and each region's"
        " blocks in order (0 first) with their props.",
        PageRef,
        read=get_page,
    ),
    "set_page": Tool(
        "Add a page, or set a page's layout, department and flow, all of them (start from"
        " get_overview): a null one clears it. Its blocks stay.",
        PageIn,
        edit=set_page,
        summary=lambda a: f"Page {a.key}: {a.layout}",
    ),
    "place_block": Tool(
        "Put a block, with its props, in a region of a page.",
        NewBlock,
        edit=place_block,
        summary=lambda a: f"Placed {a.block} on {a.page}",
        strict=False,
    ),
    "update_block": Tool(
        "Set a placed block's props, all of them (start from get_page).",
        BlockProps,
        edit=update_block,
        summary=lambda a: f"Updated a block on {a.page}",
        strict=False,
    ),
    "remove_block": Tool(
        "Take a block off a page.",
        BlockAt,
        edit=remove_block,
        summary=lambda a: f"Removed a block from {a.page}",
    ),
    "set_nav": Tool(
        "Set the app's menu, replacing it: the pages it opens, in order, and who sees each.",
        Nav,
        edit=set_nav,
        summary=lambda a: "Menu: " + (", ".join(i.label for i in a.items) or "empty"),
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
    """The tools as the Messages API takes them. Only the edits are strict (but those taking a
    block's props): Anthropic caps strict tools and their union and optional parameters per
    request (test_tools checks the totals), and every call is validated here anyway."""
    return [
        {
            "name": name,
            "description": t.description,
            "input_schema": t.args.model_json_schema(),
            "strict": t.edit is not None and t.strict,
        }
        for name, t in TOOLS.items()
    ]


MAX_DATA = 40_000
"""Characters of JSON a result's `data` keeps (about 10k tokens; Matrix Retail's overview is a
quarter of it); past it, `data` is that much of its JSON text."""
MAX_ERRORS = 50


def _fit(result: Result) -> Result:
    """`result` cut to MAX_DATA and MAX_ERRORS, with `truncated` when it was."""
    if len(result.get("errors", [])) > MAX_ERRORS:
        result = {**result, "errors": result["errors"][:MAX_ERRORS], "truncated": True}
    if "data" in result and len(found := json.dumps(result["data"])) > MAX_DATA:
        # no sha for a read the agent couldn't see whole: an edit needs one of a full read
        result = {k: v for k, v in result.items() if k != "draft_sha256"}
        result = {**result, "data": found[:MAX_DATA], "truncated": True}
        cut = "(cut: read the parts with get_task, get_form or get_page)"
        result["summary"] = f"{result.get('summary', '')} {cut}".strip()
    return result


async def call(
    ctx: ClientContext,
    author: UUID,
    name: str,
    args: Any,
    conversation_id: UUID | None = None,
) -> Result:
    """Run one tool call for `ctx`'s client; every refusal comes back as `ok: false`. A read of
    the draft and a done edit answer the draft's `draft_sha256`, which the next edit names."""
    return _fit(await _call(ctx, author, name, args, conversation_id))


async def _call(
    ctx: ClientContext, author: UUID, name: str, args: Any, conversation_id: UUID | None
) -> Result:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "summary": f"No tool '{name}'"}
    try:
        a = tool.args.model_validate(args)
        if tool.read is not None:
            return await tool.read(ctx, a)
        change = await drafts.apply_change(
            ctx,
            author,
            name,
            lambda doc: tool.edit(doc, a),
            tool.summary(a),
            conversation_id,
            draft_sha256=a.draft_sha256,
        )
    except ValidationError as e:
        errors = [{"path": loc_to_path(x["loc"]), "message": x["msg"]} for x in e.errors()]
        return {"ok": False, "summary": "The arguments don't fit the tool", "errors": errors}
    except ApiError as e:
        found = {"errors": [x.model_dump() for x in e.errors]} if e.errors else {}
        return {"ok": False, "summary": e.detail or "Refused", **found}
    if change is None:  # the draft is still the one the agent read
        return {"ok": True, "summary": "Nothing changed", "draft_sha256": a.draft_sha256}
    return {
        "ok": True,
        "summary": tool.summary(a),
        "draft_sha256": change.after_sha256,
        "change_id": change.id,
    }
