"""`workflow.json` schema v1: the Pydantic models are the source of truth (#16, S1-07).

One document per client holds the org (departments and their levels), any number of flows, the
shared fields and forms, the UI arrangement (`apps`) and KPIs. The exported JSON Schema
(`workflow.schema.json`) and the TS types (`ts/workflow.d.ts`) are generated from these models.
Cross-reference rules live in `semantics.py`; a constructed `WorkflowV1` has passed both.
"""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    ValidationInfo,
    model_validator,
)

# Ids that end up in BPMN element ids and form keys (`zm:<task id>`): an XML-name-safe subset.
Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
LevelNo = Annotated[int, Field(ge=1)]

# Blueprint §09 field catalogue. v1 renders a subset (S1-15); the rest are valid in documents.
FieldType = Literal[
    "text",
    "textarea",
    "number",
    "currency",
    "percent",
    "date",
    "date_range",
    "select",
    "multi_select",
    "yes_no",
    "rating",
    "user",
    "address",
    "checklist",
    "line_items",
    "computed",
    "document",
    "info",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


# ── org ─────────────────────────────────────────────────────────────────────


class Level(_Model):
    n: LevelNo
    name: Text


class Department(_Model):
    key: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    """Department key; Operaton candidate groups are derived as `<key>__L<n>`."""
    name: Text
    levels: list[Level] = Field(min_length=1)
    """Contiguous from 1 (L1 = most junior)."""


class Org(_Model):
    departments: list[Department] = Field(min_length=1)


class DeptLevel(_Model):
    department: str
    level: LevelNo


# ── performers and rules ────────────────────────────────────────────────────


class LevelPerformer(_Model):
    department: str
    level: LevelNo
    or_above: bool = False
    """Higher levels of the same department may also do the task."""


class InitiatorPerformer(_Model):
    initiator: Literal[True]
    """The person who started the case."""


class FieldPerformer(_Model):
    from_field: str
    """A `user`-type field filled earlier in the case."""


def _performer_tag(v: Any) -> str | None:
    keys = (
        set(v)
        if isinstance(v, dict)
        else set(type(v).model_fields)
        if isinstance(v, BaseModel)
        else set()
    )
    if "initiator" in keys:
        return "InitiatorPerformer"
    if "from_field" in keys:
        return "FieldPerformer"
    if "department" in keys:
        return "LevelPerformer"
    return None


Performer = Annotated[
    Annotated[LevelPerformer, Tag("LevelPerformer")]
    | Annotated[InitiatorPerformer, Tag("InitiatorPerformer")]
    | Annotated[FieldPerformer, Tag("FieldPerformer")],
    Discriminator(
        _performer_tag,
        custom_error_type="performer",
        custom_error_message=(
            "performer must be {department, level[, or_above]}, {initiator: true} "
            "or {from_field: <user field>}"
        ),
    ),
]


class InitiatorAtLeastRule(_Model):
    initiator_at_least: DeptLevel


Rule = Annotated[str, StringConstraints(min_length=1)] | InitiatorAtLeastRule
"""A JUEL expression over field keys (as in the prototype), or a check on the initiator's level."""


# ── flows ───────────────────────────────────────────────────────────────────


class Outcome(_Model):
    id: Id
    label: Text
    to: str | None = None
    """A task id in the same stage, `@close` (close the case) or `@end`; default: next step."""
    comment_required: bool = False


class Approval(_Model):
    chain: list[DeptLevel] = Field(min_length=1)
    """Approvers in order; each can approve, reject (if allowed) or send back."""
    send_back_to: str | None = None
    allow_reject: bool = True


class Route(_Model):
    when: Rule
    to: str
    label: Text


class Task(_Model):
    id: Id
    name: Text
    performer: Performer
    form: str | None = None
    show: list[str] = []
    """Read-only fields from earlier steps shown on this task."""
    outcomes: list[Outcome] = []
    approval: Approval | None = None
    skip_if: Rule | None = None
    route: list[Route] = []


class ParallelGroup(_Model):
    parallel: list[Task] = Field(min_length=2)


def _stage_item_tag(v: Any) -> str:
    if isinstance(v, dict):
        return "ParallelGroup" if "parallel" in v else "Task"
    return type(v).__name__


StageItem = Annotated[
    Annotated[Task, Tag("Task")] | Annotated[ParallelGroup, Tag("ParallelGroup")],
    Discriminator(_stage_item_tag),
]


class Stage(_Model):
    key: Id
    name: Text
    department: str
    after: list[str] = []
    """Stage keys of the same flow that must finish first; several = parallel join."""
    tasks: list[StageItem] = Field(min_length=1)


class CaseCode(_Model):
    pattern: Annotated[str, StringConstraints(pattern=r"\{seq:[1-9]\}")]
    """e.g. `MX-{seq:4}`; must contain `{seq:N}`."""


class Start(_Model):
    performers: list[Performer] = Field(min_length=1)
    form: str


class Flow(_Model):
    key: Id
    name: Text
    case_label: Text
    """What one case is called, e.g. "Site"."""
    case_code: CaseCode
    start: Start
    stages: list[Stage] = Field(min_length=1)


# ── fields and forms ────────────────────────────────────────────────────────


class Option(_Model):
    id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    label: Text


class DocumentRule(_Model):
    accept: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)
    """Allowed file extensions, e.g. `["pdf", "jpg"]`."""
    max_mb: Annotated[int, Field(ge=1, le=100)] = 25
    keep_versions: bool = False


class FieldDef(_Model):
    type: FieldType
    label: Text
    required: bool = False
    options: list[Option] | None = None
    document: DocumentRule | None = None
    since: Annotated[int, Field(ge=1)] | None = None
    """Release version that added the field (additive versioning, S2-25)."""
    hidden: bool = False
    """Hidden from new forms; stored values are kept."""


class Cell(_Model):
    field: str
    span: Annotated[int, Field(ge=1, le=12)] = 12


class Section(_Model):
    key: Id
    title: Text
    hidden_below_level: DeptLevel | None = None
    rows: list[list[Cell]] = Field(min_length=1)


class Form(_Model):
    sections: list[Section] = Field(min_length=1)


# ── apps (UI arrangement) and KPIs ──────────────────────────────────────────


class BlockRef(_Model):
    block: Annotated[str, StringConstraints(min_length=1)]
    props: dict[str, Any] = {}


class NavItem(_Model):
    page: str
    label: Text
    visible_to: list[str] = Field(min_length=1)
    """Department keys, or `"*"` for everyone."""


class Page(_Model):
    key: Id
    layout: Annotated[str, StringConstraints(min_length=1)]
    department: str | None = None
    flow: str | None = None
    """Required for the `case` layout."""
    regions: dict[str, list[BlockRef]] = {}


class Apps(_Model):
    theme: Annotated[str, StringConstraints(min_length=1)] = "quartz"
    """Theme preset key (D19)."""
    accent: str | None = None
    nav: list[NavItem] = []
    pages: list[Page] = []


class Kpi(_Model):
    key: Id
    label: Text
    type: Literal["duration", "count"]
    from_: str | None = Field(default=None, alias="from")
    """Stage key the measurement starts at."""
    to: str | None = None

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True, populate_by_name=True)


# ── document ────────────────────────────────────────────────────────────────


class WorkflowV1(_Model):
    """One client's `workflow.json`, schema version 1."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True, title="WorkflowV1")

    schema_version: Literal[1]
    org: Org
    flows: list[Flow] = Field(min_length=1)
    fields: dict[str, FieldDef] = {}
    forms: dict[str, Form] = {}
    apps: Apps = Apps()
    kpis: list[Kpi] = []

    @model_validator(mode="after")
    def _semantics(self, info: ValidationInfo) -> "WorkflowV1":
        if isinstance(info.context, dict) and info.context.get("structural_only"):
            return self
        from zm_schema.semantics import semantic_errors

        errors = semantic_errors(self)
        if errors:
            raise ValueError("; ".join(f"{e.path}: {e.message}" for e in errors))
        return self
